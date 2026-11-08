"""Game server settings - env-driven, fully validated, aggregated errors.

Mirrors broker/config.py's BrokerSettings shape exactly (CONVENTIONS.md
calls engine/src/config.py's ConfigError pattern out as the one to match;
broker/ already ported it once, this is the same port for game/): every
field is declared once with its env var and its check, and
load_validated() raises ONE ConfigError carrying every problem at once.

MOCK_MODE is GONE as an env var and mock_mode now defaults to False.
UI-21: this paragraph used to claim the opposite - it was written when
the game served a fake town by default, and was not updated when that
stopped. A reader following it expects `make run-game` to work with no
world running, and spends time finding out why it does not.
does: `make run-game` and `make test` must both work with no running
world process and no network, since the world module owner has not built
past `world/app.py`'s `/health` placeholder yet (see contracts.py's
CONTRACT docstring). Only a deploy that wants the live town points this
at a real world_base_url/world_ws_url.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parent / "static"


class ConfigError(ValueError):
    """Aggregated configuration failure - carries every error at once, same
    rationale as broker/config.py's ConfigError."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid game configuration ({len(self.errors)} error(s)):\n  - {joined}")


def parse_bool(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    raise ValueError(f"{name}={value!r} - must be 'true' or 'false' (case-insensitive)")


def parse_int(name: str, value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise ValueError(f"{name}={value!r} - must be an integer") from None


def parse_float(name: str, value: str) -> float:
    try:
        return float(value.strip())
    except ValueError:
        raise ValueError(f"{name}={value!r} - must be a number") from None


def read_env_file(env_path: Path | None = None) -> dict[str, str]:
    """Parse a .env file into a dict. Missing file returns empty."""
    if env_path is None:
        env_path = Path(".env")
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    return env_vars


@dataclass
class GameSettings:
    mock_mode: bool = False

    world_base_url: str = "http://localhost:8100"
    world_ws_url: str = "ws://localhost:8100/ws/events"
    world_http_timeout_s: float = 10.0
    world_ws_reconnect_delay_s: float = 2.0

    host: str = "0.0.0.0"
    port: int = 8400

    mock_agent_count: int = 12
    mock_seed: int = 20260730

    replay_buffer_size: int = 200

    extra_static_root: Path | None = field(default=None, repr=False)

    def validate(self) -> list[str]:
        """Full semantic validation. Returns ALL errors (empty = valid)."""
        errors: list[str] = []

        if self.port <= 0 or self.port > 65535:
            errors.append(f"GAME_PORT={self.port} - must be between 1 and 65535")
        if self.world_http_timeout_s <= 0:
            errors.append(
                f"WORLD_HTTP_TIMEOUT_S={self.world_http_timeout_s} - must be greater than 0"
            )
        if self.world_ws_reconnect_delay_s <= 0:
            errors.append(
                "WORLD_WS_RECONNECT_DELAY_S="
                f"{self.world_ws_reconnect_delay_s} - must be greater than 0"
            )
        if self.mock_agent_count <= 0:
            errors.append(f"GAME_MOCK_AGENT_COUNT={self.mock_agent_count} - must be greater than 0")
        if self.replay_buffer_size <= 0:
            errors.append(f"GAME_REPLAY_BUFFER_SIZE={self.replay_buffer_size} - must be greater than 0")

        if not self.mock_mode:
            if not self.world_base_url:
                errors.append("WORLD_BASE_URL is required when MOCK_MODE=false")
            if not self.world_ws_url:
                errors.append("WORLD_WS_URL is required when MOCK_MODE=false")

        return errors

    @classmethod
    def build_from_env(cls, env_vars: dict[str, str]) -> tuple["GameSettings", list[str]]:
        errors: list[str] = []
        kwargs: dict[str, object] = {}

        def get(name: str) -> str | None:
            return os.environ.get(name, env_vars.get(name))

        if get("MOCK_MODE") is not None:
            errors.append(
                "MOCK_MODE is no longer supported - game/ only serves a real world "
                "server. Point WORLD_BASE_URL at one."
            )
        if (raw := get("WORLD_BASE_URL")) is not None:
            kwargs["world_base_url"] = raw.strip()
        if (raw := get("WORLD_WS_URL")) is not None:
            kwargs["world_ws_url"] = raw.strip()
        if (raw := get("WORLD_HTTP_TIMEOUT_S")) is not None:
            try:
                kwargs["world_http_timeout_s"] = parse_float("WORLD_HTTP_TIMEOUT_S", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("WORLD_WS_RECONNECT_DELAY_S")) is not None:
            try:
                kwargs["world_ws_reconnect_delay_s"] = parse_float("WORLD_WS_RECONNECT_DELAY_S", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("GAME_HOST")) is not None:
            kwargs["host"] = raw.strip()
        if (raw := get("GAME_PORT")) is not None:
            try:
                kwargs["port"] = parse_int("GAME_PORT", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("GAME_MOCK_AGENT_COUNT")) is not None:
            try:
                kwargs["mock_agent_count"] = parse_int("GAME_MOCK_AGENT_COUNT", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("GAME_MOCK_SEED")) is not None:
            try:
                kwargs["mock_seed"] = parse_int("GAME_MOCK_SEED", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("GAME_REPLAY_BUFFER_SIZE")) is not None:
            try:
                kwargs["replay_buffer_size"] = parse_int("GAME_REPLAY_BUFFER_SIZE", raw)
            except ValueError as e:
                errors.append(str(e))

        return cls(**kwargs), errors  # type: ignore[arg-type]

    @classmethod
    def load(cls, env_path: Path | None = None) -> "GameSettings":
        """Parse env/.env only, no semantic validation. Raises ConfigError
        aggregating every parse error."""
        settings, errors = cls.build_from_env(read_env_file(env_path))
        if errors:
            raise ConfigError(errors)
        return settings

    @classmethod
    def load_validated(cls, env_path: Path | None = None) -> "GameSettings":
        """Boot entry point: parse AND validate, one aggregated report."""
        settings, errors = cls.build_from_env(read_env_file(env_path))
        errors.extend(settings.validate())
        if errors:
            raise ConfigError(errors)
        return settings
