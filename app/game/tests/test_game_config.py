"""GameSettings: parsing, aggregated validation errors, mock-mode default."""

from __future__ import annotations

import pytest

from game.config import ConfigError, GameSettings


def test_defaults_are_valid() -> None:
    assert GameSettings().validate() == []


def test_mock_mode_defaults_false() -> None:
    """A real world server unless a TEST asks otherwise, in code."""
    assert GameSettings().mock_mode is False


def test_default_port_does_not_collide_with_console() -> None:
    # game/console/config.py's ConsoleSettings.port defaults to 8300;
    # this service is a second, unauthenticated process on the same box
    # (see config.py's port field comment) and must not silently bind the
    # same port.
    assert GameSettings().port == 8400


def test_build_from_env_parses_known_vars() -> None:
    settings, errors = GameSettings.build_from_env(
        {
            "WORLD_BASE_URL": "http://world:8100",
            "WORLD_WS_URL": "ws://world:8100/ws/events",
            "WORLD_HTTP_TIMEOUT_S": "5",
            "WORLD_WS_RECONNECT_DELAY_S": "1.5",
            "GAME_HOST": "127.0.0.1",
            "GAME_PORT": "9000",
            "GAME_MOCK_AGENT_COUNT": "20",
            "GAME_MOCK_SEED": "7",
            "GAME_REPLAY_BUFFER_SIZE": "50",
        }
    )
    assert errors == []
    assert settings.mock_mode is False
    assert settings.world_base_url == "http://world:8100"
    assert settings.world_ws_url == "ws://world:8100/ws/events"
    assert settings.world_http_timeout_s == 5.0
    assert settings.world_ws_reconnect_delay_s == 1.5
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.mock_agent_count == 20
    assert settings.mock_seed == 7
    assert settings.replay_buffer_size == 50


def test_build_from_env_aggregates_every_parse_error() -> None:
    _, errors = GameSettings.build_from_env(
        {
            "MOCK_MODE": "not-a-bool",
            "WORLD_HTTP_TIMEOUT_S": "not-a-float",
            "GAME_PORT": "not-an-int",
            "GAME_MOCK_AGENT_COUNT": "also-not-an-int",
        }
    )
    assert len(errors) == 4


def test_validate_requires_world_urls_when_not_mock() -> None:
    settings = GameSettings(mock_mode=False, world_base_url="", world_ws_url="")
    errors = settings.validate()
    assert any("WORLD_BASE_URL" in e for e in errors)
    assert any("WORLD_WS_URL" in e for e in errors)


def test_validate_passes_with_empty_world_urls_when_mock_mode() -> None:
    settings = GameSettings(mock_mode=True, world_base_url="", world_ws_url="")
    assert settings.validate() == []


def test_validate_rejects_bad_port_and_intervals() -> None:
    settings = GameSettings(
        port=0,
        world_http_timeout_s=-1.0,
        world_ws_reconnect_delay_s=0,
        mock_agent_count=0,
        replay_buffer_size=-5,
    )
    errors = settings.validate()
    assert len(errors) == 5


def test_load_validated_raises_config_error_with_all_problems(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "os.environ", {"MOCK_MODE": "false", "GAME_PORT": "0", "WORLD_BASE_URL": ""}
    )
    missing_env_file = tmp_path / "does-not-exist.env"

    with pytest.raises(ConfigError) as excinfo:
        GameSettings.load_validated(env_path=missing_env_file)

    joined = "\n".join(excinfo.value.errors)
    assert "GAME_PORT" in joined
    assert "WORLD_BASE_URL" in joined


def test_config_error_lists_every_message() -> None:
    err = ConfigError(["first problem", "second problem"])
    assert "first problem" in str(err)
    assert "second problem" in str(err)
    assert err.errors == ["first problem", "second problem"]
