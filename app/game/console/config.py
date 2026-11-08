"""Console settings - env-driven, fully validated, aggregated errors.

Mirrors broker/config.py's shape (itself matching engine/src/config.py,
the pattern app/CONVENTIONS.md calls out by name): every field is
declared once with its env var and its check, and load_validated() raises
ONE ConfigError carrying every problem at once instead of failing on the
first bad value.

OPERATOR_TOKEN has no fixed default - unlike broker's MOCK_MODE=True,
there is no single safe constant here (a hardcoded default secret would
just be a well-known password). Leaving the env var unset instead
auto-generates a fresh random token per process (operator_token's
default_factory), the same "always boots, never wide open" posture
broker's MOCK_MODE gives it: `game.console.app`'s module-level `app =
create_app()` (needed so `make run-console` and
`from game.console.app import app` both work, matching broker/app.py's
own bottom-of-file `app = create_app()`) never fails to import for lack
of configuration, and every request is still gated behind a real,
unguessable secret. create_app() logs the generated token once at boot
so an operator running `make run-console` without setting OPERATOR_TOKEN
can still find it. Production deployments (infra/) set OPERATOR_TOKEN
explicitly so the token survives a process restart.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """Aggregated configuration failure - carries every error at once,
    same rationale as broker/config.py's ConfigError."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(
            f"invalid console configuration ({len(self.errors)} error(s)):\n  - {joined}"
        )


def parse_float(name: str, value: str) -> float:
    try:
        return float(value.strip())
    except ValueError:
        raise ValueError(f"{name}={value!r} - must be a number") from None


def parse_int(name: str, value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise ValueError(f"{name}={value!r} - must be an integer") from None


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
class ConsoleSettings:
    operator_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    operator_token_was_generated: bool = False
    """True only when OPERATOR_TOKEN was absent from the environment and
    build_from_env fell back to the random default - app.py logs a
    warning with the generated token in that case, so it is discoverable
    without grepping source. Never itself read for auth decisions."""

    world_base_url: str = "http://localhost:8100"
    world_http_timeout_s: float = 10.0

    host: str = "0.0.0.0"
    port: int = 8300

    def validate(self) -> list[str]:
        """Full semantic validation. Returns ALL errors (empty = valid)."""
        errors: list[str] = []

        if not self.operator_token:
            errors.append(
                "OPERATOR_TOKEN is required - the console has no auth without it "
                "(DESIGN.md section 8: 'Admin API must be auth-gated')"
            )
        if not self.world_base_url:
            errors.append("WORLD_BASE_URL is required")
        if self.world_http_timeout_s <= 0:
            errors.append(
                f"WORLD_HTTP_TIMEOUT_S={self.world_http_timeout_s} - must be greater than 0"
            )
        if not (0 < self.port <= 65535):
            errors.append(f"PORT={self.port} - must be between 1 and 65535")

        return errors

    @classmethod
    def build_from_env(cls, env_vars: dict[str, str]) -> tuple[ConsoleSettings, list[str]]:
        errors: list[str] = []
        kwargs: dict[str, object] = {}

        def get(name: str) -> str | None:
            return os.environ.get(name, env_vars.get(name))

        if (raw := get("OPERATOR_TOKEN")) is not None:
            kwargs["operator_token"] = raw
        else:
            kwargs["operator_token_was_generated"] = True
        if (raw := get("WORLD_BASE_URL")) is not None:
            kwargs["world_base_url"] = raw.strip()
        if (raw := get("WORLD_HTTP_TIMEOUT_S")) is not None:
            try:
                kwargs["world_http_timeout_s"] = parse_float("WORLD_HTTP_TIMEOUT_S", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("CONSOLE_HOST")) is not None:
            kwargs["host"] = raw.strip()
        if (raw := get("CONSOLE_PORT")) is not None:
            try:
                kwargs["port"] = parse_int("CONSOLE_PORT", raw)
            except ValueError as e:
                errors.append(str(e))

        return cls(**kwargs), errors  # type: ignore[arg-type]

    @classmethod
    def load(cls, env_path: Path | None = None) -> ConsoleSettings:
        """Parse env/.env only, no semantic validation. Raises ConfigError
        aggregating every parse error."""
        settings, errors = cls.build_from_env(read_env_file(env_path))
        if errors:
            raise ConfigError(errors)
        return settings

    @classmethod
    def load_validated(cls, env_path: Path | None = None) -> ConsoleSettings:
        """Boot entry point: parse AND validate, one aggregated report."""
        settings, errors = cls.build_from_env(read_env_file(env_path))
        errors.extend(settings.validate())
        if errors:
            raise ConfigError(errors)
        return settings
