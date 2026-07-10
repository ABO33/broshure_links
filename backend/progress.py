from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
import time


PHASE_WEIGHTS: dict[str, OrderedDict[str, float]] = {
    "fallback_links": OrderedDict(
        [
            ("extract_pdf", 0.16),
            ("detect_items", 0.42),
            ("resolve_links", 0.08),
            ("group_links", 0.08),
            ("write_pdf", 0.22),
            ("finalize", 0.04),
        ]
    ),
    "website_links_prices": OrderedDict(
        [
            ("extract_pdf", 0.05),
            ("detect_items", 0.10),
            ("resolve_links", 0.02),
            ("website_lookup", 0.64),
            ("group_links", 0.14),
            ("write_pdf", 0.04),
            ("finalize", 0.01),
        ]
    ),
    "excel_prices": OrderedDict(
        [
            ("extract_pdf", 0.15),
            ("detect_items", 0.38),
            ("excel_load", 0.10),
            ("resolve_links", 0.05),
            ("excel_compare", 0.22),
            ("write_pdf", 0.08),
            ("finalize", 0.02),
        ]
    ),
    "full_check": OrderedDict(
        [
            ("extract_pdf", 0.04),
            ("detect_items", 0.08),
            ("excel_load", 0.02),
            ("resolve_links", 0.02),
            ("website_lookup", 0.59),
            ("excel_compare", 0.05),
            ("triple_compare", 0.02),
            ("group_links", 0.14),
            ("write_pdf", 0.03),
            ("finalize", 0.01),
        ]
    ),
}


@dataclass
class ProgressReporter:
    request_id: str
    mode: str
    file_name: str = ""
    started_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    phase_started_at: float = field(default_factory=time.monotonic)
    phase: str = "starting"
    detail: str = ""
    completed: int = 0
    total: int = 0
    percent: float = 0.0
    eta_seconds: float | None = None
    status: str = "running"
    error: str = ""
    _lock: RLock = field(default_factory=RLock, repr=False)

    def update(self, phase: str, completed: int = 0, total: int = 0, detail: str = "") -> None:
        now = time.monotonic()
        with self._lock:
            if self.status != "running":
                return
            if phase != self.phase:
                self.phase = phase
                self.phase_started_at = now
            self.completed = max(0, int(completed or 0))
            self.total = max(0, int(total or 0))
            self.detail = str(detail or "")[:180]
            self.updated_at = now
            self.percent = max(self.percent, self._weighted_percent())
            self.eta_seconds = self._estimate_eta(now)

    def finish(self) -> None:
        with self._lock:
            self.status = "complete"
            self.phase = "complete"
            self.detail = ""
            self.completed = max(self.completed, self.total)
            self.percent = 100.0
            self.eta_seconds = 0.0
            self.updated_at = time.monotonic()

    def fail(self, error: Exception | str) -> None:
        with self._lock:
            self.status = "error"
            self.phase = "error"
            self.error = str(error)[:300]
            self.eta_seconds = None
            self.updated_at = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = max(0.0, time.monotonic() - self.started_at)
            return {
                "requestId": self.request_id,
                "status": self.status,
                "mode": self.mode,
                "fileName": self.file_name,
                "phase": self.phase,
                "detail": self.detail,
                "completed": self.completed,
                "total": self.total,
                "percent": round(self.percent, 1),
                "elapsedSeconds": round(elapsed, 1),
                "estimatedSecondsRemaining": (
                    round(self.eta_seconds) if self.eta_seconds is not None else None
                ),
                "error": self.error,
            }

    def _weights(self) -> OrderedDict[str, float]:
        return PHASE_WEIGHTS.get(self.mode, PHASE_WEIGHTS["fallback_links"])

    def _weighted_percent(self) -> float:
        weights = self._weights()
        if self.phase not in weights:
            return self.percent
        completed_weight = 0.0
        for name, weight in weights.items():
            if name == self.phase:
                ratio = min(1.0, self.completed / self.total) if self.total else 0.0
                completed_weight += weight * ratio
                break
            completed_weight += weight
        return min(99.5, completed_weight * 100.0)

    def _estimate_eta(self, now: float) -> float | None:
        elapsed = max(0.1, now - self.started_at)
        phase_elapsed = max(0.1, now - self.phase_started_at)
        weights = list(self._weights())
        if "website_lookup" in weights:
            website_index = weights.index("website_lookup")
            current_index = weights.index(self.phase) if self.phase in weights else -1
            if current_index < website_index or (self.phase == "website_lookup" and self.completed == 0):
                return None
        if self.total > 0 and self.completed > 0 and self.completed < self.total:
            current_phase_eta = phase_elapsed / self.completed * (self.total - self.completed)
            # Later phases are normally much smaller. This allowance avoids promising
            # completion at the exact moment the current SKU/page loop ends.
            return max(1.0, current_phase_eta * 1.08)
        if self.percent >= 4.0 and self.percent < 99.5:
            return max(1.0, elapsed * (100.0 - self.percent) / self.percent)
        return None


_REPORTERS: dict[str, ProgressReporter] = {}
_REPORTERS_LOCK = RLock()
_REPORTER_TTL_SECONDS = 60 * 60


def create_progress(request_id: str, mode: str, file_name: str = "") -> ProgressReporter:
    reporter = ProgressReporter(request_id=request_id, mode=mode or "fallback_links", file_name=file_name)
    with _REPORTERS_LOCK:
        _prune_reporters()
        _REPORTERS[request_id] = reporter
    return reporter


def get_progress(request_id: str) -> dict | None:
    with _REPORTERS_LOCK:
        reporter = _REPORTERS.get(request_id)
    return reporter.snapshot() if reporter else None


def _prune_reporters() -> None:
    cutoff = time.monotonic() - _REPORTER_TTL_SECONDS
    stale = [key for key, reporter in _REPORTERS.items() if reporter.updated_at < cutoff]
    for key in stale:
        _REPORTERS.pop(key, None)
