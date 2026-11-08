"""Tests for ConsoleSettings (config.py) - env parsing, aggregated
validation errors, and the ephemeral-token fallback."""

from __future__ import annotations

import pytest

from game.console.config import ConfigError, ConsoleSettings


def test_build_from_env_reads_every_field() -> None:
    settings, errors = ConsoleSettings.build_from_env(
        {
            "OPERATOR_TOKEN": "secret-123",
            "WORLD_BASE_URL": "http://world:8100",
            "WORLD_HTTP_TIMEOUT_S": "5",
            "CONSOLE_HOST": "127.0.0.1",
            "CONSOLE_PORT": "9000",
        }
    )
    assert errors == []
    assert settings.operator_token == "secret-123"
    assert settings.world_base_url == "http://world:8100"
    assert settings.world_http_timeout_s == 5.0
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.operator_token_was_generated is False


def test_missing_operator_token_auto_generates_a_random_one() -> None:
    settings, errors = ConsoleSettings.build_from_env({})
    assert errors == []
    assert settings.operator_token
    assert settings.operator_token_was_generated is True


def test_two_auto_generated_tokens_differ() -> None:
    first, _ = ConsoleSettings.build_from_env({})
    second, _ = ConsoleSettings.build_from_env({})
    assert first.operator_token != second.operator_token


def test_explicit_empty_operator_token_fails_validation() -> None:
    settings, parse_errors = ConsoleSettings.build_from_env({"OPERATOR_TOKEN": ""})
    assert parse_errors == []
    assert settings.operator_token_was_generated is False
    errors = settings.validate()
    assert any("OPERATOR_TOKEN" in e for e in errors)


def test_bad_timeout_is_a_parse_error() -> None:
    _, errors = ConsoleSettings.build_from_env({"WORLD_HTTP_TIMEOUT_S": "not-a-number"})
    assert len(errors) == 1
    assert "WORLD_HTTP_TIMEOUT_S" in errors[0]


def test_bad_port_is_a_parse_error() -> None:
    _, errors = ConsoleSettings.build_from_env({"CONSOLE_PORT": "not-an-int"})
    assert len(errors) == 1
    assert "CONSOLE_PORT" in errors[0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("world_base_url", ""),
        ("world_http_timeout_s", 0.0),
        ("world_http_timeout_s", -1.0),
        ("port", 0),
        ("port", 70000),
    ],
)
def test_validate_rejects_bad_values(field: str, value: object) -> None:
    settings = ConsoleSettings(operator_token="t")
    setattr(settings, field, value)
    errors = settings.validate()
    assert errors, f"{field}={value!r} should have produced a validation error"


def test_validate_accepts_defaults_with_a_token() -> None:
    settings = ConsoleSettings(operator_token="t")
    assert settings.validate() == []


def test_load_validated_aggregates_every_error_at_once() -> None:
    env = {
        "OPERATOR_TOKEN": "",
        "WORLD_HTTP_TIMEOUT_S": "0",
        "CONSOLE_PORT": "-1",
    }
    settings, parse_errors = ConsoleSettings.build_from_env(env)
    assert parse_errors == []
    all_errors = parse_errors + settings.validate()
    assert len(all_errors) == 3


def test_load_validated_raises_config_error_with_generated_message(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OPERATOR_TOKEN=\nWORLD_HTTP_TIMEOUT_S=0\n")
    with pytest.raises(ConfigError) as excinfo:
        ConsoleSettings.load_validated(env_path)
    assert "OPERATOR_TOKEN" in str(excinfo.value)
    assert "WORLD_HTTP_TIMEOUT_S" in str(excinfo.value)


def test_load_validated_succeeds_with_no_env_file(tmp_path) -> None:
    settings = ConsoleSettings.load_validated(tmp_path / "does-not-exist.env")
    assert settings.operator_token
    assert settings.operator_token_was_generated is True
