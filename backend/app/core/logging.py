"""Structured logging configuration.

Per PROJECT_CONTRACT.md section 8, logs must never contain secrets or
credentials. This module only ever logs the settings' non-sensitive
shape (env name, mode, demo flag) — never secret values.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Minimal structured (JSON-lines) log formatter.

    Kept dependency-free in Phase 1. If a richer structured-logging
    library becomes necessary in a later phase, it should replace this
    formatter without changing the `get_logger` call sites.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure root logging once, at application startup."""

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())

    # Avoid duplicate handlers if configure_logging is called more than
    # once (e.g. under test collection / reload).
    root_logger.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Retrieve a module-scoped logger."""

    return logging.getLogger(name)
