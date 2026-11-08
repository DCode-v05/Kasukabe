"""Settings loader - declarative, fully validated, zero external dependencies.

Every setting is declared exactly once, on its dataclass field, via the
``setting()`` helper: typed default, env var name, and semantic check all
live together. ``load()`` and ``validate()`` are generic loops over
``dataclasses.fields(Settings)`` - there is no second table to keep in sync.

Three entry points:

- ``Settings.load()``       - parse env/.env only. Raises ``ConfigError``
                              aggregating ALL parse errors. Semantically
                              lenient by design: the module-level CORS read
                              in server.py, the per-request /health/deep
                              load, and tests all rely on being able to
                              build a Settings without a full valid config.
- ``Settings.validate()``   - full per-field + cross-field semantic checks;
                              returns the complete list of errors.
- ``Settings.load_validated()`` - the boot entry point: parse + validate,
                              raising ONE ConfigError carrying every parse
                              and semantic error together. Boot hard-fails
                              on any error; nothing partial ever serves.

Model defaults are NOT here. Herald owns them (src/herald/defaults.py); the
model fields below default to empty string, meaning "use the herald launch
default". Resolved model ids (override-else-default) are validated against
the citadel-synced catalog right after the first catalog sync
(src/herald/model_validation.py).
"""

from __future__ import annotations

import base64
import os
import typing
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

Checker = Callable[[str, Any], "str | None"]

SANDBOX_IDLE_TTL_DEFAULT_S = 43200


class ConfigError(ValueError):
    """Aggregated configuration failure - carries every error at once.

    One report per boot attempt: a bad MAX_TOKENS and a bad CHANNEL_MAX both
    appear in the same exception instead of surfacing one restart at a time.
    Subclasses ValueError: callers that treated a single bad value as a
    ValueError (tests, defensive except blocks) keep working.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(
            f"invalid engine configuration ({len(self.errors)} error(s)):\n  - {joined}"
        )


def parse_bool(name: str, value: str) -> bool:
    """Strict true/false parser for boolean feature flags.

    Accepts only ``"true"`` or ``"false"`` (case-insensitive). Rejects
    ``1``/``0``, ``yes``/``no``, ``on``/``off``, and any other form
    with a clear ``ValueError`` at load time. Keeps flag semantics
    unambiguous so config mistakes fail fast instead of silently
    defaulting to ``False``.
    """
    v = value.strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    raise ValueError(
        f"{name}={value!r} - feature flags must be 'true' or 'false' "
        f"(case-insensitive); numeric 1/0 and yes/no are not accepted"
    )


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


def parse_csv(name: str, value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def all_checks(*checks: Checker) -> Checker:
    def run(name: str, value: Any) -> str | None:
        for check in checks:
            msg = check(name, value)
            if msg is not None:
                return msg
        return None

    return run


def require_nonempty(name: str, value: Any) -> str | None:
    if not value:
        return f"{name} is required - set it in .env or as an environment variable"
    return None


def check_url(name: str, value: Any) -> str | None:
    """http(s) URL shape. Empty passes (pair with require_nonempty to force)."""
    if not value:
        return None
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(value)
    except ValueError:
        return f"{name}={value!r} - malformed URL"
    if parts.scheme not in ("http", "https") or not parts.netloc or " " in value:
        return f"{name}={value!r} - must be an http(s) URL like https://host[:port]"
    return None


def check_positive_number(name: str, value: Any) -> str | None:
    if value <= 0:
        return f"{name}={value!r} - must be greater than 0"
    return None


def check_nonnegative_number(name: str, value: Any) -> str | None:
    if value < 0:
        return f"{name}={value!r} - must be 0 or greater"
    return None


def check_enum(*allowed: str) -> Checker:
    def run(name: str, value: Any) -> str | None:
        if value not in allowed:
            listed = ", ".join(repr(a) for a in allowed)
            return f"{name}={value!r} - must be one of {listed}"
        return None

    return run


def check_fernet_key(name: str, value: Any) -> str | None:
    """Empty OK; else urlsafe base64 decoding to exactly 32 bytes."""
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value)
    except Exception:
        raw = b""
    if len(raw) != 32:
        return (
            f"{name} - must be a urlsafe-base64 encoded 32-byte Fernet key; "
            f'generate one with: python -c "from cryptography.fernet import '
            f'Fernet; print(Fernet.generate_key().decode())"'
        )
    return None


def check_origins(name: str, value: Any) -> str | None:
    """Each CORS origin: '*' or scheme://host[:port] with no path/query."""
    from urllib.parse import urlsplit

    for origin in value:
        if origin == "*":
            continue
        try:
            parts = urlsplit(origin)
        except ValueError:
            return f"{name}: {origin!r} - malformed origin"
        if (
            parts.scheme not in ("http", "https")
            or not parts.netloc
            or parts.path
            or parts.query
            or parts.fragment
        ):
            return (
                f"{name}: {origin!r} - each origin must be '*' or scheme://host[:port] with no path"
            )
    return None


def check_absolute_paths(name: str, value: Any) -> str | None:
    for root in value:
        if not root.startswith("/"):
            return f"{name}: {root!r} - each entry must be an absolute path"
    return None


def setting(
    default: Any = MISSING,
    *,
    env: str | None = None,
    check: Checker | None = None,
    factory: Callable[[], Any] | None = None,
) -> Any:
    """Declare one Settings field: typed default + env var + semantic check.

    ``env=None`` marks a code-only field (never read from the environment).
    ``factory`` is for mutable defaults (lists); ``default`` is ignored then.
    """
    meta = {"env": env, "check": check}
    if factory is not None:
        return field(default_factory=factory, metadata=meta)
    return field(default=default, metadata=meta)


ENVLESS_FIELDS = frozenset({"deny_rules", "permission_store_user_path"})


@dataclass
class Settings:
    cdt_url: str = setting("", env="CDT_URL", check=all_checks(require_nonempty, check_url))
    cdt_key: str = setting("", env="CDT_KEY", check=require_nonempty)
    engine_id: str = setting("", env="ENGINE_ID")

    llm_model: str = setting("", env="LLM_MODEL")
    max_tokens: int = setting(4096, env="MAX_TOKENS", check=check_positive_number)
    planner_model: str = setting("", env="PLANNER_MODEL")
    light_model: str = setting("", env="LIGHT_MODEL")
    dispatch_model: str = setting("", env="DISPATCH_MODEL")
    dispatch_model_alternative: str = setting("", env="DISPATCH_MODEL_ALTERNATIVE")
    fallback_model: str = setting("", env="FALLBACK_MODEL")
    persistent_retry: bool = setting(False, env="PERSISTENT_RETRY")

    september_home: str = setting("", env="SEPTEMBER_HOME")
    sqlite_db_path: str = setting("", env="SQLITE_DB_PATH")
    catalog_dir: str = setting("", env="CATALOG_DIR")
    migrations_dir: str = setting("", env="MIGRATIONS_DIR")
    ad_encryption_key: str = setting("", env="AD_ENCRYPTION_KEY", check=check_fernet_key)
    cors_origins: list[str] = setting(env="CORS_ORIGINS", factory=list, check=check_origins)
    oauth_redirect_base_url: str = setting("", env="OAUTH_REDIRECT_BASE_URL", check=check_url)

    channel_poll_default_s: float = setting(
        2.0, env="CHANNEL_POLL_DEFAULT_S", check=check_positive_number
    )
    channel_grace_s: float = setting(30.0, env="CHANNEL_GRACE_S", check=check_nonnegative_number)
    channel_max: int = setting(256, env="CHANNEL_MAX", check=check_positive_number)

    catalog_resync_interval_s: int = setting(
        300, env="CATALOG_RESYNC_INTERVAL_S", check=check_positive_number
    )
    global_cache_ttl: str = setting("1h", env="GLOBAL_CACHE_TTL", check=check_enum("5m", "1h"))

    lc_batch_interval_hours: float = setting(
        6.0, env="LC_BATCH_INTERVAL_HOURS", check=check_positive_number
    )
    lc_model: str = setting("", env="LC_MODEL")

    embedding_model: str = setting("", env="EMBEDDING_MODEL")
    embedding_dim: int = setting(0, env="EMBEDDING_DIM", check=check_nonnegative_number)

    asset_directory_sandboxed: bool = setting(True, env="ASSET_DIRECTORY_SANDBOXED")
    deny_rules: list[dict[str, str]] = setting(factory=list)
    allowed_roots: list[str] = setting(
        env="ALLOWED_ROOTS", factory=list, check=check_absolute_paths
    )
    permission_store_user_path: str = setting("", check=require_nonempty)
    subprocess_scrub_mode: str = setting(
        "default", env="SUBPROCESS_SCRUB_MODE", check=check_enum("off", "default", "strict")
    )

    token_budget_enabled: bool = setting(False, env="TOKEN_BUDGET_ENABLED")
    stop_hooks_enabled: bool = setting(True, env="STOP_HOOKS_ENABLED")
    pre_tool_hooks_enabled: bool = setting(True, env="PRE_TOOL_HOOKS_ENABLED")
    post_tool_hooks_enabled: bool = setting(True, env="POST_TOOL_HOOKS_ENABLED")
    hooks_settings_load_enabled: bool = setting(True, env="HOOKS_SETTINGS_LOAD_ENABLED")
    media_error_recovery_enabled: bool = setting(False, env="MEDIA_ERROR_RECOVERY_ENABLED")
    media_presend_validation_enabled: bool = setting(False, env="MEDIA_PRESEND_VALIDATION_ENABLED")
    content_ingestion_enabled: bool = setting(False, env="CONTENT_INGESTION_ENABLED")
    media_image_resize_enabled: bool = setting(False, env="MEDIA_IMAGE_RESIZE_ENABLED")
    enable_sigint_interrupt: bool = setting(False, env="ENABLE_SIGINT_INTERRUPT")
    query_chain_tracking_enabled: bool = setting(False, env="QUERY_CHAIN_TRACKING_ENABLED")
    reactive_compact_enabled: bool = setting(True, env="REACTIVE_COMPACT_ENABLED")
    cache_hit_monitor_enabled: bool = setting(True, env="CACHE_HIT_MONITOR_ENABLED")
    conformance_mode: bool = setting(False, env="CONFORMANCE_MODE")

    activity_idle_timeout_s: int = setting(
        300, env="ACTIVITY_IDLE_TIMEOUT_S", check=check_positive_number
    )
    activity_away_timeout_s: int = setting(
        1800, env="ACTIVITY_AWAY_TIMEOUT_S", check=check_positive_number
    )
    activity_disconnect_timeout_s: int = setting(
        3600, env="ACTIVITY_DISCONNECT_TIMEOUT_S", check=check_positive_number
    )
    sandbox_idle_ttl_s: int = setting(
        SANDBOX_IDLE_TTL_DEFAULT_S, env="SANDBOX_IDLE_TTL_S", check=check_positive_number
    )

    september_registry_url: str = setting("", env="SEPTEMBER_REGISTRY_URL", check=check_url)
    september_client_metadata_url: str = setting(
        "", env="SEPTEMBER_CLIENT_METADATA_URL", check=check_url
    )
    asset_log_retention_days: int = setting(
        30, env="ASSET_LOG_RETENTION_DAYS", check=check_nonnegative_number
    )


    @classmethod
    def load(cls, env_path: Path | None = None) -> "Settings":
        """Parse settings from environment variables and .env.

        Environment variables take precedence over .env file values.
        Raises ``ConfigError`` listing EVERY unparseable value (no
        first-error-wins). Performs no semantic validation - see
        ``load_validated`` for the boot entry point.
        """
        settings, errors = cls.build_from_env(read_env_file(env_path))
        if errors:
            raise ConfigError(errors)
        return settings

    @classmethod
    def build_from_env(cls, env_vars: dict[str, str]) -> "tuple[Settings, list[str]]":
        """Build a Settings from an env map, collecting all parse errors.

        A field whose raw value fails to parse keeps its typed default so
        semantic validation can still run over the rest of the object; the
        parse error is recorded and reported alongside.
        """
        hints = typing.get_type_hints(cls)
        errors: list[str] = []
        kwargs: dict[str, Any] = {}

        for f in fields(cls):
            env_name = f.metadata.get("env") if f.metadata else None
            if not env_name:
                continue
            raw = os.environ.get(env_name, env_vars.get(env_name))
            if raw is None:
                continue
            hint = hints.get(f.name)
            try:
                if hint is bool:
                    kwargs[f.name] = parse_bool(env_name, raw)
                elif hint is int:
                    kwargs[f.name] = parse_int(env_name, raw)
                elif hint is float:
                    kwargs[f.name] = parse_float(env_name, raw)
                elif hint == list[str]:
                    kwargs[f.name] = parse_csv(env_name, raw)
                else:
                    kwargs[f.name] = raw.strip()
            except ValueError as e:
                errors.append(str(e))

        return cls(**kwargs), errors

    @classmethod
    def load_validated(cls, env_path: Path | None = None) -> "Settings":
        """Boot entry point: parse AND validate, one aggregated report.

        Raises ``ConfigError`` carrying every parse error and every
        semantic error together, so an operator fixes the whole config in
        one pass instead of one restart per mistake. Deliberately does not
        delegate through ``cls.load`` (tests monkeypatch ``load``; the boot
        path must stay independent of that seam).
        """
        settings, errors = cls.build_from_env(read_env_file(env_path))
        errors.extend(settings.validate())
        if errors:
            raise ConfigError(errors)
        return settings


    def __post_init__(self) -> None:
        """Fill path fields left empty from SEPTEMBER_HOME.

        An explicit value always wins: the root supplies defaults, it does not
        override an operator's choice. Resolution errors surface here rather
        than at first write, so a bad root fails at construction.
        """
        from src.infra.september_home import september_paths, resolve_september_home

        root = resolve_september_home(self.september_home or None)
        paths = september_paths(root)

        self.september_home = str(root)
        if not self.sqlite_db_path:
            self.sqlite_db_path = str(paths["db"])
        if not self.permission_store_user_path:
            self.permission_store_user_path = str(paths["config"] / "permissions.json")


    def validate(self) -> list[str]:
        """Full semantic validation. Returns ALL errors (empty = valid)."""
        errors: list[str] = []

        for f in fields(self):
            check = f.metadata.get("check") if f.metadata else None
            if check is None:
                continue
            name = (f.metadata.get("env") if f.metadata else None) or f.name
            msg = check(name, getattr(self, f.name))
            if msg is not None:
                errors.append(msg)

        if not (
            self.activity_idle_timeout_s
            < self.activity_away_timeout_s
            < self.activity_disconnect_timeout_s
        ):
            errors.append(
                f"activity timeouts must be strictly ordered: "
                f"ACTIVITY_IDLE_TIMEOUT_S ({self.activity_idle_timeout_s}) < "
                f"ACTIVITY_AWAY_TIMEOUT_S ({self.activity_away_timeout_s}) < "
                f"ACTIVITY_DISCONNECT_TIMEOUT_S ({self.activity_disconnect_timeout_s})"
            )
        if self.embedding_model and self.embedding_dim <= 0:
            errors.append(
                f"EMBEDDING_MODEL={self.embedding_model!r} is overridden but "
                f"EMBEDDING_DIM is {self.embedding_dim} - an override must state "
                f"its dimension (the herald default dim only applies to the "
                f"herald default model)"
            )
        if self.embedding_dim > 0 and not self.embedding_model:
            errors.append(
                f"EMBEDDING_DIM={self.embedding_dim} is set but EMBEDDING_MODEL "
                f"is not - a dimension override only applies to a model override; "
                f"set EMBEDDING_MODEL too or leave EMBEDDING_DIM unset to use the "
                f"herald default model and its dimension"
            )
        return errors


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
                key, sep, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    return env_vars
