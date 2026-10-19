"""Structured logging configuration.

Per PROJECT_CONTRACT.md section 8, logs must never contain secrets or
credentials. This module only ever logs the settings' non-sensitive
shape (env name, mode, demo flag) — never secret values.
"""

from __future__ import annotations

import json
import logging
import re
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


_SECRET_QUERY = re.compile(
    r"(?i)\b(access_token|request_token|api_secret|checksum|refresh_token|enctoken)=[^&\s\"']+"
)
_AUTH_HEADER = re.compile(r"(?i)\btoken\s+[A-Za-z0-9]+:[A-Za-z0-9]+")

#: Third-party loggers that print URLs or headers. Kite takes the access token
#: as a query parameter on some calls (DELETE /session/token) and its login
#: redirect carries the request token, so their records are redacted.
REDACTED_LOGGERS = ("httpx", "httpcore", "uvicorn.access", "uvicorn.error", "websockets")


def redact(text: str) -> str:
    text = _SECRET_QUERY.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return _AUTH_HEADER.sub("token [REDACTED]", text)


class RedactSecretsFilter(logging.Filter):
    """Masks token / secret query parameters and Kite auth headers."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def install_redaction() -> None:
    for name in REDACTED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactSecretsFilter) for f in logger.filters):
            logger.addFilter(RedactSecretsFilter())
    # request lines of the HTTP client are not needed at INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure root logging once, at application startup."""

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())

    # Avoid duplicate handlers if configure_logging is called more than
    # once (e.g. under test collection / reload).
    root_logger.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactSecretsFilter())
    root_logger.addHandler(handler)
    install_redaction()


def get_logger(name: str) -> logging.Logger:
    """Retrieve a module-scoped logger."""

    return logging.getLogger(name)
