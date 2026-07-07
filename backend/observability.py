from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import socket
import threading
import time
from traceback import format_exception
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .discord_notifier import notify_discord


ROOT = Path(__file__).resolve().parents[1]
STARTED_AT = time.time()
logger = logging.getLogger(__name__)


@dataclass
class RuntimeStats:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    running_requests: int = 0
    last_request: dict = field(default_factory=dict)
    last_success: dict = field(default_factory=dict)
    last_failure: dict = field(default_factory=dict)
    last_health: dict = field(default_factory=dict)


_stats = RuntimeStats()
_lock = threading.Lock()
_monitor_started = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def record_process_started(request_id: str, meta: dict) -> None:
    event = {
        "request_id": request_id,
        "started_at": now_iso(),
        "file": meta.get("file", ""),
        "size_bytes": meta.get("size_bytes", 0),
        "mode": meta.get("mode", ""),
        "page_mode": meta.get("page_mode", "all"),
        "client": meta.get("client", ""),
    }
    with _lock:
        _stats.total_requests += 1
        _stats.running_requests += 1
        _stats.last_request = event

    if env_bool("DISCORD_PROCESS_NOTIFICATIONS", True):
        notify_discord(
            "Processing started",
            f"{event['file']} started in mode {event['mode'] or 'default'}.",
            "INFO",
            {
                "Request": request_id,
                "Client": event["client"],
                "Page scope": event["page_mode"],
                "Size": f"{event['size_bytes']} bytes",
            },
            dedupe_key=f"process-start:{request_id}",
            force=True,
        )


def record_process_finished(request_id: str, meta: dict, result: dict, duration_seconds: float) -> None:
    summary = result.get("summary", {}) if isinstance(result, dict) else {}
    event = {
        "request_id": request_id,
        "finished_at": now_iso(),
        "file": meta.get("file", ""),
        "duration_seconds": round(duration_seconds, 2),
        "rows": len(result.get("rows") or []) if isinstance(result, dict) else 0,
        "pages": summary.get("pages", 0),
        "unique_skus": summary.get("uniqueSkus", 0),
        "links": summary.get("linkedAnnotations", 0),
        "blocked": summary.get("blockedLookups", 0),
        "price_different": summary.get("priceDifferent", 0),
        "excel_different": summary.get("excelDifferent", 0),
        "triple_different": summary.get("tripleDifferent", 0),
        "mode": summary.get("mode", meta.get("mode", "")),
    }
    with _lock:
        _stats.running_requests = max(0, _stats.running_requests - 1)
        _stats.successful_requests += 1
        _stats.last_success = event

    slow_seconds = env_int("BROCHURE_SLOW_SECONDS", 1800, minimum=1)
    level = "WARNING" if duration_seconds >= slow_seconds else "INFO"
    title = "Slow processing finished" if level == "WARNING" else "Processing finished"
    if env_bool("DISCORD_PROCESS_NOTIFICATIONS", True) or level == "WARNING":
        notify_discord(
            title,
            f"{event['file']} finished in {event['duration_seconds']} seconds.",
            level,
            {
                "Request": request_id,
                "Mode": event["mode"],
                "Pages": event["pages"],
                "Rows": event["rows"],
                "Unique SKUs": event["unique_skus"],
                "PDF links": event["links"],
                "Blocked lookups": event["blocked"],
                "Price differences": event["price_different"],
            },
            dedupe_key=f"process-finish:{request_id}",
            force=True,
        )


def record_process_failed(request_id: str, meta: dict, exc: Exception, duration_seconds: float) -> None:
    event = {
        "request_id": request_id,
        "failed_at": now_iso(),
        "file": meta.get("file", ""),
        "mode": meta.get("mode", ""),
        "duration_seconds": round(duration_seconds, 2),
        "error": str(exc),
    }
    with _lock:
        _stats.running_requests = max(0, _stats.running_requests - 1)
        _stats.failed_requests += 1
        _stats.last_failure = event

    notify_discord(
        "Processing failed",
        f"{event['file']} failed after {event['duration_seconds']} seconds.",
        "ERROR",
        {
            "Request": request_id,
            "Mode": event["mode"],
            "Error": event["error"][:1000],
            "Traceback": "".join(format_exception(type(exc), exc, exc.__traceback__))[-1000:],
        },
        dedupe_key=f"process-failed:{request_id}",
        force=True,
    )


def metrics_snapshot() -> dict:
    with _lock:
        stats = {
            "total_requests": _stats.total_requests,
            "successful_requests": _stats.successful_requests,
            "failed_requests": _stats.failed_requests,
            "running_requests": _stats.running_requests,
            "last_request": dict(_stats.last_request),
            "last_success": dict(_stats.last_success),
            "last_failure": dict(_stats.last_failure),
            "last_health": dict(_stats.last_health),
        }
    stats["uptime_seconds"] = round(time.time() - STARTED_AT, 2)
    stats["started_at"] = datetime.fromtimestamp(STARTED_AT, timezone.utc).isoformat()
    return stats


def local_health() -> dict:
    metrics = metrics_snapshot()
    return {
        "ok": True,
        "status": "ok",
        "service": "praktis-brochure-linker",
        "timestamp": now_iso(),
        "uptime_seconds": metrics["uptime_seconds"],
        "running_requests": metrics["running_requests"],
        "total_requests": metrics["total_requests"],
        "failed_requests": metrics["failed_requests"],
    }


def deep_health() -> dict:
    checks = {
        "service": {"ok": True, "message": "HTTP server is responding."},
        "disk": check_disk(),
        "internet": check_tcp_connect(
            os.environ.get("BROCHURE_HEALTH_INTERNET_HOST", "1.1.1.1"),
            env_int("BROCHURE_HEALTH_INTERNET_PORT", 443, minimum=1),
        ),
    }

    praktis_url = os.environ.get("BROCHURE_HEALTH_PRAKTIS_URL", "https://praktis.bg/").strip()
    if praktis_url:
        checks["praktis"] = check_http(praktis_url, accept_statuses={200, 301, 302, 403, 429})

    cdp_url = os.environ.get("PRAKTIS_CDP_URL", "").strip()
    if cdp_url:
        checks["chrome_cdp"] = check_http(cdp_url.rstrip("/") + "/json/version", accept_statuses={200})

    ok = all(check.get("ok") for check in checks.values())
    payload = {
        "ok": ok,
        "status": "ok" if ok else "degraded",
        "timestamp": now_iso(),
        "checks": checks,
        "metrics": metrics_snapshot(),
    }
    with _lock:
        _stats.last_health = payload
    return payload


def check_disk() -> dict:
    minimum_mb = env_int("BROCHURE_MIN_FREE_DISK_MB", 512, minimum=1)
    usage = shutil.disk_usage(ROOT)
    free_mb = round(usage.free / 1024 / 1024, 1)
    total_mb = round(usage.total / 1024 / 1024, 1)
    ok = free_mb >= minimum_mb
    return {
        "ok": ok,
        "free_mb": free_mb,
        "total_mb": total_mb,
        "message": "Disk space is OK." if ok else f"Only {free_mb} MB free.",
    }


def check_tcp_connect(host: str, port: int) -> dict:
    timeout = env_int("BROCHURE_HEALTH_TIMEOUT_SECONDS", 5, minimum=1)
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {
            "ok": True,
            "target": f"{host}:{port}",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "message": "TCP connection succeeded.",
        }
    except OSError as exc:
        return {
            "ok": False,
            "target": f"{host}:{port}",
            "message": str(exc)[:300],
        }


def check_http(url: str, accept_statuses: set[int]) -> dict:
    timeout = env_int("BROCHURE_HEALTH_TIMEOUT_SECONDS", 5, minimum=1)
    started = time.monotonic()
    request = Request(url, headers={"User-Agent": "praktis-brochure-linker-health/2.0"}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            ok = response.status in accept_statuses
            return {
                "ok": ok,
                "url": url,
                "status_code": response.status,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "message": "HTTP check succeeded." if ok else f"Unexpected HTTP {response.status}.",
            }
    except HTTPError as exc:
        ok = exc.code in accept_statuses
        return {
            "ok": ok,
            "url": url,
            "status_code": exc.code,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "message": "HTTP check succeeded." if ok else f"Unexpected HTTP {exc.code}.",
        }
    except Exception as exc:
        return {"ok": False, "url": url, "message": str(exc)[:300]}


def start_health_monitor() -> None:
    global _monitor_started
    if _monitor_started:
        return
    _monitor_started = True
    thread = threading.Thread(target=_health_monitor_loop, name="health-monitor", daemon=True)
    thread.start()


def _health_monitor_loop() -> None:
    interval = env_int("BROCHURE_HEALTH_INTERVAL_SECONDS", 60, minimum=10)
    reminder = env_int("BROCHURE_HEALTH_REMINDER_SECONDS", 1800, minimum=60)
    logger.info("Health monitor started: interval=%ss reminder=%ss", interval, reminder)
    if env_bool("DISCORD_STARTUP_NOTIFICATIONS", True):
        notify_discord(
            "Service started",
            "The brochure linker service has started.",
            "INFO",
            {"Health": "/api/health", "Deep health": "/api/health/deep"},
            dedupe_key="service-started",
            force=True,
        )

    previous_ok: bool | None = None
    last_alert = 0.0
    while True:
        time.sleep(interval)
        health = deep_health()
        ok = bool(health.get("ok"))
        now = time.monotonic()
        should_send = previous_ok is None or ok != previous_ok or (not ok and now - last_alert >= reminder)
        if should_send:
            title = "Health recovered" if ok and previous_ok is False else "Health problem detected"
            level = "INFO" if ok else "WARNING"
            if ok:
                logger.info("Health check OK: %s", health_summary(health))
            else:
                logger.warning("Health check degraded: %s", health_summary(health))
            notify_discord(
                title,
                health_summary(health),
                level,
                health_fields(health),
                dedupe_key=f"health:{health.get('status')}:{health_summary(health)}",
                force=ok != previous_ok,
            )
            last_alert = now
        previous_ok = ok


def health_summary(health: dict) -> str:
    broken = [
        f"{name}: {check.get('message', 'failed')}"
        for name, check in health.get("checks", {}).items()
        if not check.get("ok")
    ]
    if not broken:
        return "All health checks are OK."
    return "\n".join(broken)[:3900]


def health_fields(health: dict) -> dict[str, object]:
    fields: dict[str, object] = {"Status": health.get("status", "unknown")}
    for name, check in health.get("checks", {}).items():
        fields[name] = "OK" if check.get("ok") else check.get("message", "failed")
    metrics = health.get("metrics", {})
    fields["Running requests"] = metrics.get("running_requests", 0)
    fields["Total requests"] = metrics.get("total_requests", 0)
    fields["Failed requests"] = metrics.get("failed_requests", 0)
    return fields
