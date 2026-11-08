"""Production bootstrapper — boots all engine subsystems.

SQLite + in-memory working memory + file-based catalog.
Single-user engine: one process, one SQLite database.

Usage:
    from src.bootstrap import bootstrap, shutdown, EngineState, BootReport
    from src.config import Settings

    settings = Settings.load()
    state, report = await bootstrap(settings=settings)
    await shutdown(state)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.config import ConfigError, Settings
from src.herald.catalog import get_model
from src.herald.defaults import ModelRole, resolve_model
from src.engine_core.events import EventEmitter
from src.engine_core.blocks import ThreadBlockBuffers, TurnBlockBuffers

logger = logging.getLogger(__name__)


@dataclass
class EngineState:
    """Holds references to every booted subsystem."""

    settings: Settings
    emitter: EventEmitter

    db: Any = None  # SqlitePool
    catalog: Any = None  # Catalog
    adapter: Any = None
    embedding_service: Any = None
    platform_registry: Any = None
    ud_full: Any = None
    ud_meta: Any = None
    ud_write: Any = None
    working_memory: Any = None
    episodic_memory: Any = None
    knowledge_store: Any = None
    social_graph: Any = None
    soul: Any = None
    memory_system: Any = None
    search: Any = None
    utility: Any = None
    tools: Any = None
    sandbox_provider: Any = None  # SandboxProvider (isolation seam) — LocalBwrapProvider today
    sandbox_session: Any = None  # SessionHandle for the per-user-engine sandbox
    consciousness_mgr: Any = None
    coordinator: Any = None
    lc_pipeline: Any = None
    lc_scheduler: Any = None
    activity_manager: Any = None
    activity_policy: Any = None
    light_adapter: Any = None  # fast/cheap LLM for cosmetic jobs (recaps etc)
    # Thread IDs picked up from the conversations table during Step 21b
    # (crash recovery). Lets observability surfaces (RPC list_threads,
    # /health diagnostics) distinguish restored vs freshly-created threads
    # without timestamp heuristics.
    restored_thread_ids: set[str] = field(default_factory=set)
    # Per-thread block buffers + seq counters for the v2 wire: the /execute
    # sink appends every folded block here, and /execute/replay reads one
    # thread's blocks back so ?after=<seq> never leaks across threads.
    block_buffers: "ThreadBlockBuffers" = field(default_factory=lambda: ThreadBlockBuffers())
    turn_buffers: "TurnBlockBuffers" = field(default_factory=lambda: TurnBlockBuffers())

    @property
    def is_operational(self) -> bool:
        return self.coordinator is not None


@dataclass
class BootReport:
    success: bool = False
    subsystems: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    boot_duration_ms: int = 0


def record(report: BootReport, name: str, ok: bool, msg: str = "") -> None:
    report.subsystems[name] = ok
    if ok:
        logger.info("boot.ok: %s%s", name, f" — {msg}" if msg else "")
    else:
        report.errors.append(f"{name}: {msg}" if msg else name)
        logger.error("boot.fail: %s — %s", name, msg)


def record_disabled(report: BootReport, name: str, msg: str = "") -> None:
    """Record a subsystem as intentionally not running.

    Sets ``subsystems[name] = False`` (the subsystem is not active) and
    writes the reason to ``warnings`` rather than ``errors`` — this is a
    design choice or non-fatal external dependency, not a failure.

    Use this for: flag-disabled subsystems, optional external integrations
    that are configured-but-unreachable, etc.  Pairs with the ``errors=[]
    iff status=healthy`` invariant enforced in ``server.py /health``.
    """
    report.subsystems[name] = False
    text = f"{name}: {msg}" if msg else name
    report.warnings.append(text)
    logger.info("boot.disabled: %s%s", name, f" — {msg}" if msg else "")


def warn(report: BootReport, msg: str) -> None:
    report.warnings.append(msg)
    logger.warning("boot.warn: %s", msg)


async def bootstrap(
    settings: Settings,
    ask_user_callback: Any = None,
) -> tuple[EngineState, BootReport]:
    """Boot all engine subsystems."""
    t0 = time.monotonic()
    report = BootReport()
    state = EngineState(settings=settings, emitter=EventEmitter())

    logger.info("Booting engine model=%s", resolve_model(ModelRole.PRIMARY, settings))

    # Materialize the engine root before any subsystem writes into it, so a
    # captured tree has the same shape on every engine rather than only the
    # directories that happened to be used. Individual writers still create
    # their own parents, so this is a guarantee rather than a precondition.
    from src.infra.september_home import ensure_layout

    ensure_layout(settings.september_home)

    # Register OS signal handlers (SIGTERM, SIGHUP, and optionally SIGINT).
    # In Uvicorn server mode, Uvicorn overwrites SIGTERM/SIGINT after this;
    # SIGHUP remains ours (Uvicorn doesn't handle it).  In CLI/TUI mode all
    # three handlers are active.  Safe to call multiple times — idempotent.
    from src.infra.graceful_shutdown import setup_signal_handlers
    from src.infra.cleanup_registry import clear_registry

    clear_registry()  # Reset between bootstrap calls (e.g., in tests)
    sigint_owned_by_interrupt_source = settings.enable_sigint_interrupt
    setup_signal_handlers(register_sigint=not sigint_owned_by_interrupt_source)

    # Install SIGINT → cancel_interruptible fan-out if the deployment
    # opted in.  Kept off by default so uvicorn retains SIGINT for
    # graceful shutdown in server deployments; CLI/TUI flips it on.
    if settings.enable_sigint_interrupt:
        from src.engine_core.interrupt_source import get_interrupt_source

        get_interrupt_source().install()

    # --- 1. Validate settings ---
    # Hard-fail: a config mistake aborts boot with the FULL aggregated
    # report instead of serving degraded. server.py's lifespan lets the
    # ConfigError propagate so uvicorn refuses to start.
    validation_errors = settings.validate()
    if validation_errors:
        for err in validation_errors:
            record(report, "settings", False, err)
        report.boot_duration_ms = int((time.monotonic() - t0) * 1000)
        raise ConfigError(validation_errors)
    record(report, "settings", True, f"model={resolve_model(ModelRole.PRIMARY, settings)}")

    # --- 1b. Subprocess env scrub mode + audit observer ---
    # Secrets remain in os.environ so the parent can do lazy credential reads
    # (LLM adapter, credential store, DB reconnect). Every child-process spawn
    # site calls subprocess_env() for a fresh scrubbed copy. Push the session
    # tier and register an observer that fans each spawn through the emitter.
    # Runs AFTER validation so an invalid SUBPROCESS_SCRUB_MODE surfaces as a
    # friendly error on line above instead of an unhandled ValueError here.
    from src.utility_directory.subprocess_env import (
        register_spawn_observer,
        set_session_mode,
        SubprocessEnvResult,
    )
    from src.engine_core.events import SubprocessSpawnedEvent

    set_session_mode(settings.subprocess_scrub_mode)  # type: ignore[arg-type]
    logger.info("Subprocess env scrub mode: %s", settings.subprocess_scrub_mode)

    def fanout_spawn_event(result: SubprocessEnvResult, spawn_site: str) -> None:
        event = SubprocessSpawnedEvent(
            spawn_site=spawn_site,  # type: ignore[arg-type]
            mode=result.mode,  # type: ignore[arg-type]
            scrubbed_count=result.scrubbed_count,
            has_proxy_env=result.has_proxy_env,
        )
        # emit() is async; schedule on the running loop so the spawn path
        # stays sync. Tests/teardown may have no loop — drop silently.
        try:
            asyncio.get_running_loop().create_task(state.emitter.emit(event))
        except RuntimeError:
            pass

    register_spawn_observer(fanout_spawn_event)

    # --- 2. SQLite ---
    try:
        from src.infra.sqlite_pool import SqlitePool

        state.db = SqlitePool(settings.sqlite_db_path)
        await state.db.connect()
        await state.db.health_check()
        vec_status = "vec loaded" if state.db.vec_available else "vec unavailable"
        record(report, "sqlite", True, vec_status)
        # Register LIFO cleanup — closes last (after coordinator + scheduler)
        from src.infra.cleanup_registry import register_cleanup

        db_ref = state.db
        register_cleanup("sqlite", lambda: db_ref.close())
    except Exception as e:
        record(report, "sqlite", False, str(e))
        state.db = None

    # --- 2b. Run migrations ---
    if state.db is not None:
        try:
            from src.infra.migrations import run_migrations

            applied = await run_migrations(state.db, settings.migrations_dir or None)
            if applied:
                logger.info("Migrations applied: %s", applied)
            record(report, "migrations", True, f"{len(applied)} new" if applied else "up to date")
        except Exception as e:
            record(report, "migrations", False, str(e))

    # --- 2c. Raven (observability) ---
    # Needs SQLite + obs_* tables from migration 013.  Initialised here so
    # later subsystems (ActivityPolicy, LLM usage hook) can track events.
    if state.db is not None:
        try:
            from src.raven import api as raven_api

            raven_api.init(state.db)
            raven_api.start_retention()
            from src.infra.cleanup_registry import register_cleanup

            register_cleanup("raven_retention", lambda: raven_api.stop_retention())
            record(report, "raven", True)
        except Exception as e:
            record(report, "raven", False, str(e))

    # --- 3. Catalog ---
    try:
        from src.catalog import load_catalog
        from pathlib import Path
        from src.package import PACKAGE_ROOT

        # Resolution order:
        #   1. CATALOG_DIR env (operator override)
        #   2. /catalog (Dockerfile VOLUME mount — prod default)
        #   3. PACKAGE_ROOT/catalog (dev tree, when running engine outside docker)
        if settings.catalog_dir:
            catalog_dir = settings.catalog_dir
        elif Path("/catalog").exists():
            catalog_dir = "/catalog"
        else:
            catalog_dir = str(PACKAGE_ROOT / "catalog")
        user_data_dir = str(Path(settings.sqlite_db_path).parent)
        state.catalog = load_catalog(
            catalog_dir,
            user_data_dir=user_data_dir,
        )
        record(
            report,
            "catalog",
            True,
            f"{len(state.catalog.agents)}a {len(state.catalog.skills)}s {len(state.catalog.tools)}t",
        )
    except Exception as e:
        record(report, "catalog", False, str(e))

    # --- 3b. Orphan cleanup (user state referencing removed catalog entries) ---
    if state.db is not None and state.catalog is not None:
        try:
            catalog_skill_names = state.catalog.all_skill_names()
            catalog_agent_names = state.catalog.all_agent_names()
            catalog_tool_names = state.catalog.all_tool_names()
            if catalog_skill_names:
                placeholders = ",".join("?" * len(catalog_skill_names))
                await state.db.execute(
                    f"DELETE FROM user_skills WHERE platform_skill_name IS NOT NULL AND platform_skill_name NOT IN ({placeholders})",
                    tuple(catalog_skill_names),
                )
            if catalog_agent_names:
                placeholders = ",".join("?" * len(catalog_agent_names))
                await state.db.execute(
                    f"DELETE FROM user_agents WHERE platform_agent_name IS NOT NULL AND platform_agent_name NOT IN ({placeholders})",
                    tuple(catalog_agent_names),
                )
            if catalog_tool_names:
                placeholders = ",".join("?" * len(catalog_tool_names))
                await state.db.execute(
                    f"DELETE FROM user_tools WHERE platform_tool_name NOT IN ({placeholders})",
                    tuple(catalog_tool_names),
                )
            await state.db.commit()
            record(report, "orphan_cleanup", True)
        except Exception as e:
            record(report, "orphan_cleanup", False, str(e))

    # --- 4. LLM adapter ---
    try:
        from src.herald import create_llm

        state.adapter = create_llm(settings)
        record(report, "llm_adapter", True, f"citadel/{resolve_model(ModelRole.PRIMARY, settings)}")
    except Exception as e:
        record(report, "llm_adapter", False, str(e))

    # --- 4-gw. Herald gateway config + cache tracker ---
    # Wires the gateway config object and cache tracker into herald_api. The
    # per-turn model router (engine_core/thread.py) and cache-hit accounting
    # (raven/api.py) both gate on herald_api.get_config_obj()/get_cache_tracker()
    # being non-None. Without this call the globals stay None, so the router
    # silently falls back to the primary adapter on every turn and cache
    # hit/miss is never recorded. Uses the default GatewayConfig (profile="auto").
    if state.db is not None:
        try:
            from src.herald import herald_api

            herald_api.init(state.db)
            gw = herald_api.get_config_obj()
            record(report, "herald_gateway", True, f"profile={gw.routing.profile}")
        except Exception as e:
            record(report, "herald_gateway", False, str(e))

    # --- 4a. Dynamic catalog sync from citadel ---
    # Pull /v1/models for our pool + start the periodic poll. The catalog
    # is 100 percent citadel-synced (no static seed). Sync failure is
    # non-fatal: the engine boots degraded with an empty catalog and
    # catalog_sync's accelerated retry self-heals it.
    catalog_synced = False
    try:
        from src.herald import catalog_sync

        catalog_synced = await catalog_sync.initialize(settings)
        record(
            report,
            "catalog_sync",
            catalog_synced,
            "synced" if catalog_synced else "degraded - empty catalog, accelerated retry active",
        )
    except Exception as e:
        record(report, "catalog_sync", False, str(e))

    # --- 4a2. Model validation against the synced catalog ---
    # Every RESOLVED model id (env override else herald default) is checked
    # here, before any adapter construction below, so a model typo aborts
    # boot with a clear report instead of surfacing as per-request citadel
    # 4xxs. Degraded catalog -> warnings only (cannot distinguish missing
    # from unsyncable).
    from src.herald.model_validation import log_findings, validate_resolved_models

    model_fatal, model_warnings = validate_resolved_models(settings, synced=catalog_synced)
    log_findings(model_fatal, model_warnings)
    for finding in model_warnings:
        warn(report, finding)
    if model_fatal:
        for finding in model_fatal:
            record(report, "model_validation", False, finding)
        report.boot_duration_ms = int((time.monotonic() - t0) * 1000)
        raise ConfigError(model_fatal)
    record(
        report,
        "model_validation",
        True,
        "synced" if catalog_synced else "degraded (warn-only)",
    )

    # --- 4b. Light LLM adapter (cheap tier for cosmetic jobs) ---
    # Used by services that don't need the primary model's horsepower —
    # reconnect recaps, small one-shot classifications. Falls back to the
    # primary adapter if resolution fails so callers never see None.
    try:
        from src.herald import create_llm
        from src.herald.catalog import ModelTier, resolve_model_for_tier

        light_def = resolve_model_for_tier(ModelTier.LIGHT, settings)
        state.light_adapter = create_llm(settings, model_id=light_def.id)
        record(report, "light_adapter", True, light_def.id)
    except Exception as e:
        state.light_adapter = state.adapter  # safe fallback
        record(report, "light_adapter", False, f"fell back to primary: {e}")

    # --- 5. Embedding service ---
    try:
        from src.memory.embeddings import get_embedding_service

        state.embedding_service = get_embedding_service(settings)
        mode = "real" if state.embedding_service.is_real else "zero-vector"
        record(report, "embeddings", True, mode)
    except Exception as e:
        record(report, "embeddings", False, str(e))

    # --- 5b. Embed skill descriptions for intent matching ---
    if state.catalog is not None and state.embedding_service is not None:
        try:
            await state.catalog.embed_all_descriptions(state.embedding_service)
            embedded = len(state.catalog.skill_embeddings)
            record(report, "skill_embeddings", True, f"{embedded} skills embedded")
        except Exception as e:
            record(report, "skill_embeddings", False, str(e))

    if state.db is not None:
        # --- 7. Utility Directory ---
        try:
            from src.utility_directory.platform_registry import PlatformRegistry
            from src.utility_directory.ud_full import UDFull
            from src.utility_directory.ud_meta import UDMeta
            from src.utility_directory.ud_write import UDWrite

            state.platform_registry = PlatformRegistry(state.db, state.catalog)
            state.ud_full = UDFull(state.db, state.catalog)
            state.ud_meta = UDMeta(state.db, state.catalog)
            state.ud_write = UDWrite(state.db)
            record(report, "utility_directory", True)
            record(report, "platform_registry", state.platform_registry is not None)
            record(report, "ud_full", state.ud_full is not None)
            record(report, "ud_meta", state.ud_meta is not None)
            record(report, "ud_write", state.ud_write is not None)
        except Exception as e:
            record(report, "utility_directory", False, str(e))
            record(report, "platform_registry", False, "utility_directory init failed")
            record(report, "ud_full", False, "utility_directory init failed")
            record(report, "ud_meta", False, "utility_directory init failed")
            record(report, "ud_write", False, "utility_directory init failed")
    else:
        warn(report, "Skipping AD + UD (no SQLite)")

    # --- 8. Working Memory (SQLite default, in-memory via USE_INMEMORY_WM=true) ---
    try:
        from src.memory.memory_factory import create_working_memory, USE_INMEMORY_WM

        state.working_memory = create_working_memory(
            session_id="",
            embedder=state.embedding_service,
            db=state.db,
        )
        backend = "in-memory" if USE_INMEMORY_WM else "sqlite"
        record(report, "working_memory", True, backend)
    except Exception as e:
        record(report, "working_memory", False, str(e))

    # --- 9-11. SQLite-backed memory subsystems ---
    if state.db is not None:
        try:
            from src.memory.episodic_memory import EpisodicMemory

            state.episodic_memory = EpisodicMemory(state.db, state.embedding_service)
            record(report, "episodic_memory", True)
        except Exception as e:
            record(report, "episodic_memory", False, str(e))

        try:
            from src.memory.knowledge_store import KnowledgeStore

            state.knowledge_store = KnowledgeStore(state.db, state.embedding_service)
            record(report, "knowledge_store", True)
        except Exception as e:
            record(report, "knowledge_store", False, str(e))

        try:
            from src.memory.social_graph import SocialGraph

            state.social_graph = SocialGraph(state.db, state.embedding_service)
            record(report, "social_graph", True)
        except Exception as e:
            record(report, "social_graph", False, str(e))

        try:
            from src.memory.soul import Soul, set_core_soul_cache
            from src.memory.consciousness import set_core_soul as set_consciousness_soul

            state.soul = Soul(state.db)
            if state.catalog and state.catalog.core_soul:
                set_core_soul_cache(state.catalog.core_soul)
                set_consciousness_soul(state.catalog.core_soul)
            record(report, "soul", True)
        except Exception as e:
            record(report, "soul", False, str(e))
    else:
        warn(report, "Skipping memory subsystems (no SQLite)")

    # --- 12. Memory System facade ---
    try:
        from src.memory.memory_system import MemorySystem

        state.memory_system = MemorySystem(
            working_memory=state.working_memory,
            episodic_memory=state.episodic_memory,
            knowledge_store=state.knowledge_store,
            social_graph=state.social_graph,
            soul=state.soul,
        )
        record(report, "memory_system", True)
    except Exception as e:
        record(report, "memory_system", False, str(e))

    # --- 13. Search module ---
    try:
        from src.engine_core.search import Search
        from src.herald.catalog import get_context_window

        # Sizes the skill-metadata budget off the agent model's real window, so
        # a small-context model is not handed the same skill list as a large one.
        skill_context_window = (
            get_context_window(settings.llm_model) if settings.llm_model else None
        )

        state.search = Search(
            db=state.db,
            episodic_memory=state.episodic_memory,
            knowledge_store=state.knowledge_store,
            social_graph=state.social_graph,
            embedding_service=state.embedding_service,
            catalog=state.catalog,
            skill_context_window=skill_context_window,
        )
        await state.search.start()
        record(report, "search", True)
    except Exception as e:
        record(report, "search", False, str(e))

    # --- 14. Event Emitter ---
    record(report, "event_emitter", True)

    # --- 14b. Activity Manager ---
    try:
        from src.utils.activity_manager import ActivityManager

        state.activity_manager = ActivityManager(
            emitter=state.emitter,
            idle_timeout_s=float(settings.activity_idle_timeout_s),
            away_timeout_s=float(settings.activity_away_timeout_s),
            disconnect_timeout_s=float(settings.activity_disconnect_timeout_s),
        )
        ActivityManager.set_instance(state.activity_manager)
        await state.activity_manager.start()
        record(
            report,
            "activity_manager",
            True,
            f"idle={settings.activity_idle_timeout_s}s "
            f"away={settings.activity_away_timeout_s}s "
            f"disconnect={settings.activity_disconnect_timeout_s}s",
        )
        from src.infra.cleanup_registry import register_cleanup

        am_ref = state.activity_manager
        register_cleanup("activity_manager", lambda: am_ref.stop())
    except Exception as e:
        record(report, "activity_manager", False, str(e))

    # --- 15. Tool Registry ---
    try:
        from src.utility_directory.tool_runtime import hydrate_tools

        state.tools = await hydrate_tools(ud_full=state.ud_full)
        tool_count = state.tools.tool_count if state.tools else 0
        record(report, "tool_registry", True, f"{tool_count} tools")
    except Exception as e:
        record(report, "tool_registry", False, str(e))

    # --- 15b. Sandbox (isolation seam) ---
    # Provision ONE bwrap sandbox per user-engine and register its tools into the
    # SHARED registry, so every thread shares the one namespace-isolated sandbox.
    # The session is EPHEMERAL: a SandboxSessionManager reaps it after
    # settings.sandbox_idle_ttl_s of inactivity (discarding the workspace) and
    # re-provisions a fresh one on the next use — the durable TASK state lives in
    # SQLite, not the sandbox. The engine depends on SandboxProvider, never on
    # SandboxExecutor directly — swapping in a Firecracker provider later never
    # touches engine code. Network is default-DENY (no permission_callback → egress
    # refused). See architecture/sections/50_sandbox.md → the isolation seam.
    if state.tools is not None:
        try:
            from src.sandbox.provider import (
                LocalBwrapProvider,
                SandboxConfig,
                SandboxSessionManager,
            )
            from src.sandbox.tools import build_sandbox_tools

            provider = LocalBwrapProvider()
            config = SandboxConfig(allow_network=False)
            session = await provider.provision(config)
            # Field always exists and validation enforces >= 1; no fallback.
            ttl = settings.sandbox_idle_ttl_s
            manager = SandboxSessionManager(provider, config, session, idle_ttl_s=ttl)
            manager.start_reaper()
            state.sandbox_provider = provider
            state.sandbox_session = manager
            for tool in build_sandbox_tools(manager):
                state.tools.register(tool)
            from src.infra.cleanup_registry import register_cleanup

            sandbox_manager = manager
            register_cleanup("sandbox", lambda: sandbox_manager.shutdown())
            record(
                report,
                "sandbox",
                True,
                f"{provider.name} bwrap={manager.uses_bubblewrap} idle_ttl={ttl}s",
            )
        except Exception as e:
            record(report, "sandbox", False, str(e))
    else:
        record(report, "sandbox", False, "tool registry unavailable")

    # --- 16. Utility module ---
    try:
        from src.utility_directory.utility import Utility

        state.utility = Utility(ud_full=state.ud_full, tool_registry=state.tools)
        record(report, "utility", True)
    except Exception as e:
        record(report, "utility", False, str(e))

    # --- 18. Trajectory ---
    record(report, "trajectory", state.db is not None, "via sqlite")

    # --- 19. Planner ---
    record(report, "planner", True, "library mode")

    # --- 20. Consciousness Manager ---
    try:
        from src.memory.consciousness import ConsciousnessManager

        state.consciousness_mgr = ConsciousnessManager(
            soul=state.soul,
            social_graph=state.social_graph,
            episodic_memory=state.episodic_memory,
            knowledge_store=state.knowledge_store,
            global_cache_ttl=settings.global_cache_ttl,
        )
        record(report, "consciousness", True)
    except Exception as e:
        record(report, "consciousness", False, str(e))

    # --- 21. Coordinator ---
    if state.adapter is not None and state.working_memory is not None:
        try:
            from src.engine_core.coordinator import Coordinator

            state.coordinator = Coordinator(
                adapter=state.adapter,
                emitter=state.emitter,
                memory=state.working_memory,
                tools=state.tools,
                db=state.db,
                settings=settings,
                consciousness_mgr=state.consciousness_mgr,
                ask_user_callback=ask_user_callback,
                search=state.search,
                utility=state.utility,
                skill_catalog=state.catalog,
                data_dir=user_data_dir,
            )
            # v2: the coordinator owns the pause path. Point its ask-user
            # callback at its own await_block_input so every thread created via
            # get_or_create_thread (and the ambiguous-routing prompt) funnels
            # through the one pending_inputs registry that resume_block resolves.
            state.coordinator.ask_user_callback = state.coordinator.await_block_input
            record(report, "coordinator", True)
            # Register LIFO cleanup — runs before SQLite (registered earlier)
            from src.infra.cleanup_registry import register_cleanup

            coord_ref = state.coordinator
            register_cleanup("coordinator", lambda: coord_ref.shutdown())
        except Exception as e:
            record(report, "coordinator", False, str(e))
    else:
        missing = []
        if state.adapter is None:
            missing.append("LLM adapter")
        if state.working_memory is None:
            missing.append("Working Memory")
        record(report, "coordinator", False, f"missing: {', '.join(missing)}")

    # --- 21b. Restore active conversations (crash recovery) ---
    if state.db is not None and state.coordinator is not None:
        try:
            from src.engine_core.conversation_store import ConversationStore

            conv_store = ConversationStore(state.db)
            active = await conv_store.list_active()
            restored = 0
            for convo in active:
                tid = convo["thread_id"]
                try:
                    await state.coordinator.get_or_create_thread(tid)
                    # Restore task-index entry.  list_active() emits the
                    # thread_index dict directly from the new SQL columns
                    # (migration 015); recent_turns is a list already.
                    from src.engine_core.schema import ThreadIndexEntry

                    ti = convo.get("thread_index", {}) or {}
                    recent_turns = ti.get("recent_turns") or []
                    if not isinstance(recent_turns, list):
                        recent_turns = []
                    state.coordinator.thread_index[tid] = ThreadIndexEntry(
                        thread_id=tid,
                        turn_count=convo.get("turn_count", 0),
                        last_activity=convo.get("last_activity", ""),
                        summary=ti.get("summary", ""),
                        current_state=ti.get("current_state", ""),
                        recent_turns=recent_turns,
                        pinned=bool(convo.get("pinned", False)),
                    )
                    state.restored_thread_ids.add(tid)
                    restored += 1
                except Exception as e:
                    logger.warning("Failed to restore thread %s: %s", tid, e)
            record(report, "thread_restore", True, f"{restored}/{len(active)} threads")
        except Exception as e:
            record(report, "thread_restore", False, str(e))

    # --- 21c. Set up triage on the coordinator ---
    # Triage gates every input, so it gets its own cheap, fast models rather
    # than sharing the light adapter: a primary and an alternative from a
    # different provider, so one provider's outage costs latency, not the turn.
    if state.coordinator is not None:
        try:
            from src.herald import create_llm

            dispatch_adapter = None
            dispatch_alternative = None
            # Herald owns the defaults: empty DISPATCH_MODEL means "use the
            # launch default", not "disable dispatch". Resolution always
            # returns a non-empty id for both roles.
            dispatch_id = resolve_model(ModelRole.DISPATCH, settings)
            dispatch_alt_id = resolve_model(ModelRole.DISPATCH_ALTERNATIVE, settings)
            try:
                dispatch_adapter = create_llm(settings, model_id=dispatch_id)
            except Exception as e:
                warn(report, f"Dispatch primary unavailable ({dispatch_id}): {e}")
            try:
                dispatch_alternative = create_llm(settings, model_id=dispatch_alt_id)
            except Exception as e:
                warn(
                    report,
                    f"Dispatch alternative unavailable ({dispatch_alt_id}): {e}",
                )
            if dispatch_adapter is None:
                dispatch_adapter = dispatch_alternative or state.light_adapter
                dispatch_alternative = None
            if dispatch_adapter is None:
                raise RuntimeError("no model available for triage")

            state.coordinator.set_router(dispatch_adapter, alternative=dispatch_alternative)
            record(report, "thread_router", True)
        except Exception as e:
            record(report, "thread_router", False, str(e))
            warn(report, "Router not available — new messages will create new threads")

    # --- 22b. LC Adapter (cheap model for batch encoding) ---
    lc_adapter = None
    if state.db is not None and state.memory_system is not None:
        try:
            from src.herald import create_llm

            lc_id = resolve_model(ModelRole.LC, settings)
            lc_adapter = create_llm(settings, lc_id)
            # Provider label comes from the catalog entry, not config: the
            # catalog (citadel-synced) knows who serves a model id.
            lc_entry = get_model(lc_id)
            lc_provider_label = lc_entry.provider if lc_entry is not None else "unknown"
            record(report, "lc_adapter", True, f"{lc_provider_label}/{lc_id}")
        except Exception as e:
            lc_adapter = state.adapter
            warn(report, f"LC adapter failed ({e}), using main adapter")

    # --- 22c. LC Pipeline + Scheduler ---
    if state.db is not None and lc_adapter is not None and state.memory_system is not None:
        try:
            from src.learning_centre.pipeline import LCPipeline
            from src.learning_centre.scheduler import LCScheduler

            state.lc_pipeline = LCPipeline(
                adapter=lc_adapter,
                db=state.db,
                memory_system=state.memory_system,
                ud_write=state.ud_write,
                settings=settings,
                on_data_changed=state.consciousness_mgr.invalidate
                if state.consciousness_mgr
                else None,
                signal_store=None,  # pipeline creates its own from db
                embedding_service=state.embedding_service,
            )
            state.lc_scheduler = LCScheduler(
                state.lc_pipeline,
                interval_hours=settings.lc_batch_interval_hours,
            )
            if state.coordinator is not None:
                state.lc_scheduler.set_coordinator(state.coordinator)
            await state.lc_scheduler.start()
            record(
                report,
                "lc_pipeline",
                True,
                f"every {settings.lc_batch_interval_hours}h lc_model={lc_id}",
            )
            record(report, "lc_scheduler", state.lc_scheduler is not None)
            # Register LIFO cleanup — runs before coordinator (registered later)
            from src.infra.cleanup_registry import register_cleanup

            sched_ref = state.lc_scheduler
            register_cleanup("lc_scheduler", lambda: sched_ref.stop())
        except Exception as e:
            record(report, "lc_pipeline", False, str(e))
            record(report, "lc_scheduler", False, "lc_pipeline init failed")
    else:
        warn(report, "Skipping LC Pipeline (missing SQLite, LLM, or MemorySystem)")

    # --- Activity policy (consumer wiring for ActivityStateChanged) ---
    # Installed after lc_scheduler + raven init so the policy can pause/
    # resume the scheduler and mirror transitions into obs_events.
    try:
        from src.utils.activity_consumers import ActivityPolicy
        from src.raven import api as raven_api

        raven_track = raven_api.track if raven_api.is_ready() else None
        state.activity_policy = ActivityPolicy(
            emitter=state.emitter,
            lc_scheduler=state.lc_scheduler,
            raven_track=raven_track,
        )
        state.activity_policy.install()
        record(
            report,
            "activity_policy",
            True,
            f"lc={'y' if state.lc_scheduler else 'n'} raven={'y' if raven_track else 'n'}",
        )
        from src.sandbox.cleanup_registry import register_cleanup as register_sandbox_cleanup

        policy_ref = state.activity_policy

        async def uninstall_policy() -> None:
            policy_ref.uninstall()

        register_sandbox_cleanup("activity_policy", uninstall_policy)
    except Exception as e:
        record(report, "activity_policy", False, str(e))

    # --- Process cleanup infrastructure ---
    try:
        from src.sandbox.orphan_detector import orphan_cleanup_enabled, start_orphan_scanner
        from src.engine_core.orphan_self_detect import (
            self_orphan_detection_enabled,
            start_self_orphan_detector,
        )

        if orphan_cleanup_enabled():
            await start_orphan_scanner(interval_sec=30.0)
            record(report, "orphan_scanner", True, "interval=30s")
        else:
            record(report, "orphan_scanner", False, "disabled by flag")

        if self_orphan_detection_enabled():

            async def trigger_shutdown() -> None:
                await shutdown(state)

            await start_self_orphan_detector(shutdown_callback=trigger_shutdown)
            record(report, "self_orphan_detector", True)
        else:
            # Intentional config choice — server mode, CLI/TUI enables it instead.
            record_disabled(report, "self_orphan_detector", "disabled by flag")
    except Exception as e:
        record(report, "process_cleanup", False, str(e))

    # --- Finalize ---
    report.success = state.is_operational
    report.boot_duration_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "Boot complete: success=%s duration=%dms subsystems=%d/%d",
        report.success,
        report.boot_duration_ms,
        sum(1 for v in report.subsystems.values() if v),
        len(report.subsystems),
    )
    return state, report


async def shutdown(state: EngineState) -> None:
    """Graceful shutdown — stop scheduler, close database.

    Internal plumbing (signal observers, interrupt source, orphan detectors,
    sandbox cleanup) is torn down here.  Subsystem teardown (lc_scheduler,
    coordinator, SQLite) is handled by the infra cleanup registry in LIFO
    order, registered during bootstrap().
    """
    logger.info("Shutting down engine")

    # Signal activity manager that this session is ending
    try:
        from src.utils.activity_manager import ActivityManager

        am = ActivityManager.get_instance()
        am.record_shutdown()
    except Exception:
        pass

    # Clear the subprocess spawn observer so a subsequent bootstrap
    # does not fan events out to a dead emitter.
    try:
        from src.utility_directory.subprocess_env import register_spawn_observer

        register_spawn_observer(None)
    except Exception:
        pass

    # Stop the catalog resync poll.
    #
    # `catalog_sync.initialize()` creates a periodic task - and on a
    # degraded boot an ACCELERATED retry, polling harder precisely because
    # citadel was unreachable. `catalog_sync.shutdown()` was written to
    # cancel it and had no caller anywhere in the boot path, so every
    # engine that shut down left that task running against a loop that was
    # about to go away.
    #
    # Found because it hung the integration suite: pytest-asyncio gives
    # each test its own loop, the orphaned task holds the previous one, and
    # the next bootstrap sits in `select` forever. The whole file emitted no
    # output at all, not even collection, and the suite reported itself
    # green without its fifteen tests.
    #
    # Same shape as this project's most-cited defect: written, tested, and
    # never called.
    try:
        from src.herald import catalog_sync

        await catalog_sync.shutdown()
    except Exception:
        logger.debug("catalog_sync teardown failed", exc_info=True)

    # Release the SIGINT handler if bootstrap installed one.  Idempotent.
    try:
        from src.engine_core.interrupt_source import get_interrupt_source

        get_interrupt_source().uninstall()
    except Exception:
        logger.debug("interrupt source uninstall failed", exc_info=True)

    # Stop process cleanup infrastructure
    try:
        from src.engine_core.orphan_self_detect import stop_self_orphan_detector
        from src.sandbox.orphan_detector import stop_orphan_scanner
        from src.sandbox.cleanup_registry import run_all_cleanup

        await stop_self_orphan_detector()
        await stop_orphan_scanner()
        cleaned = await run_all_cleanup(timeout=2.0)
        if cleaned:
            logger.info("Sandbox cleanup registry: %d callback(s) executed", cleaned)
    except Exception:
        logger.debug("process cleanup teardown failed", exc_info=True)

    # Run infra cleanup registry in LIFO order:
    # lc_scheduler → coordinator → sqlite (registered in reverse boot order)
    try:
        from src.infra.cleanup_registry import run_cleanups, clear_registry

        await run_cleanups()
        clear_registry()
    except Exception:
        logger.debug("infra cleanup registry teardown failed", exc_info=True)

    # The registry is process-global and clear_registry() empties it, so a
    # nested shutdown drops the outer engine's entries before it ever runs its
    # own. Close this state's database directly as well: SqlitePool.close() is
    # idempotent, and a shutdown must be self-sufficient for the state it was
    # handed rather than trusting a shared list nobody else has emptied. An
    # unclosed connection leaves its aiosqlite worker thread alive past the
    # event loop, which surfaces later as "Event loop is closed" from
    # unrelated code.
    try:
        if getattr(state, "db", None) is not None:
            await state.db.close()
    except Exception:
        logger.debug("database close during shutdown failed", exc_info=True)

    logger.info("Engine shutdown complete")
