"""The sim boot sequence: one pinned thread, no Coordinator/ThreadRouter dispatch.

Why this is its own function and not a call into bootstrap.bootstrap()
------------------------------------------------------------------------
bootstrap.bootstrap() (src/bootstrap.py) is the production boot: it
validates EVERY chat model role against the citadel-synced catalog (step
4a2, via src/herald/model_validation.py) before anything else runs,
including ModelRole.REASONING - which has NO settings override by design
(see src/herald/defaults.py's ROLE_SETTINGS_FIELD: REASONING is
deliberately absent, "reachable only through tier resolution"). A broker
that serves exactly {moonshotai.kimi-k2.5} has no row for whatever
REASONING resolves to, so that validation step is unconditionally FATAL for
this sim topology - not a bug in bootstrap.py, a correct rejection of a
config that cannot satisfy production's promises (a live citadel catalog
with dozens of models). bootstrap.py also unconditionally wires a
Coordinator (step 21) and a ThreadRouter (step 21c, via
Coordinator.set_router), the latter of which the sim must never do at all
(see below).

So this module builds only the subsystems ONE pinned thread actually needs -
SQLite, working memory, the guarded kimi-k2.5 adapters, a Coordinator - by
composing the same constructors bootstrap.py composes, directly, in the
order bootstrap.py established. src/bootstrap.py and
src/engine_core/coordinator.py are unmodified; boot_sim_engine is a new,
alternate front door, not a patch to the old one.

Why "bypass Coordinator/ThreadRouter" doesn't mean "don't use Coordinator"
------------------------------------------------------------------------
Coordinator.build_dispatch (src/engine_core/coordinator.py) already ships an
explicit bypass: "an explicit thread_id (the caller already knows the
thread)" skips ThreadRouter entirely and returns a single_segment targeting
that thread_id - no triage LLM call, no model resolution for DISPATCH_MODEL
at all. This module drives every tick through exactly that path
(``SimEngineState.send_tick`` calls ``coordinator.handle_input(text,
thread_id=PINNED_THREAD_ID)``) and never calls ``coordinator.set_router()``,
so ``coordinator.router`` stays ``None``: ThreadRouter is never even
instantiated in this process. Reusing Coordinator (rather than driving
ExecutionThread.execute() directly) keeps pin/unpin bookkeeping, per-thread
turn locking, and LC-signal flushing working exactly as in production - only
the routing hop is gone, which is what DESIGN.md section 4 asks for: "saves
a routing LLM call per message; linear trajectory."

There is also a second, unrelated per-turn model router INSIDE
ExecutionThread's agent loop (thread.py's "Per-turn model routing" block,
gated on ``herald_api.get_config_obj() is not None`` - see
src/herald/herald_api.py). That global starts and stays ``None`` until
something calls ``herald_api.init(db)``, which bootstrap.py does (step
"4-gw") and this module deliberately never does. So that block is dead code
in this topology too: every turn's adapter is ``self.adapter`` (the guarded
kimi-k2.5 Provider handed to ExecutionThread's constructor), for the whole
process lifetime, with no per-turn re-selection.

Why spawn_sub_agent forking is structurally impossible here, not just off
------------------------------------------------------------------------
FORK_SUBAGENT=false (src/engine_core/fork_subagent.is_fork_subagent_enabled)
only disables the prompt-cache-sharing optimisation inside an ALREADY-
spawned sub-agent; it does not stop the model from calling the
spawn_sub_agent TOOL in the first place - ExecutionThread.initiate()
registers that tool unconditionally for every thread (see
register_spawn_sub_agent_tool). The real gate is settings.deny_rules
(src.sim.config.fork_deny_rules, wired in by build_settings):
check_permission()'s DENY-rule step (step 2 of its pipeline, see
src/sandbox/permissions.py) runs and returns BEFORE the spawn_sub_agent
handler is ever invoked - before any SubAgent is constructed, before any
model tier is resolved, before any second create_llm call happens.
tests/test_sim_fake_broker.py proves this: it scripts a
tool_use("spawn_sub_agent", ...) response and asserts the fake broker never
receives a request for anything but MODEL_ID.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import ConfigError, Settings
from src.engine_core.coordinator import Coordinator
from src.engine_core.loop_state import StrugglingState
from src.memory.working_memory import EntryType
from src.sim.bounded_history import bound_history_in_the_seam
from src.sim.bounded_monologue import end_the_turn_on_a_cut_monologue
from src.sim.a_turn_that_tried_to_act import notice_a_turn_that_tried_to_act
from src.sim.half_written_tool_call import keep_the_turn_on_a_half_written_call
from src.sim.bounded_signals import bound_signal_growth
from src.engine_core.events import EventEmitter
from src.engine_core.schema import RequestOptions, ThreadIndexEntry
from src.engine_core.thread import ExecutionThread
from src.herald.base import LLM
from src.infra.sqlite_pool import SqlitePool
from src.memory.episodic_memory import EpisodicMemory
from src.memory.knowledge_store import KnowledgeStore
from src.memory.social_graph import SocialGraph
from src.memory.soul import Soul
from src.memory.consciousness import ConsciousnessManager, SectionStability
from src.memory.memory_factory import create_working_memory
from src.memory.memory_system import MemorySystem
from src.sim import world_tools
from src.sim.config import (
    SimProfile,
    build_settings,
    learn_awake_interval_ticks,
    pinned_thread_id,
    resident_learns,
    validate_sim_settings,
)
from src.sim.learning_gate import SimLearningGate
from src.sim.model_profile import MODEL_ID, guarded_create_llm, register_into_catalog_overlay
from src.sim.resident_voice import speak_as_resident
from src.sim.memory_stream import (
    MEMORY_STREAM_FILENAME,
    MemoryStreamWriter,
    build_memory_stream_tool,
    install_memory_stream_permissions,
)
from src.sim.session_log import SessionLogWriter, build_session_log_tool, install_session_log_permissions
from src.sim.tool_surface import prune_to_the_towns_tools
from src.utility_directory.tool_runtime import ToolRegistry

logger = logging.getLogger(__name__)


async def default_sim_ask_user_callback(params: Any) -> dict:
    """Answers ask_user / permission prompts immediately - no operator loop.

    DESIGN.md's operator console (section 8) is for petitions, prayers,
    land, and disasters, not routine per-tool permission prompts, and there
    is no human watching every tick of every one of 250 agents.
    Coordinator.await_block_input (the production ask_user_callback) waits
    on a real client to answer a CraftRequest block; wired unmodified here it
    would wait forever with nobody able to answer (pinned threads get NO
    timeout at all in await_block_input, not even the normal 72h one). This
    callback returns immediately instead, so a tick can never hang on it.
    World tool calls should almost never reach this path: world_tools.
    install_permissions grants an explicit ALLOW rule for every "world:*"
    tool, so only a genuine ask_user tool call or a non-world tool with no
    matching rule lands here.
    """
    return {
        "response": (
            "No human operator is available in the simulation loop; "
            "proceed using your own judgment."
        ),
        "feedback": "",
        "content_blocks": [],
    }


@dataclass
class SimEngineState:
    """Everything one pinned agent's engine process holds."""

    profile: SimProfile
    settings: Settings
    db: SqlitePool
    emitter: EventEmitter
    coordinator: Coordinator
    thread: ExecutionThread
    thread_id: str
    primary_adapter: LLM
    lc_adapter: LLM
    learning_gate: SimLearningGate
    world: world_tools.WorldClient
    session_log: SessionLogWriter
    memory_stream: MemoryStreamWriter
    tools: ToolRegistry | None = None
    _turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def turn_in_flight(self) -> bool:
        """Is a turn running right now?

        CORE-11. The runner used to answer this by GUESSING: after a tick
        timed out it refused to dispatch to that resident for another
        `tick_timeout_s`, on the reasoning that the turn was "probably
        still running". A guess in the right direction, but a guess - and
        the comment justifying it said the engine "has no route that
        answers 'are you busy', and inventing one would put the fix in
        frozen code". That was wrong on the second half: this file and
        `agent_adapter.py` are both `engine/src/sim/`, the seam ROADMAP
        Law 1 leaves writable, and the review's own fix says so.

        `locked()` rather than a flag we set and clear: a flag has a window
        between the lock being taken and the flag being written, and the
        thing being guarded against is precisely a request arriving in a
        window.
        """
        return self._turn_lock.locked()
    tool_registry_version: int = 0
    last_turn_steps: int = 0
    last_turn_hit_budget: bool = False

    async def send_tick(
        self, perception_text: str, call_budget: int | None = None
    ) -> str:
        """Route one tick's perception straight to the pinned thread.

        thread_id is explicit, so Coordinator.build_dispatch takes its
        "explicit thread_id" structural bypass and never calls ThreadRouter
        (see this module's docstring).

        session_log.start_turn() runs first so the accumulator has an
        input to attach reasoning/actions to as this turn's events arrive
        on the emitter; ExecutionSummary (fired by the frozen engine
        itself, not by this call) is what actually flushes the completed
        record to disk - see session_log.py.
        """
        if self.tools is not None:
            self.tool_registry_version = await world_tools.refresh_world_tool_registry(
                self.world,
                self.tools,
                self.tool_registry_version,
                force=LOCAL_TOOL_SCHEMAS,
            )
            forget_the_cached_tool_wire(self.primary_adapter)

        logger.info(
            "tick tools: registry=%d defs=%d",
            len(getattr(self.tools, "tools", {}) or {}),
            len(self.tools.to_tool_defs()) if self.tools else -1,
        )

        async with self._turn_lock:
            self.session_log.start_turn(perception_text)
            self.memory_stream.start_turn(perception_text)
            await self.forget_the_last_ticks_struggle()
            self.last_turn_steps = 0
            self.last_turn_hit_budget = False
            options = (
                RequestOptions(max_turns=call_budget)
                if call_budget and call_budget > 0
                else None
            )
            result = await self.coordinator.handle_input(
                perception_text, thread_id=self.thread_id, options=options
            )

        summary = self.session_log.last_summary
        if summary is not None:
            self.last_turn_steps = int(summary.get("num_turns", 0) or 0)
            self.last_turn_hit_budget = summary.get("exit_reason") == "max_turns"
        return result if isinstance(result, str) else str(result)

    async def forget_the_last_ticks_struggle(self) -> None:
        """Zero the struggle counters this tick's turn will be judged on.

        ENG-22. At the top of every agent loop `thread.py` restores
        `state.struggle` from the LAST working-memory `SYSTEM_EVENT` whose
        content begins with the struggle marker, and after every tool step
        it writes the updated counters back. For an assistant thread that
        spans one conversation that is exactly right. For a Kasukabe
        resident the thread is PINNED FOR THE LIFE OF THE RUN, so nothing
        ever ended the accumulation: `recent_tool_sigs` kept a rolling
        twelve signatures across every tick since genesis and
        `turns_without_text` only reset when the model spoke. Three `say`
        calls with the same argument keys - one at dawn, one at noon, one
        now - trip `STRUGGLE_REPEATED_CALLS`, and the resident is handed
        "[System: Execution was struggling ...]" plus an auto-plan for
        behaviour that repeated nothing within any turn it took. That
        injection is ENG-07(b), scaffolding this seam exists to remove.

        The seam cannot change what `thread.py` restores; it can decide
        what is there to be restored. Writing one zeroed entry per tick,
        into the same working-memory object the thread reads, is a
        composition over the engine's own persistence and not an edit to
        it - the review's own wording. Within a tick the counters still
        accumulate, which is the behaviour that was always wanted: a
        resident spinning on one perception is still caught.

        Never raises. A resident whose tick died because a bookkeeping
        write failed has been punished for the bookkeeping's problem.
        """
        try:
            await self.thread.memory.write(
                self.thread_id,
                EntryType.SYSTEM_EVENT,
                json.dumps({"_struggle": StrugglingState().to_dict()}),
            )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning("could not clear this tick's struggle counters: %s", exc)

    async def shutdown(self) -> None:
        await self.coordinator.shutdown()
        await self.world.aclose()
        await self.db.close()


LOCAL_TOOL_SCHEMAS = os.environ.get(
    "KASUKABE_LOCAL_TOOL_SCHEMAS", "1"
).strip().lower() not in {"0", "false", "no", "off"}


def forget_the_cached_tool_wire(adapter: Any) -> bool:
    """Drop herald's cached tool array so a re-description reaches the wire.

    ENG-16. Returns whether a cache was actually found and cleared, so a
    caller can tell "cleared it" from "there was nothing to clear" - the
    difference between this working and this silently doing nothing is the
    whole finding.

    Walks the WHOLE `_inner` chain, not one link of it. Every seam wrapper
    (`resident_voice`, `bounded_monologue`) proxies unknown attributes down
    to the provider through `__getattr__` but has no `__setattr__`, so
    `hasattr(wrapper, "cached_tool_names")` is True while the assignment
    lands on the WRAPPER and shadows the provider's field forever. With one
    wrapper the old two-element walk still reached the provider on its
    second candidate; the moment a second wrapper went on (ENG-21) it would
    have stopped one link short and quietly restored the exact staleness
    ENG-16 exists to remove.
    """
    seen = False
    candidate: Any = adapter
    for _ in range(8):
        if candidate is None:
            break
        if hasattr(candidate, "cached_tool_names"):
            candidate.cached_tool_names = None
            candidate.cached_tool_schemas = None
            seen = True
        candidate = getattr(candidate, "_inner", None)
    return seen


async def boot_sim_engine(
    profile: SimProfile,
    world_base_url: str,
    ask_user_callback: Any = None,
    world_client: world_tools.WorldClient | None = None,
) -> SimEngineState:
    """Boot one Kasukabe agent: settings -> sqlite -> adapters -> pinned thread.

    Raises ConfigError (aggregated - every problem at once, per
    CONVENTIONS.md) if the profile would resolve to any model other than
    MODEL_ID or is missing the fork-block deny rules; see
    src.sim.config.validate_sim_settings.

    ``world_client`` is an injection seam for tests: pass a WorldClient
    built over an ``httpx.MockTransport`` (see tests/test_sim_fake_broker.py
    and tests/test_sim_world_tools.py) instead of one that opens a real
    connection to ``world_base_url``. Production callers (sim_main.py) leave
    it None and a real WorldClient is built from ``world_base_url``.
    """
    settings = build_settings(profile)
    errors = validate_sim_settings(settings)
    if errors:
        raise ConfigError(errors)

    register_into_catalog_overlay()

    db = SqlitePool(settings.sqlite_db_path)
    await db.connect()
    from src.infra.migrations import run_migrations

    await run_migrations(db, settings.migrations_dir or None)

    working_memory = create_working_memory(session_id="", embedder=None, db=db)

    primary_adapter = guarded_create_llm(settings)
    lc_adapter = guarded_create_llm(settings)

    primary_adapter = speak_as_resident(primary_adapter, agent_name=profile.agent_id)

    primary_adapter = end_the_turn_on_a_cut_monologue(primary_adapter)

    primary_adapter = keep_the_turn_on_a_half_written_call(primary_adapter)

    primary_adapter = notice_a_turn_that_tried_to_act(primary_adapter)

    emitter = EventEmitter()

    world = world_client or world_tools.WorldClient(world_base_url, agent_id=profile.agent_id)
    tools: ToolRegistry = await world_tools.build_world_tool_registry(world)

    session_log_path = Path(profile.september_home) / "session_log.jsonl"
    session_log = SessionLogWriter(profile.agent_id, session_log_path)
    session_log.attach(emitter)
    tools.register(build_session_log_tool(session_log_path))

    memory_stream_path = Path(profile.september_home) / MEMORY_STREAM_FILENAME
    memory_stream = MemoryStreamWriter(
        profile.agent_id,
        memory_stream_path,
        sink=world.record_memory,
    )
    memory_stream.attach(emitter)
    tools.register(build_memory_stream_tool(memory_stream_path))

    from src.engine_core.search import Search
    from src.learning_centre.signals import SignalStore
    from src.sim.memory_query import (
        build_search_memory_tool,
        install_search_memory_permissions,
    )

    tools.register(build_search_memory_tool(Search(db, signal_store=SignalStore(db))))

    episodic_memory = EpisodicMemory(db, None)
    knowledge_store = KnowledgeStore(db, None)
    social_graph = SocialGraph(db, None)
    soul = Soul(db)

    consciousness_mgr = ConsciousnessManager(
        soul=soul,
        social_graph=social_graph,
        episodic_memory=episodic_memory,
        knowledge_store=knowledge_store,
    )
    persona_path = Path(profile.september_home) / "persona.txt"
    persona_text = persona_path.read_text(encoding="utf-8") if persona_path.exists() else ""
    consciousness_mgr.register_section(
        "kasukabe_persona",
        SectionStability.STABLE,
        lambda: persona_text
        or (
            f"You are Kasukabe agent {profile.agent_id}, a resident of the town of "
            "Kasukabe. No further persona was seeded for you - act as a plausible "
            "person living an ordinary life here."
        ),
    )

    coordinator = Coordinator(
        adapter=primary_adapter,
        emitter=emitter,
        memory=working_memory,
        tools=tools,
        db=db,
        settings=settings,
        ask_user_callback=ask_user_callback or default_sim_ask_user_callback,
        consciousness_mgr=consciousness_mgr,
    )

    kept = bound_history_in_the_seam(coordinator)
    if bound_signal_growth(coordinator):
        logger.info("signal flushing watermarked: each WM entry is stored once")
    logger.info("history bounded in the seam: %d most recent messages", kept)

    thread_id = pinned_thread_id(profile.agent_id)
    thread = await coordinator.get_or_create_thread(thread_id)
    prune_to_the_towns_tools(thread)
    world_tools.install_permissions(thread)
    install_session_log_permissions(thread)
    install_memory_stream_permissions(thread)
    install_search_memory_permissions(thread)

    coordinator.thread_index[thread_id] = ThreadIndexEntry(
        thread_id=thread_id,
        pinned=True,
        summary=f"Kasukabe agent {profile.agent_id}",
    )

    memory_system = MemorySystem(
        working_memory=working_memory,
        episodic_memory=episodic_memory,
        knowledge_store=knowledge_store,
        social_graph=social_graph,
        soul=soul,
    )
    from src.learning_centre.pipeline import LCPipeline

    from src.sim.resident_learning_prompts import install as install_resident_prompts

    install_resident_prompts()

    lc_pipeline = LCPipeline(
        adapter=lc_adapter,
        db=db,
        memory_system=memory_system,
        settings=settings,
        on_data_changed=consciousness_mgr.invalidate,
    )
    learning_gate = SimLearningGate(
        pipeline=lc_pipeline,
        awake_interval_ticks=learn_awake_interval_ticks(),
        learns=resident_learns(),
    )

    if isinstance(world, world_tools.WorldClient):
        world.signal_store = SignalStore(db)
        world.thread_id = thread_id

    logger.info(
        "Kasukabe sim engine booted: agent=%s thread=%s model=%s broker=%s world=%s",
        profile.agent_id,
        thread_id,
        MODEL_ID,
        settings.cdt_url,
        world_base_url,
    )

    return SimEngineState(
        profile=profile,
        settings=settings,
        db=db,
        emitter=emitter,
        coordinator=coordinator,
        thread=thread,
        thread_id=thread_id,
        primary_adapter=primary_adapter,
        lc_adapter=lc_adapter,
        learning_gate=learning_gate,
        world=world,
        session_log=session_log,
        memory_stream=memory_stream,
        tools=tools,
    )
