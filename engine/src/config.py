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

# Checker signature: (env-or-field name, parsed value) -> error message or None.
Checker = Callable[[str, Any], "str | None"]

# One canonical sandbox idle TTL default. Also imported by
# src/sandbox/provider.py (its library-level DEFAULT_IDLE_TTL_S) and used as
# this module's field default, so the value exists exactly once. 12 hours,
# matching industry sandbox caches (Codex caches <= 12h).
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


# ── Parsers (type-driven; every message names the env var) ──────────


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


# ── Checkers (composable per-field semantic rules) ──────────────────


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


# ── Field declaration helper ────────────────────────────────────────


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


# Fields that deliberately have no env var. A meta-test asserts this list is
# exact so a new field cannot silently become unreachable from the
# environment.
ENVLESS_FIELDS = frozenset({"deny_rules", "permission_store_user_path"})

# ══════════════════════════════════════════════════════════════════════
# Liveness TTL principle - hardcode the ratios, not vibes.
#
# A liveness TTL is always a multiple of the heartbeat that feeds it:
#   - fast heartbeats (seconds): TTL = 3x the heartbeat
#     (20s heartbeat -> 60s TTL: one missed beat is jitter, two is a
#     warning, three is dead)
#   - slow polls (minutes+): TTL = 24x the poll
#     (10min poll -> 4h TTL: cheap polls deserve wide tolerance so a
#     transient outage does not reap healthy state)
# If you add a liveness TTL to citadel/engine bridging (channel liveness,
# session reaping, catalog staleness), derive it from one of these two
# ratios and say which one in a comment. Mirrored in
# architecture/sections/70_security_deployment.md.
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Settings:
    # ── Citadel gateway ─────────────────────────────────────────────
    # Every LLM call routes through one URL + one token. Engine never holds
    # Anthropic/OpenAI/Gemini keys directly.
    cdt_url: str = setting("", env="CDT_URL", check=all_checks(require_nonempty, check_url))
    # Required; the value is never echoed in error messages (secret).
    cdt_key: str = setting("", env="CDT_KEY", check=require_nonempty)
    # Engine pod identity. Set by the orchestrator (bap-engine, future
    # product-engines) at provision time. Engine emits it in log records
    # and as the X-Engine-Id header on outbound citadel calls so traces
    # are correlatable across the full stack. Empty when running standalone
    # (e.g. local dev without an orchestrator).
    engine_id: str = setting("", env="ENGINE_ID")

    # ── Models ──────────────────────────────────────────────────────
    # HERALD OWNS THE DEFAULTS (src/herald/defaults.py). Empty string means
    # "use the launch default". Non-empty values are overrides; every
    # RESOLVED id is validated against the citadel-synced catalog right
    # after the first sync (src/herald/model_validation.py), so no rule
    # here.
    llm_model: str = setting("", env="LLM_MODEL")
    max_tokens: int = setting(4096, env="MAX_TOKENS", check=check_positive_number)
    planner_model: str = setting("", env="PLANNER_MODEL")
    light_model: str = setting("", env="LIGHT_MODEL")
    # Triage runs on every input, so it is priced per input for the life of
    # the product: it must be cheap, fast, and reliable at structured
    # output, and it never writes anything the user reads. Two are
    # configured - the second is used when the first is unreachable, and
    # they are deliberately from different providers so one provider's
    # outage cannot stop triage. The default pair (and the benchmark that
    # ordered it) lives in the herald defaults block.
    dispatch_model: str = setting("", env="DISPATCH_MODEL")
    dispatch_model_alternative: str = setting("", env="DISPATCH_MODEL_ALTERNATIVE")
    fallback_model: str = setting("", env="FALLBACK_MODEL")
    persistent_retry: bool = setting(False, env="PERSISTENT_RETRY")

    # ── Storage & platform ──────────────────────────────────────────
    # The one root the engine owns. Everything below defaults inside it, so a
    # deployment sets one path instead of a dozen. See infra/september_home.py
    # for resolution rules (a set-but-missing root is fatal, by design).
    september_home: str = setting("", env="SEPTEMBER_HOME")
    # SQLite per-user memory database (the brain). Empty means <home>/brain.sqlite.
    sqlite_db_path: str = setting("", env="SQLITE_DB_PATH")
    # Platform catalog directory (mounted volume, read-only). Empty means
    # the built-in resolution order; existence is bootstrap's concern.
    catalog_dir: str = setting("", env="CATALOG_DIR")
    migrations_dir: str = setting("", env="MIGRATIONS_DIR")
    # Asset Directory encryption key (Fernet, base64-encoded 32 bytes)
    ad_encryption_key: str = setting("", env="AD_ENCRYPTION_KEY", check=check_fernet_key)
    cors_origins: list[str] = setting(env="CORS_ORIGINS", factory=list, check=check_origins)
    oauth_redirect_base_url: str = setting("", env="OAUTH_REDIRECT_BASE_URL", check=check_url)

    # ── Channel subsystem (live-data plane) ─────────────────────────
    # minimum poll interval - protects asset quota
    channel_poll_default_s: float = setting(
        2.0, env="CHANNEL_POLL_DEFAULT_S", check=check_positive_number
    )
    # linger after last stream drops before GC
    channel_grace_s: float = setting(30.0, env="CHANNEL_GRACE_S", check=check_nonnegative_number)
    # cap on concurrently live channels
    channel_max: int = setting(256, env="CHANNEL_MAX", check=check_positive_number)

    # ── Catalog sync (herald) ───────────────────────────────────────
    # How the engine keeps its model catalog fresh from citadel's
    # /v1/models. Poll-only: full re-pull every interval picks up catalog
    # changes (model added/removed/retired, price edits). While the catalog
    # has never synced, catalog_sync retries on an accelerated schedule
    # instead of waiting out this interval.
    catalog_resync_interval_s: int = setting(
        300, env="CATALOG_RESYNC_INTERVAL_S", check=check_positive_number
    )
    # Cache TTL for the global (cross-user) cacheable system-prompt prefix
    # emitted by ConsciousnessManager.build_prompt_blocks. Anthropic charges
    # 2x base for a 1h cache write vs 1.25x for 5m, so a long-lived or
    # multi-turn session amortizes the 1h write while a short one-off
    # context over-pays. Default "1h" preserves prior behavior; set "5m"
    # for ephemeral deploys.
    global_cache_ttl: str = setting("1h", env="GLOBAL_CACHE_TTL", check=check_enum("5m", "1h"))

    # ── Learning Centre ─────────────────────────────────────────────
    # hours between automated batch runs
    lc_batch_interval_hours: float = setting(
        6.0, env="LC_BATCH_INTERVAL_HOURS", check=check_positive_number
    )
    # Batch model default lives in the herald defaults block; the provider
    # label in boot records is derived from the catalog entry.
    lc_model: str = setting("", env="LC_MODEL")

    # ── Embeddings ──────────────────────────────────────────────────
    # Routed through citadel (POST {CDT_URL}/v1/embeddings); citadel holds
    # the credential. Model + dim defaults live in the herald defaults
    # block; 0 means "use the herald default dim".
    embedding_model: str = setting("", env="EMBEDDING_MODEL")
    embedding_dim: int = setting(0, env="EMBEDDING_DIM", check=check_nonnegative_number)

    # ── Permission pipeline ─────────────────────────────────────────
    # when True, AD tools run inside bwrap
    asset_directory_sandboxed: bool = setting(True, env="ASSET_DIRECTORY_SANDBOXED")
    # [{tool, pattern?, reason?}] - code/test-only, no env var
    deny_rules: list[dict[str, str]] = setting(factory=list)
    # path validation: restrict file ops to these dirs
    allowed_roots: list[str] = setting(
        env="ALLOWED_ROOTS", factory=list, check=check_absolute_paths
    )
    # Persistent operator permission grants. Empty means
    # <home>/config/permissions.json, so grants survive a restart wherever the
    # root is mounted. There is deliberately no second, cwd-relative tier: a
    # file the sandboxed agent could create must never be an authority source.
    permission_store_user_path: str = setting("", check=require_nonempty)
    # Subprocess env scrub tier. See src/utility_directory/subprocess_env.py
    # for the threat model and per-tier scrub lists.
    subprocess_scrub_mode: str = setting(
        "default", env="SUBPROCESS_SCRUB_MODE", check=check_enum("off", "default", "strict")
    )

    # ── Feature gates (parse_bool IS the validation) ────────────────
    # TOKEN_BUDGET gate - when False, the token budget continuation loop in
    # agent_loop is completely inert (behavior matches pre-feature main).
    token_budget_enabled: bool = setting(False, env="TOKEN_BUDGET_ENABLED")
    # STOP_HOOKS gate - when False, the stop-hook execution point at turn
    # boundaries in agent_loop is skipped entirely (zero overhead). On by
    # default because zero registered hooks is already a no-op path.
    stop_hooks_enabled: bool = setting(True, env="STOP_HOOKS_ENABLED")
    pre_tool_hooks_enabled: bool = setting(True, env="PRE_TOOL_HOOKS_ENABLED")
    post_tool_hooks_enabled: bool = setting(True, env="POST_TOOL_HOOKS_ENABLED")
    # When False, hooks are NOT auto-loaded from settings files (user,
    # project, local, plugin). Programmatically registered hooks (session,
    # built-in) still work normally. The older inverted env var
    # INTELLIGENCE_ENGINE_DISABLE_HOOK_SETTINGS is still honoured; either
    # lever alone is enough to disable loading.
    hooks_settings_load_enabled: bool = setting(True, env="HOOKS_SETTINGS_LOAD_ENABLED")
    # When True, the agent loop catches media-specific API errors
    # (corrupt/oversized images, too-many-page documents), strips the
    # problematic blocks, and retries with cleaned messages. Separate retry
    # budget from the overflow retry loop.
    media_error_recovery_enabled: bool = setting(False, env="MEDIA_ERROR_RECOVERY_ENABLED")
    # When True, base64-encoded images are validated (size, empty buffer)
    # before sending to the API.
    media_presend_validation_enabled: bool = setting(False, env="MEDIA_PRESEND_VALIDATION_ENABLED")
    # When True, large media sent with execute requests is chunked and
    # stored in ingestion_chunks for tool-based retrieval instead of being
    # injected into context.
    content_ingestion_enabled: bool = setting(False, env="CONTENT_INGESTION_ENABLED")
    # When True, oversized images are resized/compressed via the
    # multi-strategy pipeline (Pillow) before the API call, instead of
    # being stripped entirely.
    media_image_resize_enabled: bool = setting(False, env="MEDIA_IMAGE_RESIZE_ENABLED")
    # When True, bootstrap installs a SIGINT handler that fans interrupts
    # out to every active turn executor via InterruptSource. Keep False in
    # server deployments - uvicorn owns SIGINT for graceful shutdown.
    # Enable in CLI/TUI deployments that run the engine on the main thread.
    enable_sigint_interrupt: bool = setting(False, env="ENABLE_SIGINT_INTERRUPT")
    query_chain_tracking_enabled: bool = setting(False, env="QUERY_CHAIN_TRACKING_ENABLED")
    # When False, context overflow errors from the API surface immediately
    # without attempting recovery. Useful for constrained deploys,
    # reliability testing, and debugging.
    reactive_compact_enabled: bool = setting(True, env="REACTIVE_COMPACT_ENABLED")
    # When True, the agent loop runs a lightweight CacheHitMonitor that
    # fires CacheHitRatioAlert events on low cache ratio or prefix
    # instability. Cache stats logging (always on) is unaffected.
    cache_hit_monitor_enabled: bool = setting(True, env="CACHE_HIT_MONITOR_ENABLED")
    # When True, the herald factory builds a deterministic canned LLM
    # instead of the citadel-routed Provider, so a full /execute turn emits
    # a fixed short block sequence with no network and no citadel call.
    # Used only by the SDK conformance suite. Off in production: the LLM
    # path is byte-identical when this flag is False.
    conformance_mode: bool = setting(False, env="CONFORMANCE_MODE")

    # ── Session activity (ActivityManager) ──────────────────────────
    # Drive the state machine: active -> idle -> away -> disconnected.
    # Cross-field rule: idle < away < disconnect (validated below).
    activity_idle_timeout_s: int = setting(
        300, env="ACTIVITY_IDLE_TIMEOUT_S", check=check_positive_number
    )
    activity_away_timeout_s: int = setting(
        1800, env="ACTIVITY_AWAY_TIMEOUT_S", check=check_positive_number
    )
    activity_disconnect_timeout_s: int = setting(
        3600, env="ACTIVITY_DISCONNECT_TIMEOUT_S", check=check_positive_number
    )
    # Reap the per-user sandbox session after this much inactivity,
    # discarding the ephemeral workspace (the task state is durable
    # elsewhere). A fresh sandbox is re-provisioned on the next use.
    sandbox_idle_ttl_s: int = setting(
        SANDBOX_IDLE_TTL_DEFAULT_S, env="SANDBOX_IDLE_TTL_S", check=check_positive_number
    )

    # ── September registry ──────────────────────────────────────────
    # The dormant "septemberai" source tier. Public metadata endpoint for
    # September's own first-party hosted MCP servers, fetched at read time
    # (never seeded into the DB). Empty disables the tier entirely.
    # Example: "https://registry.september.wtf".
    september_registry_url: str = setting("", env="SEPTEMBER_REGISTRY_URL", check=check_url)
    # Client ID Metadata Document (SEP-991) URL - the engine's own hosted
    # OAuth client identity. When set AND a discovered authorization server
    # advertises client_id_metadata_document_supported, discovery uses this
    # URL as the client_id (public client + PKCE) instead of Dynamic Client
    # Registration. Empty disables CIMD (DCR / pre-registered paths apply).
    september_client_metadata_url: str = setting(
        "", env="SEPTEMBER_CLIENT_METADATA_URL", check=check_url
    )
    # Retention window (days) for the append-only asset logs (execution_log,
    # connection_events). Rows older than this are swept opportunistically
    # on write (throttled) and by the periodic maintenance loop. 0 disables
    # the sweep entirely (unbounded growth).
    asset_log_retention_days: int = setting(
        30, env="ASSET_LOG_RETENTION_DAYS", check=check_nonnegative_number
    )

    # ── Loading ─────────────────────────────────────────────────────

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

    # ── Derived paths ───────────────────────────────────────────────

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

    # ── Validation ──────────────────────────────────────────────────

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

        # Cross-field rules.
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
            # The inverse trap: a dim override with the default model would
            # make embeddings.py silently truncate/pad every real vector to
            # the wrong length (the default model emits a fixed dimension).
            # A dim only means something paired with the model it describes.
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
