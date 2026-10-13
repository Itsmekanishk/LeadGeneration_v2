"""Structured JSON logging with secret redaction.

Every log line is a single JSON object so logs can be shipped to any
aggregator. Secrets must never reach logs: the ``SecretsFilter`` scrubs
values registered via :func:`app.core.config.register_secret` before
records are emitted. Secret *keys* (password, token, api_key, ...)
are dropped from extra payloads entirely.
"""
from __future__ import annotations

from typing import Optional

import json
import logging
import sys
from datetime import datetime, timezone

_SECRET_KEY_MARKERS = (
    "password", "token", "secret", "api_key", "apikey", "authorization",
    "cookie", "credential", "smtp_pass", "private_key", "session",
)

_REDACTED = "***REDACTED***"


def _scrub(value):  # noqa: ANN202
    if isinstance(value, dict):
        return {k: _scrub_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _scrub_value(key: str, value):  # noqa: ANN202
    key_l = str(key).lower()
    if any(marker in key_l for marker in _SECRET_KEY_MARKERS):
        return _REDACTED
    return _scrub(value)


class SecretsFilter(logging.Filter):
    """Scrub secret keys from extras and known secret values from messages."""

    def __init__(self) -> None:
        super().__init__()
        self.secret_values: set[str] = set()

    def add_secret(self, value: Optional[str]) -> None:
        if value and len(value) >= 6:
            self.secret_values.add(value)

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        msg = record.getMessage()
        for secret in self.secret_values:
            if secret in msg:
                msg = msg.replace(secret, _REDACTED)
        record.msg = msg
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Format records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        std = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "taskName",
            "message", "event",
        }
        for key, value in record.__dict__.items():
            if key not in std and not key.startswith("_"):
                payload[key] = _scrub_value(key, value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class EventLogger:
    """Small helper that emits ``event``-tagged structured records."""

    def __init__(self, logger: logging.Logger, name: str) -> None:
        self._logger = logger
        self._name = name

    def event(self, event: str, level: int = logging.INFO, **fields) -> None:
        self._logger.log(level, event, extra={"event": event, **fields})

    def info(self, event: str, **fields) -> None:
        self.event(event, logging.INFO, **fields)

    def warning(self, event: str, **fields) -> None:
        self.event(event, logging.WARNING, **fields)

    def error(self, event: str, **fields) -> None:
        self.event(event, logging.ERROR, **fields)


def event_logger(name: str) -> EventLogger:
    """Get an EventLogger bound to *name* (uses root handler filters)."""
    return EventLogger(logging.getLogger(name), name)


def setup_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger exactly once."""
    root = logging.getLogger()
    if getattr(root, "_locallead_json", False):
        return
    root.setLevel(level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    sf = SecretsFilter()
    handler.addFilter(sf)

    # register known secret values from env (best effort; config registers too)
    from app.core.config import get_registered_secrets

    for s in get_registered_secrets():
        sf.add_secret(s)

    root.handlers = [handler]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    root._locallead_json = True  # type: ignore[attr-defined]
