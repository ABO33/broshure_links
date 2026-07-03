from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from queue import Empty, Queue
import threading
import time
from traceback import format_exception
from urllib.error import URLError
from urllib.request import Request, urlopen


LEVEL_COLORS = {
    "DEBUG": 0x95A5A6,
    "INFO": 0x3498DB,
    "WARNING": 0xF1C40F,
    "ERROR": 0xE74C3C,
    "CRITICAL": 0x8E0000,
}


class DiscordNotifier:
    def __init__(self) -> None:
        self.info_webhook_url = (
            os.environ.get("DISCORD_INFO_WEBHOOK_URL", "").strip()
            or os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        )
        self.error_webhook_url = os.environ.get("DISCORD_ERROR_WEBHOOK_URL", "").strip()
        self.error_min_level = _level_number(os.environ.get("DISCORD_ERROR_LEVEL", "WARNING"), logging.WARNING)
        self.app_name = os.environ.get("DISCORD_APP_NAME", "Praktis Brochure Linker").strip()
        self.min_duplicate_seconds = int(os.environ.get("DISCORD_MIN_DUPLICATE_SECONDS", "60"))
        self.timeout_seconds = int(os.environ.get("DISCORD_TIMEOUT_SECONDS", "8"))
        self.queue: Queue[dict] = Queue(maxsize=200)
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.last_sent: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.info_webhook_url or self.error_webhook_url)

    def send(
        self,
        title: str,
        description: str = "",
        level: str = "INFO",
        fields: dict[str, object] | None = None,
        dedupe_key: str | None = None,
        force: bool = False,
    ) -> None:
        if not self.enabled:
            return

        level_name = str(level or "INFO").upper()
        webhook_url = self._webhook_url(level_name)
        if not webhook_url:
            return
        key = dedupe_key or f"{level_name}:{title}:{description[:160]}"
        now = time.monotonic()
        if not force and self.min_duplicate_seconds > 0:
            previous = self.last_sent.get(key, 0)
            if now - previous < self.min_duplicate_seconds:
                return
        self.last_sent[key] = now

        payload = self._payload(title, description, level_name, fields or {})
        try:
            self._ensure_worker()
            self.queue.put_nowait({"webhook_url": webhook_url, "payload": payload})
        except Exception:
            # Discord alerts should never break brochure processing.
            pass

    def _webhook_url(self, level_name: str) -> str:
        is_error_level = _level_number(level_name, logging.INFO) >= self.error_min_level
        if is_error_level:
            return self.error_webhook_url or self.info_webhook_url
        return self.info_webhook_url

    def _payload(self, title: str, description: str, level: str, fields: dict[str, object]) -> dict:
        clean_fields = []
        for name, value in fields.items():
            text = str(value)
            if not text:
                continue
            clean_fields.append(
                {
                    "name": str(name)[:256],
                    "value": text[:1000],
                    "inline": len(text) < 80,
                }
            )

        embed = {
            "title": f"{level}: {title}",
            "description": str(description or "")[:3900],
            "color": LEVEL_COLORS.get(level, LEVEL_COLORS["INFO"]),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": clean_fields[:20],
            "footer": {"text": self.app_name},
        }
        return {
            "username": self.app_name,
            "embeds": [embed],
            "allowed_mentions": {"parse": []},
        }

    def _ensure_worker(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._worker, name="discord-notifier", daemon=True)
            self.thread.start()

    def _worker(self) -> None:
        while True:
            try:
                item = self.queue.get(timeout=30)
            except Empty:
                return
            try:
                self._post(item["webhook_url"], item["payload"])
            except Exception:
                pass
            finally:
                self.queue.task_done()

    def _post(self, webhook_url: str, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "praktis-brochure-linker/2.0"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status >= 400:
                raise URLError(f"Discord webhook returned HTTP {response.status}")


class DiscordLogHandler(logging.Handler):
    def __init__(self, notifier: DiscordNotifier | None = None) -> None:
        super().__init__()
        self.notifier = notifier or get_notifier()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name == __name__:
                return
            message = record.getMessage()
            fields: dict[str, object] = {
                "Logger": record.name,
                "Location": f"{record.pathname}:{record.lineno}",
            }
            if record.exc_info:
                fields["Traceback"] = "".join(format_exception(*record.exc_info))[-1000:]
            self.notifier.send(
                title=message[:180],
                description=self.format(record)[-3900:],
                level=record.levelname,
                fields=fields,
                dedupe_key=f"log:{record.levelname}:{record.name}:{message[:220]}",
            )
        except Exception:
            pass


_notifier: DiscordNotifier | None = None


def get_notifier() -> DiscordNotifier:
    global _notifier
    if _notifier is None:
        _notifier = DiscordNotifier()
    return _notifier


def notify_discord(
    title: str,
    description: str = "",
    level: str = "INFO",
    fields: dict[str, object] | None = None,
    dedupe_key: str | None = None,
    force: bool = False,
) -> None:
    get_notifier().send(title, description, level, fields, dedupe_key, force)


def _level_number(level_name: str, default: int) -> int:
    return getattr(logging, str(level_name or "").upper(), default)
