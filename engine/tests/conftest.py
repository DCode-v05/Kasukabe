"""Test session bootstrap and shared pytest fixtures.

Two responsibilities:
1. Detect a test environment that cannot load seccomp BPF filters and,
   when detected, monkey-patch ``build_bpf_for_sandbox`` to return None.
2. Expose a shared ``db`` fixture that materializes an in-memory SqlitePool
   with all migrations applied, for any test that needs a real SQLite
   backend. Individual test files can still define a local ``db`` fixture
   to override the shared one (pytest's closest-scope-wins rule applies).

Why this exists
---------------
Production runs on Linux with native seccomp support. The test container
sometimes runs on a developer's Apple Silicon Mac via Docker for Mac,
forced to ``platform: linux/amd64`` so the x86_64 BPF syscall numbers
are exercised. Under that QEMU translation layer, ``prctl(PR_SET_SECCOMP)``
returns EINVAL — the kernel-userspace metadata reports seccomp support
(``/proc/sys/kernel/seccomp/actions_avail`` is populated) but the
syscall path itself isn't implemented in the QEMU emulation.

The fix is test-only and one place: probe at session start with a
forked child (so the parent's seccomp state is untouched), and if
loading fails, patch ``build_bpf_for_sandbox`` to return None. The
sandbox executor's existing fall-through path then invokes bwrap
without ``--seccomp`` and the bwrap+landlock layers still provide
isolation. Real Linux x86_64 (production and CI) detects native
support and runs the full seccomp path unchanged.

No source code in ``src/`` is modified. The probe is host-agnostic
— it doesn't sniff Mac/Windows/Linux strings; it just checks whether
the syscall actually works.
"""

from __future__ import annotations

import os


def can_load_seccomp_bpf() -> bool:
    """Probe whether ``prctl(PR_SET_SECCOMP, MODE_FILTER, ...)`` works.

    Forks a child that tries to load a noop ALLOW filter and exits
    with status 0 on success. The parent observes the exit status and
    is itself unaffected — seccomp filters are inherited only by
    descendants of the process that installed them, so loading in a
    short-lived child leaves the test runner clean.
    """
    # Windows has no os.fork at all, so this is an AttributeError rather
    # than the OSError the handler below was written for - and an
    # uncaught AttributeError at import time takes the whole engine suite
    # down before collection, on every developer machine that is not
    # Linux. Answering "no seccomp here" is the correct answer for a
    # platform that has no seccomp; the branch below then drops --seccomp
    # from the bwrap args exactly as it does on a Linux box without it.
    if not hasattr(os, "fork"):
        return False

    try:
        pid = os.fork()
    except OSError:
        return False

    if pid == 0:
        # Child: try a noop seccomp BPF load.
        try:
            import ctypes
            import struct

            libc = ctypes.CDLL("libc.so.6", use_errno=True)

            # PR_SET_NO_NEW_PRIVS = 38 — required before SECCOMP_MODE_FILTER.
            if libc.prctl(38, 1, 0, 0, 0) != 0:
                os._exit(1)

            # Minimal BPF program: a single instruction that returns
            # SECCOMP_RET_ALLOW (0x7FFF_0000). Allows every syscall, so
            # the child can still _exit cleanly.
            BPF_RET_K = 0x06
            SECCOMP_RET_ALLOW = 0x7FFF_0000
            instr = struct.pack("<HBBI", BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW)
            buf = ctypes.create_string_buffer(instr)

            class sock_fprog(ctypes.Structure):
                _fields_ = [
                    ("len", ctypes.c_ushort),
                    ("filter", ctypes.c_void_p),
                ]

            fprog = sock_fprog(len=1, filter=ctypes.addressof(buf))

            # PR_SET_SECCOMP = 22, SECCOMP_MODE_FILTER = 2
            ret = libc.prctl(22, 2, ctypes.byref(fprog), 0, 0)
            os._exit(0 if ret == 0 else 1)
        except Exception:
            os._exit(1)

    # Parent: wait for the child and read its exit status.
    _, status = os.waitpid(pid, 0)
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


if not can_load_seccomp_bpf():
    # Patch the sandbox executor's bwrap-arg builder to drop the
    # ``seccomp_fd`` parameter. The result is that bwrap is invoked
    # without ``--seccomp`` and the bwrap mount/network/PID namespace
    # and Landlock filesystem ACL still apply. ``build_bpf_for_sandbox``
    # and ``write_bpf_file`` are NOT patched — their unit tests still
    # exercise the real implementations.
    import src.sandbox.executor as executor_mod

    original_build_bwrap_args = executor_mod.build_bwrap_args

    def patched_build_bwrap_args(*args, **kwargs):
        kwargs["seccomp_fd"] = None
        return original_build_bwrap_args(*args, **kwargs)

    executor_mod.build_bwrap_args = patched_build_bwrap_args


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------
# Tests that need a SQLite-backed memory store can use this `db` fixture
# instead of hand-rolling the same SqlitePool(:memory:) + migrations boilerplate.
# Existing test files that define a local `db` fixture with the same name
# take precedence automatically — this is an additive convenience.

import pytest  # noqa: E402

try:
    import pytest_asyncio  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover - the message IS the fix
    # EVAL-19. `uv sync` installs `[project.dependencies]` and NOT
    # `[project.optional-dependencies].dev`, which is where pytest,
    # pytest-asyncio and the rest of the test tooling live. So
    # `cd engine && uv run pytest` fails here, at an import inside a
    # conftest, with a traceback that says "no module named
    # pytest_asyncio" and nothing about what to do.
    #
    # `STAKES_TIME_COMPOUNDING.md` section 7 reports "159 passed" without
    # naming the command that produced it, so a reader who cannot
    # reproduce it has no way to tell a broken checkout from a missing
    # flag.
    #
    # Raised rather than skipped: a suite that cannot load its own
    # fixtures must not report success.
    raise ModuleNotFoundError(
        "the engine's test tooling is not installed. `uv sync` installs "
        "only [project.dependencies]; the test extras are optional. "
        "Run: cd engine && uv sync --extra dev && uv run pytest "
        f"(original error: {exc})"
    ) from exc

from pathlib import Path  # noqa: E402


@pytest.fixture(autouse=True)
def reset_request_rate_limiter():
    """Clear the process-global request rate limiter before each test.

    server.check_rate_limit uses a module-level sliding-window deque
    (request_timestamps, 60 req / 60 s) keyed on monotonic time and shared
    across the whole process. Without a per-test reset the requests one test
    makes count against the next test's window, so a request-heavy test can
    429 a later, unrelated one (the asset DCR E2E tests pushed the window over
    60 and tripped test_enable_pause / test_health_deep). Autouse so every test
    starts from an empty window; no test depends on the limiter accumulating
    across tests.
    """
    from src import server as server_mod

    server_mod.request_timestamps.clear()
    yield


@pytest.fixture(autouse=True)
def undo_what_booting_the_sim_does_to_the_process():
    """Put back the process state `boot_sim_engine` deliberately changes.

    `sim/config.py`'s `raw_process_env` says it plainly: "build_settings()
    applies this dict to the real process environment". Booting the sim sets
    DISABLE_AUTO_COMPACT=1, FORK_SUBAGENT=false and
    FEATURE_TOOL_USE_SUMMARY=false in `os.environ`, and
    `disable_tool_use_summary()` goes further and sets
    `tool_use_summary.ENABLED = False` on the already-imported module -
    for the documented reason that env-var timing is a race it refuses to
    rely on.

    All of that is right for a real sim process, which boots once and owns
    the process. Inside pytest, ten test files boot the sim and none of them
    put it back, so whichever ran first silently reconfigured every test
    after it.

    That is what made `test_compaction_orchestrator.py` a coin flip:
    `DISABLE_AUTO_COMPACT=1` left behind by a sim boot turns compaction off
    entirely, so four tests that assert `strategy == "session_memory"` get
    `"none"` instead. They pass 238/238 run alone and fail under any
    selection that puts a sim-booting test ahead of them - and the suite
    calls itself green either way, depending on the subset you happen to
    run.

    Autouse, because the contamination is not the booting test's fault in
    any way it could be expected to notice, and a fix each of the ten had to
    opt into is a fix that would be missing from the eleventh.
    """
    import os

    watched = ("DISABLE_AUTO_COMPACT", "FORK_SUBAGENT", "FEATURE_TOOL_USE_SUMMARY")
    before = {key: os.environ.get(key) for key in watched}

    enabled_before = None
    try:
        from src.engine_core import tool_use_summary

        enabled_before = tool_use_summary.ENABLED
    except ImportError:  # not loaded yet - nothing to restore
        tool_use_summary = None  # type: ignore[assignment]

    yield

    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    if tool_use_summary is not None and enabled_before is not None:
        tool_use_summary.ENABLED = enabled_before


@pytest_asyncio.fixture
async def db():
    from src.infra.sqlite_pool import SqlitePool

    pool = SqlitePool(":memory:")
    await pool.connect()
    migrations_dir = Path(__file__).parent.parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        await pool.executescript(sql_file.read_text(encoding="utf-8"))
    await pool.commit()
    yield pool
    await pool.close()


@pytest.fixture
def synced_catalog(monkeypatch):
    """Install a canned citadel-synced catalog into catalog_sync's overlay.

    The engine catalog is 100 percent citadel-synced (no static seed), so
    tests that exercise model resolution, cost ranking, or availability
    install this fixture to stand in for a completed sync. Ids, tiers, and
    prices mirror citadel's seed catalog (scripts/seed-catalog.ts there).
    """
    from src.herald import catalog_sync
    from src.herald.catalog import ModelCost, ModelDef

    def entry(mid, provider, tier, ctx, max_out, in_p, out_p, cr=0.0, cc=0.0, vision=True):
        return ModelDef(
            id=mid,
            provider=provider,
            name=mid,
            context_window=ctx,
            max_output_tokens=max_out,
            supports_tools=True,
            supports_vision=vision,
            cost=ModelCost(
                input_per_1m=in_p, output_per_1m=out_p,
                cache_read_per_1m=cr, cache_create_per_1m=cc,
            ),
            tier=tier,
        )

    canned = {
        m.id: m
        for m in [
            entry("claude-opus-4-8", "anthropic", "reasoning", 1_000_000, 128_000, 5.0, 25.0, 0.5, 6.25),
            entry("claude-opus-4-7", "anthropic", "heavy", 1_000_000, 128_000, 5.0, 25.0, 0.5, 6.25),
            entry("claude-sonnet-4-6", "anthropic", "standard", 1_000_000, 64_000, 3.0, 15.0, 0.3, 3.75),
            entry("claude-haiku-4-5", "anthropic", "light", 200_000, 64_000, 1.0, 5.0, 0.1, 1.25),
            entry("claude-haiku-4-5-20251001", "anthropic", "light", 200_000, 64_000, 1.0, 5.0, 0.1, 1.25),
            entry("gemini-3.1-flash-lite", "google", "light", 1_000_000, 65_536, 0.25, 1.5, 0.025, 0.10),
            entry("gemini-3.5-flash", "google", "standard", 1_000_000, 65_536, 1.5, 9.0, 0.15, 0.30),
            entry("gemini-3.1-pro", "google", "reasoning", 1_000_000, 65_536, 2.0, 12.0, 0.20, 1.625),
            entry("gpt-5.4", "openai", "heavy", 400_000, 128_000, 2.5, 15.0, 0.25),
            entry("gpt-5-mini", "openai", "light", 200_000, 16_384, 0.25, 2.0, 0.025),
            entry("gpt-5.4-mini", "openai", "light", 400_000, 128_000, 0.5, 3.0, 0.05),
            entry("text-embedding-3-small", "openai", "embedding", 8_191, 0, 0.02, 0.0, vision=False),
        ]
    }
    monkeypatch.setattr(catalog_sync, "runtime_overlay", canned)
    return canned
