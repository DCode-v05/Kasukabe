"""Tests that JSONFormatter redacts secrets from log messages.

SecretScanner.redact() is wired into JSONFormatter.format() so that any
secret accidentally reaching a log call site is sanitised before the JSON
line is written to stdout. These tests assert that invariant end-to-end
using the real formatter — no mocks.
"""

from __future__ import annotations

import json
import logging

import pytest

from src.logging_config import JSONFormatter, MAX_FIELD_CHARS
from tests.test_secret_scanner import CANONICAL_POSITIVES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format(msg: str, level: int = logging.DEBUG, **kwargs: object) -> dict:
    """Format a log record through JSONFormatter and return the parsed dict."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    return json.loads(formatter.format(record))


def format_with_args(msg: str, args: tuple, level: int = logging.DEBUG) -> dict:
    """Format a log record that uses %-style args."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=args,
        exc_info=None,
    )
    return json.loads(formatter.format(record))


# ---------------------------------------------------------------------------
# Basic redaction at all standard log levels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level",
    [
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    ],
)
def test_secret_redacted_at_all_log_levels(level: int) -> None:
    secret = CANONICAL_POSITIVES["github-pat"]
    entry = format(f"token value: {secret} end", level=level)
    assert secret not in entry["msg"]
    assert "[REDACTED]" in entry["msg"]


# ---------------------------------------------------------------------------
# Secrets embedded in %-formatted log messages
# ---------------------------------------------------------------------------


def test_secret_redacted_in_percent_formatted_message() -> None:
    """Secrets that arrive via %-style log args are caught after args are applied."""
    secret = CANONICAL_POSITIVES["aws-access-token"]
    entry = format_with_args("cmd=%s", args=(secret,))
    assert secret not in entry["msg"]
    assert "[REDACTED]" in entry["msg"]


def test_secret_embedded_in_command_string() -> None:
    """Simulates a permissions.py debug log: command text can contain secrets."""
    secret = CANONICAL_POSITIVES["anthropic-api-key"]
    cmd = f"curl -H 'Authorization: Bearer {secret}' https://api.example.com"
    entry = format_with_args(
        "Permission ALLOWED (sandbox auto-allow): tool=%s cmd=%.200s",
        args=("Bash", cmd),
    )
    assert secret not in entry["msg"]
    assert "[REDACTED]" in entry["msg"]
    # Structural parts of the message should be preserved.
    assert "Permission ALLOWED" in entry["msg"]
    assert "Bash" in entry["msg"]


# ---------------------------------------------------------------------------
# Clean messages are unchanged
# ---------------------------------------------------------------------------


def test_clean_message_passes_through_unchanged() -> None:
    msg = "Sandbox auto-allow: read-only command accepted"
    entry = format(msg)
    assert entry["msg"] == msg


def test_clean_percent_message_passes_through_unchanged() -> None:
    entry = format_with_args("tool=%s cmd=%.80s", args=("Bash", "ls -la /tmp"))
    assert "[REDACTED]" not in entry["msg"]
    assert "Bash" in entry["msg"]
    assert "ls -la /tmp" in entry["msg"]


# ---------------------------------------------------------------------------
# JSON structure is preserved
# ---------------------------------------------------------------------------


def test_json_structure_intact_after_redaction() -> None:
    secret = CANONICAL_POSITIVES["github-pat"]
    entry = format(f"leaked: {secret}")
    assert "ts" in entry
    assert "level" in entry
    assert "logger" in entry
    assert "msg" in entry
    assert entry["level"] == "DEBUG"
    assert entry["logger"] == "test.logger"


# ---------------------------------------------------------------------------
# Multiple secrets in one message
# ---------------------------------------------------------------------------


def test_multiple_secrets_all_redacted() -> None:
    aws = CANONICAL_POSITIVES["aws-access-token"]
    gh = CANONICAL_POSITIVES["github-pat"]
    entry = format(f"aws={aws} gh={gh}")
    assert aws not in entry["msg"]
    assert gh not in entry["msg"]
    assert entry["msg"].count("[REDACTED]") >= 2


# ---------------------------------------------------------------------------
# Empty / whitespace messages
# ---------------------------------------------------------------------------


def test_empty_message_does_not_raise() -> None:
    entry = format("")
    assert entry["msg"] == ""


def test_whitespace_only_message_preserved() -> None:
    entry = format("   ")
    assert entry["msg"] == "   "


# ---------------------------------------------------------------------------
# Exception text redaction
# ---------------------------------------------------------------------------


def format_with_exc(msg: str, exc: BaseException) -> dict:
    """Format a record that carries a captured exc_info tuple."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return json.loads(formatter.format(record))


def test_secret_in_exception_message_redacted() -> None:
    """Secrets embedded in an exception's args land in formatException
    output — without redaction they'd leak into log_entry[exception]."""
    secret = CANONICAL_POSITIVES["github-pat"]
    try:
        raise RuntimeError(f"upstream rejected token {secret}")
    except RuntimeError as exc:
        entry = format_with_exc("call failed", exc)
    assert secret not in entry["exception"]
    assert "[REDACTED]" in entry["exception"]
    # Structural framing of the traceback is preserved.
    assert "RuntimeError" in entry["exception"]
    assert "Traceback" in entry["exception"]


def test_secret_in_nested_exception_cause_redacted() -> None:
    secret = CANONICAL_POSITIVES["aws-access-token"]
    try:
        try:
            raise ValueError(f"aws creds {secret}")
        except ValueError as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as exc:
        entry = format_with_exc("outer", exc)
    assert secret not in entry["exception"]


# ---------------------------------------------------------------------------
# Field-name JSON redaction (opaque values without a format match)
# ---------------------------------------------------------------------------


def test_opaque_token_in_json_body_redacted() -> None:
    """A JSON body with an opaque token that matches no gitleaks rule
    still gets redacted because the field name is in the sensitive list."""
    opaque = "xxx-opaque-internal-session-id-99"
    body = f'{{"session_ingress_token":"{opaque}","other":"visible"}}'
    entry = format(body)
    assert opaque not in entry["msg"]
    assert '"session_ingress_token":"[REDACTED]"' in entry["msg"]
    assert '"other":"visible"' in entry["msg"]


def test_field_name_redaction_is_case_insensitive() -> None:
    entry = format('{"Authorization":"Bearer xyz-custom-opaque-token"}')
    assert "xyz-custom-opaque-token" not in entry["msg"]
    assert "[REDACTED]" in entry["msg"]


def test_multiple_sensitive_fields_all_redacted() -> None:
    body = (
        '{"access_token":"opaque-a-01234","refresh_token":"opaque-b-56789",'
        '"password":"hunter2","harmless":"keep"}'
    )
    entry = format(body)
    assert "opaque-a-01234" not in entry["msg"]
    assert "opaque-b-56789" not in entry["msg"]
    assert "hunter2" not in entry["msg"]
    assert '"harmless":"keep"' in entry["msg"]


# ---------------------------------------------------------------------------
# Size truncation
# ---------------------------------------------------------------------------


def test_long_message_truncated_with_length_suffix() -> None:
    long_msg = "x" * (MAX_FIELD_CHARS + 500)
    entry = format(long_msg)
    assert len(entry["msg"]) < len(long_msg)
    assert entry["msg"].endswith(f"... ({len(long_msg)} chars)")
    assert entry["msg"].startswith("x" * 100)


def test_message_exactly_at_limit_not_truncated() -> None:
    msg = "y" * MAX_FIELD_CHARS
    entry = format(msg)
    assert entry["msg"] == msg


def test_long_exception_truncated() -> None:
    long_payload = "z" * (MAX_FIELD_CHARS + 2000)
    try:
        raise RuntimeError(long_payload)
    except RuntimeError as exc:
        entry = format_with_exc("failed", exc)
    assert len(entry["exception"]) <= MAX_FIELD_CHARS + 40  # room for suffix
    assert "chars)" in entry["exception"]


# ---------------------------------------------------------------------------
# LogRecord extras scanning
# ---------------------------------------------------------------------------


def format_with_extra(msg: str, extra: dict[str, object]) -> dict:
    """Emit via the real logging machinery so `extra=` attaches the way
    production code would, then capture the formatted JSON."""
    formatter = JSONFormatter()
    logger = logging.getLogger(f"test.extras.{id(extra)}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    captured: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(formatter.format(record))

    logger.addHandler(CaptureHandler())
    logger.info(msg, extra=extra)
    return json.loads(captured[0])


def test_extras_values_are_scanned() -> None:
    secret = CANONICAL_POSITIVES["github-pat"]
    entry = format_with_extra(
        "http call",
        extra={"command": f"curl -H 'Authorization: Bearer {secret}'"},
    )
    assert "extras" in entry
    assert secret not in entry["extras"]["command"]
    assert "[REDACTED]" in entry["extras"]["command"]


def test_extras_field_name_redaction_applies() -> None:
    """extras values are also passed through the field-name pass, so a
    serialized JSON body attached as extra gets its opaque tokens masked."""
    opaque = "zz-internal-session-id-4242"
    entry = format_with_extra(
        "req",
        extra={"payload": f'{{"access_token":"{opaque}"}}'},
    )
    assert opaque not in entry["extras"]["payload"]
    assert "[REDACTED]" in entry["extras"]["payload"]


def test_extras_dict_value_is_json_serialized_before_scan() -> None:
    """Non-string extras must be JSON-serialized (not Python-repr'd)
    before sanitization. `str({...})` produces single-quoted output that
    the field-name regex cannot match; `json.dumps(...)` produces the
    double-quoted shape the regex expects, so opaque tokens nested in
    dict extras get redacted."""
    opaque = "opaque-nested-token-xyz"
    entry = format_with_extra(
        "req",
        extra={"body": {"access_token": opaque, "user": "alice"}},
    )
    assert opaque not in entry["extras"]["body"]
    assert "[REDACTED]" in entry["extras"]["body"]
    assert "alice" in entry["extras"]["body"]


def test_extras_list_of_dicts_values_redacted() -> None:
    """Lists of dicts (e.g. batched request bodies) must also pass
    through JSON serialization, not Python repr."""
    opaque_a = "opaque-batch-item-aaaaa"
    opaque_b = "opaque-batch-item-bbbbb"
    entry = format_with_extra(
        "batch",
        extra={
            "items": [
                {"token": opaque_a},
                {"token": opaque_b, "kind": "retry"},
            ]
        },
    )
    assert opaque_a not in entry["extras"]["items"]
    assert opaque_b not in entry["extras"]["items"]
    assert entry["extras"]["items"].count("[REDACTED]") >= 2
    assert "retry" in entry["extras"]["items"]


def test_extras_non_serializable_falls_back_to_str() -> None:
    """`json.dumps(default=str)` keeps serialization total even for
    custom objects — they become their `__str__` form and still pass
    through the scanner, so no leak path opens via an unusual extra."""

    class Odd:
        def __str__(self) -> str:
            return "odd-object-no-secret"

    entry = format_with_extra("x", extra={"obj": Odd()})
    assert "odd-object-no-secret" in entry["extras"]["obj"]


def test_reserved_record_attrs_excluded_from_extras() -> None:
    """Standard LogRecord attrs (levelname, created, etc.) must never
    appear under `extras` — only caller-supplied keys."""
    entry = format_with_extra("hello", extra={"custom": "value"})
    assert entry["extras"] == {"custom": "value"}
    assert "levelname" not in entry["extras"]
    assert "created" not in entry["extras"]
    assert "args" not in entry["extras"]


def test_no_extras_key_when_caller_provides_none() -> None:
    entry = format("plain message")
    assert "extras" not in entry
