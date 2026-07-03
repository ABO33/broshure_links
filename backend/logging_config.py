from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from .discord_notifier import DiscordLogHandler


LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[41m",
}
RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not should_color_logs():
            return message
        color = LEVEL_COLORS.get(record.levelno, "")
        return f"{color}{message}{RESET}" if color else message


def should_color_logs() -> bool:
    value = os.environ.get("BROCHURE_LOG_COLOR", "1").lower()
    return value not in {"0", "false", "no", "off"}


def configure_logging() -> None:
    level_name = os.environ.get("BROCHURE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    console_formatter = ColorFormatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    plain_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if root.handlers:
        root.setLevel(level)
        for handler in root.handlers:
            if getattr(handler, "_brochure_file_handler", False):
                handler.setLevel(level)
            if getattr(handler, "_brochure_discord_handler", False):
                discord_level_name = os.environ.get("DISCORD_LOG_LEVEL", "ERROR").upper()
                handler.setLevel(getattr(logging, discord_level_name, logging.ERROR))
            if getattr(handler, "_brochure_file_handler", False) or getattr(handler, "_brochure_discord_handler", False):
                handler.setFormatter(plain_formatter)
            else:
                handler.setFormatter(console_formatter)
        _ensure_file_handler(root, level)
        _ensure_discord_handler(root)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(console_formatter)
    root.setLevel(level)
    root.addHandler(handler)
    _ensure_file_handler(root, level)
    _ensure_discord_handler(root)


def _ensure_file_handler(root: logging.Logger, level: int) -> None:
    log_dir = os.environ.get("BROCHURE_LOG_DIR", "").strip()
    if not log_dir:
        return
    if any(getattr(handler, "_brochure_file_handler", False) for handler in root.handlers):
        return

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / "brochure-linker.log"
    max_bytes = int(os.environ.get("BROCHURE_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    backups = int(os.environ.get("BROCHURE_LOG_BACKUPS", "5"))
    handler = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._brochure_file_handler = True
    root.addHandler(handler)


def _ensure_discord_handler(root: logging.Logger) -> None:
    if not any(
        os.environ.get(name, "").strip()
        for name in ("DISCORD_WEBHOOK_URL", "DISCORD_INFO_WEBHOOK_URL", "DISCORD_ERROR_WEBHOOK_URL")
    ):
        return
    if any(getattr(handler, "_brochure_discord_handler", False) for handler in root.handlers):
        return

    level_name = os.environ.get("DISCORD_LOG_LEVEL", "ERROR").upper()
    level = getattr(logging, level_name, logging.ERROR)
    handler = DiscordLogHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._brochure_discord_handler = True
    root.addHandler(handler)
