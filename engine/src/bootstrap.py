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

    db: Any = None
    catalog: Any = None
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
    sandbox_provider: Any = None
    sandbox_session: Any = None
    consciousness_mgr: Any = None
    coordinator: Any = None
    lc_pipeline: Any = None
    lc_scheduler: Any = None
    activity_manager: Any = None
    activity_policy: Any = None
    light_adapter: Any = None
    restored_thread_ids: set[str] = field(default_factory=set)
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

    from src.infra.september_home import ensure_layout

    ensure_layout(settings.september_home)

    from src.infra.graceful_shutdown import setup_signal_handlers
    from src.infra.cleanup_registry import clear_registry

    clear_registry()
    sigint_owned_by_interrupt_source = settings.enable_sigint_interrupt
    setup_signal_handlers(register_sigint=not sigint_owned_by_interrupt_source)

    if settings.enable_sigint_interrupt:
        from src.engine_core.interrupt_source import get_interrupt_source

        get_interrupt_source().install()

    validation_errors = settings.validate()
    if validation_errors:
        for err in validation_errors:
            record(report, "settings", False, err)
        report.boot_duration_ms = int((time.monotonic() - t0) * 1000)
        raise ConfigError(validation_errors)
    record(report, "settings", True, f"model={resolve_model(ModelRole.PRIMARY, settings)}")

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
        try:
            asyncio.get_running_loop().create_task(state.emitter.emit(event))
        except RuntimeError:
            pass

    register_spawn_observer(fanout_spawn_event)

    try:
        from src.infra.sqlite_pool import SqlitePool

        state.db = SqlitePool(settings.sqlite_db_path)
        await state.db.connect()
        await state.db.health_check()
        vec_status = "vec loaded" if state.db.vec_available else "vec unavailable"
        record(report, "sqlite", True, vec_status)
        from src.infra.cleanup_registry import register_cleanup

        db_ref = state.db
        register_cleanup("sqlite", lambda: db_ref.close())
    except Exception as e:
        record(report, "sqlite", False, str(e))
        state.db = None

    if state.db is not None:
        try:
            from src.infra.migrations import run_migrations

            applied = await run_migrations(state.db, settings.migrations_dir or None)
            if applied:
                logger.info("Migrations applied: %s", applied)
            record(report, "migrations", True, f"{len(applied)} new" if applied else "up to date")
        except Exception as e:
            record(report, "migrations", False, str(e))

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

    try:
        from src.catalog import load_catalog
        from pathlib import Path
        from src.package import PACKAGE_ROOT

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

    try:
        from src.herald import create_llm

        state.adapter = create_llm(settings)
        record(report, "llm_adapter", True, f"citadel/{resolve_model(ModelRole.PRIMARY, settings)}")
    except Exception as e:
        record(report, "llm_adapter", False, str(e))

    if state.db is not None:
        try:
            from src.herald import herald_api

            herald_api.init(state.db)
            gw = herald_api.get_config_obj()
            record(report, "herald_gateway", True, f"profile={gw.routing.profile}")
        except Exception as e:
            record(report, "herald_gateway", False, str(e))

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

    try:
        from src.herald import create_llm
        from src.herald.catalog import ModelTier, resolve_model_for_tier

        light_def = resolve_model_for_tier(ModelTier.LIGHT, settings)
        state.light_adapter = create_llm(settings, model_id=light_def.id)
        record(report, "light_adapter", True, light_def.id)
    except Exception as e:
        state.light_adapter = state.adapter
        record(report, "light_adapter", False, f"fell back to primary: {e}")

    try:
        from src.memory.embeddings import get_embedding_service

        state.embedding_service = get_embedding_service(settings)
        mode = "real" if state.embedding_service.is_real else "zero-vector"
        record(report, "embeddings", True, mode)
    except Exception as e:
        record(report, "embeddings", False, str(e))

    if state.catalog is not None and state.embedding_service is not None:
        try:
            await state.catalog.embed_all_descriptions(state.embedding_service)
            embedded = len(state.catalog.skill_embeddings)
            record(report, "skill_embeddings", True, f"{embedded} skills embedded")
        except Exception as e:
            record(report, "skill_embeddings", False, str(e))

    if state.db is not None:
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

    try:
        from src.engine_core.search import Search
        from src.herald.catalog import get_context_window

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

    record(report, "event_emitter", True)

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

    try:
        from src.utility_directory.tool_runtime import hydrate_tools

        state.tools = await hydrate_tools(ud_full=state.ud_full)
        tool_count = state.tools.tool_count if state.tools else 0
        record(report, "tool_registry", True, f"{tool_count} tools")
    except Exception as e:
        record(report, "tool_registry", False, str(e))

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

    try:
        from src.utility_directory.utility import Utility

        state.utility = Utility(ud_full=state.ud_full, tool_registry=state.tools)
        record(report, "utility", True)
    except Exception as e:
        record(report, "utility", False, str(e))

    record(report, "trajectory", state.db is not None, "via sqlite")

    record(report, "planner", True, "library mode")

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
            state.coordinator.ask_user_callback = state.coordinator.await_block_input
            record(report, "coordinator", True)
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

    if state.coordinator is not None:
        try:
            from src.herald import create_llm

            dispatch_adapter = None
            dispatch_alternative = None
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

    lc_adapter = None
    if state.db is not None and state.memory_system is not None:
        try:
            from src.herald import create_llm

            lc_id = resolve_model(ModelRole.LC, settings)
            lc_adapter = create_llm(settings, lc_id)
            lc_entry = get_model(lc_id)
            lc_provider_label = lc_entry.provider if lc_entry is not None else "unknown"
            record(report, "lc_adapter", True, f"{lc_provider_label}/{lc_id}")
        except Exception as e:
            lc_adapter = state.adapter
            warn(report, f"LC adapter failed ({e}), using main adapter")

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
                signal_store=None,
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
            from src.infra.cleanup_registry import register_cleanup

            sched_ref = state.lc_scheduler
            register_cleanup("lc_scheduler", lambda: sched_ref.stop())
        except Exception as e:
            record(report, "lc_pipeline", False, str(e))
            record(report, "lc_scheduler", False, "lc_pipeline init failed")
    else:
        warn(report, "Skipping LC Pipeline (missing SQLite, LLM, or MemorySystem)")

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
            record_disabled(report, "self_orphan_detector", "disabled by flag")
    except Exception as e:
        record(report, "process_cleanup", False, str(e))

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

    try:
        from src.utils.activity_manager import ActivityManager

        am = ActivityManager.get_instance()
        am.record_shutdown()
    except Exception:
        pass

    try:
        from src.utility_directory.subprocess_env import register_spawn_observer

        register_spawn_observer(None)
    except Exception:
        pass

    try:
        from src.herald import catalog_sync

        await catalog_sync.shutdown()
    except Exception:
        logger.debug("catalog_sync teardown failed", exc_info=True)

    try:
        from src.engine_core.interrupt_source import get_interrupt_source

        get_interrupt_source().uninstall()
    except Exception:
        logger.debug("interrupt source uninstall failed", exc_info=True)

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

    try:
        from src.infra.cleanup_registry import run_cleanups, clear_registry

        await run_cleanups()
        clear_registry()
    except Exception:
        logger.debug("infra cleanup registry teardown failed", exc_info=True)

    try:
        if getattr(state, "db", None) is not None:
            await state.db.close()
    except Exception:
        logger.debug("database close during shutdown failed", exc_info=True)

    logger.info("Engine shutdown complete")
