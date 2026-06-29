from __future__ import annotations

import logging
import os
import sys


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
    if root.handlers:
        root.setLevel(level)
        for handler in root.handlers:
            handler.setFormatter(
                ColorFormatter(
                    "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        ColorFormatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.setLevel(level)
    root.addHandler(handler)
