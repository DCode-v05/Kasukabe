"""BrokerSettings: parsing, aggregated validation errors, mock-mode
default."""

from __future__ import annotations

import pytest

from broker.config import BrokerSettings, ConfigError


def test_defaults_are_valid() -> None:
    settings = BrokerSettings()
    assert settings.validate() == []


def test_mock_mode_defaults_false() -> None:
    """Real Bedrock unless a TEST asks otherwise, in code."""
    assert BrokerSettings().mock_mode is False


def test_mock_mode_cannot_be_set_from_the_environment() -> None:
    """Rejected loudly, not ignored.

    An operator who sets MOCK_MODE expects canned responses. Silently
    giving them real Bedrock spend instead is the worse surprise of the
    two, so this is a boot error rather than a no-op.
    """
    _settings, errors = BrokerSettings.build_from_env({"MOCK_MODE": "true"})
    assert any("MOCK_MODE is no longer supported" in e for e in errors)


def test_defaults_match_task_spec_numbers() -> None:
    # TASK spec (broker-lanes track): "process-wide hard USD cap (default
    # 400.00)... warn at 80%" and "100 RPM per model PER REGION".
    settings = BrokerSettings()
    assert settings.rpm_per_lane == 100
    assert settings.spend_cap_usd == 400.0
    assert settings.spend_warn_fraction == 0.8


def test_build_from_env_parses_known_vars() -> None:
    settings, errors = BrokerSettings.build_from_env(
        {
            "WORLD_BASE_URL": "http://world:8100",
            "WORLD_HTTP_TIMEOUT_S": "5.0",
            "TRACE_BATCH_SIZE": "50",
            "TRACE_FLUSH_INTERVAL_S": "1.5",
            "RPM_PER_LANE": "100",
            "SPEND_CAP_USD": "250.0",
            "SPEND_WARN_FRACTION": "0.9",
            "CIRCUIT_BREAKER_THRESHOLD": "5",
            "CIRCUIT_BREAKER_COOLDOWN_S": "45.0",
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_KEY": "secret",
        }
    )
    assert errors == []
    assert settings.mock_mode is False
    assert settings.world_base_url == "http://world:8100"
    assert settings.world_http_timeout_s == 5.0
    assert settings.trace_batch_size == 50
    assert settings.trace_flush_interval_s == 1.5
    assert settings.rpm_per_lane == 100
    assert settings.spend_cap_usd == 250.0
    assert settings.spend_warn_fraction == 0.9
    assert settings.circuit_breaker_threshold == 5
    assert settings.circuit_breaker_cooldown_s == 45.0


def test_build_from_env_aggregates_every_parse_error() -> None:
    _, errors = BrokerSettings.build_from_env(
        {
            "MOCK_MODE": "not-a-bool",
            "TRACE_BATCH_SIZE": "not-an-int",
            "TRACE_FLUSH_INTERVAL_S": "not-a-float",
            "RPM_PER_LANE": "also-not-an-int",
            "SPEND_CAP_USD": "not-a-float-either",
        }
    )
    assert len(errors) == 5


def test_validate_requires_world_and_trace_store_when_not_mock() -> None:
    # The trace store is SQLite, not Supabase (DESIGN.md section 9). A real
    # run that cannot write traces has no research output, so an empty path
    # stays a hard error the same way the Supabase credentials used to be.
    settings = BrokerSettings(mock_mode=False, world_base_url="", trace_sqlite_path="")
    errors = settings.validate()
    assert any("WORLD_BASE_URL" in e for e in errors)
    assert any("TRACE_SQLITE_PATH" in e for e in errors)


def test_validate_accepts_real_mode_without_supabase_credentials() -> None:
    settings = BrokerSettings(
        mock_mode=False,
        world_base_url="http://world:8100",
        trace_sqlite_path="/data/kasukabe.db",
    )
    assert settings.validate() == []


def test_validate_rejects_nonpositive_batch_and_interval() -> None:
    settings = BrokerSettings(trace_batch_size=0, trace_flush_interval_s=-1.0, rpm_per_lane=0)
    errors = settings.validate()
    assert len(errors) == 3


def test_validate_rejects_bad_spend_governor_values() -> None:
    settings = BrokerSettings(spend_cap_usd=0.0, spend_warn_fraction=1.5)
    errors = settings.validate()
    assert any("SPEND_CAP_USD" in e for e in errors)
    assert any("SPEND_WARN_FRACTION" in e for e in errors)


def test_validate_rejects_bad_circuit_breaker_values() -> None:
    settings = BrokerSettings(circuit_breaker_threshold=0, circuit_breaker_cooldown_s=0.0)
    errors = settings.validate()
    assert any("CIRCUIT_BREAKER_THRESHOLD" in e for e in errors)
    assert any("CIRCUIT_BREAKER_COOLDOWN_S" in e for e in errors)


def test_load_validated_raises_config_error_with_all_problems(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Deterministic: a clean env plus one explicit bad var, not ambient
    # state - CONVENTIONS.md requires tests to be fast, free, and
    # deterministic, and reading the real process environment would be
    # none of those things.
    # WORLD_BASE_URL is blanked explicitly: it has a usable default, so
    # without this the only problem would be TRACE_BATCH_SIZE and the test
    # would not prove that errors AGGREGATE rather than short-circuit.
    monkeypatch.setattr(
        "os.environ",
        {"TRACE_BATCH_SIZE": "0", "WORLD_BASE_URL": ""},
    )
    missing_env_file = tmp_path / "does-not-exist.env"

    with pytest.raises(ConfigError) as excinfo:
        BrokerSettings.load_validated(env_path=missing_env_file)

    joined = "\n".join(excinfo.value.errors)
    assert "TRACE_BATCH_SIZE" in joined
    assert "WORLD_BASE_URL" in joined  # mock_mode=false pulls in the real-deployment checks


def test_config_error_lists_every_message() -> None:
    err = ConfigError(["first problem", "second problem"])
    assert "first problem" in str(err)
    assert "second problem" in str(err)
    assert err.errors == ["first problem", "second problem"]
