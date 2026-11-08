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
    # ── Mode ────────────────────────────────────────────────────────────
    # True: MockWorldClient, an in-process simulated town (mock_world.py).
    # False: HttpWorldClient, real HTTP+WS calls to a running world
    # process. See module docstring for why the default is True.
    # A REAL WORLD SERVER IS THE ONLY THING THIS SERVES.
    #
    # Defaults False and is no longer readable from MOCK_MODE. The map used
    # to be able to show 12 invented residents that match no genesis id,
    # which is fine as a demo and indistinguishable from a broken live feed
    # the moment anyone is trying to watch a real run.
    #
    # Still a field: game/tests/*.py construct GameSettings(mock_mode=True)
    # to exercise the relay and the town view without a world/ process.
    mock_mode: bool = False

    # ── World connection (only read when mock_mode is False) ────────────
    # app/CONVENTIONS.md: world's default dev port is 8100.
    world_base_url: str = "http://localhost:8100"
    world_ws_url: str = "ws://localhost:8100/ws/events"
    world_http_timeout_s: float = 10.0
    # Backoff between reconnect attempts to world's event WebSocket
    # (relay.py) if the connection drops - a live m5.metal deploy runs
    # world/ and game/ as separate processes on the same box (DESIGN.md
    # section 9 architecture diagram), so a world/ restart should not
    # require restarting game/ too.
    world_ws_reconnect_delay_s: float = 2.0

    # ── HTTP server ───────────────────────────────────────────────────────
    # 8100 is world/, 8200 is broker/, 8300 is game/console (the
    # operator's separate auth-gated FastAPI app - see game/README.md and
    # game/console/config.py's own PORT default). This service is the
    # unauthenticated live town view, one port further down the same
    # sequence.
    host: str = "0.0.0.0"
    port: int = 8400

    # ── Mock town (mock_world.py; only used when mock_mode is True) ─────
    mock_agent_count: int = 12
    mock_seed: int = 20260730

    # ── Snapshot fan-out ──────────────────────────────────────────────────
    # relay.py buffers this many recent WireEvents so a browser tab that
    # briefly disconnects and reconnects (a laptop lid closing, a flaky
    # wifi hop) can catch up without a full snapshot refetch. Mirrors
    # world.core.events.EventBus's own buffer_size default (2000) at a
    # smaller scale, since game/ only needs "recent," not "since sim
    # start."
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

        # MOCK_MODE is deliberately NOT read - see GameSettings.mock_mode.
        # Rejected rather than ignored: someone setting it expects a fake
        # town, and quietly giving them a live one instead is worse.
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
