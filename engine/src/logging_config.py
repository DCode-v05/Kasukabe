"""Structured JSON logging for engine and orchestrator processes.

Engine writes JSON logs to stdout. Orchestrator captures stdout per engine
and pipes it to per-engine log files.

Context variables (request_id, user_id) are automatically included in every
log line when set — no need to pass them as parameters.

Secret-redaction contract
-------------------------
Every text field emitted by JSONFormatter is run through two redaction
passes before it is written to stdout:

1. `SecretScanner.redact_json_fields()` — catches opaque tokens embedded
   in serialized JSON bodies (HTTP request/response dumps, config blobs)
   by matching known sensitive field names.
2. `SecretScanner.redact()` — catches values whose shape matches a known
   credential format anywhere in the text (e.g. an API key pasted into
   a free-form error message or traceback).

Both passes run on `msg`, on any exception text emitted via `exc_info`,
and on every `extra=` value attached to the LogRecord. This makes the
formatter the single chokepoint for log-line redaction: even if a call
site forgets to sanitize, nothing reaches disk in the clear.

After redaction each text field is capped at `MAX_FIELD_CHARS` chars
with a `... (N chars)` suffix so a single verbose log line cannot blow
up the per-engine log file or downstream JSON parsers.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

from src.security.secret_scanner import get_scanner

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")
engine_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("engine_id", default="")


MAX_FIELD_CHARS = 8000


RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def truncate(text: str) -> str:
    """Cap a string at `MAX_FIELD_CHARS` with a length-disclosing suffix."""
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return text[:MAX_FIELD_CHARS] + f"... ({len(text)} chars)"


def sanitize(text: str) -> str:
    """Run both redaction passes then truncate. Order-independent because
    `[REDACTED]` never re-triggers either pattern."""
    scanner = get_scanner()
    return truncate(scanner.redact(scanner.redact_json_fields(text)))


def stringify_extra(value: object) -> str:
    """Render an `extra=` value as a scanner-friendly string.

    Strings pass through unchanged. Everything else goes through
    `json.dumps` so that nested dicts/lists emerge as *double-quoted*
    JSON — the shape `SecretScanner.redact_json_fields` expects. If we
    fell back to `str(value)` here, a dict would render with single
    quotes (Python repr) and the field-name regex would silently miss
    every opaque token inside. `default=str` keeps the serialization
    total: anything non-JSON (datetimes, custom objects) becomes its
    string form rather than raising.
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": sanitize(record.getMessage()),
        }

        uid = user_id_var.get("")
        if uid:
            log_entry["user_id"] = uid

        rid = request_id_var.get("")
        if rid:
            log_entry["request_id"] = rid

        eid = engine_id_var.get("")
        if eid:
            log_entry["engine_id"] = eid

        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = sanitize(self.formatException(record.exc_info))

        extras = {
            key: sanitize(stringify_extra(value))
            for key, value in record.__dict__.items()
            if key not in RESERVED_RECORD_ATTRS and not key.startswith("_")
        }
        if extras:
            log_entry["extras"] = extras

        return json.dumps(log_entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Set up JSON logging to stdout for the current process.

    Call once at process start (engine lifespan or orchestrator lifespan).
    Replaces any existing handlers on the root logger.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
