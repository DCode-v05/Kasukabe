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
    # The registry world tools live on, kept so a tick can add tools the town
    # has invented since boot, and the version last seen from the world.
    tools: ToolRegistry | None = None
    # Serialises send_tick so a tick cannot start the recorders while the
    # previous turn is still filling them. default_factory, not a shared
    # module-level lock: one per engine, and each engine is its own process
    # anyway - this guards against overlapping ticks within one.
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
    # B3. What the last turn actually used, and whether it ended because it
    # ran out rather than because it was finished. Read straight back out
    # in the /tick response - the WORLD owns the wallet and therefore owns
    # the sentence a resident reads about it, so the engine reports and
    # says nothing. See app/world/call_budget.py.
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
        # Pick up anything the town has invented since the last tick. One
        # small GET when nothing has changed, which is almost every tick.
        # Failure is swallowed inside the client: a stale vocabulary is a far
        # smaller problem than a tick that did not happen.
        if self.tools is not None:
            # `force` when schemas are served by location: the resident's
            # row for `mine` is a full schema at the outcrop and one line
            # from the bakery, and the world's version counter cannot see
            # that - its registry did not change, the resident walked. Off,
            # this is the same cheap version check it always was.
            self.tool_registry_version = await world_tools.refresh_world_tool_registry(
                self.world,
                self.tools,
                self.tool_registry_version,
                force=LOCAL_TOOL_SCHEMAS,
            )
            # ENG-16. Re-describing a tool is invisible to herald's cache.
            #
            # `provider.get_cached_tools` keys on `frozenset(t.name for t in
            # tools)` - the NAME SET. When schemas are served by location the
            # names never change: all 82 are always present, some in full and
            # some as a one-line index row. Only the descriptions and the
            # schemas move. So the cache returned the first wire array it
            # ever built, for the whole run, and ENG-03's saving reached the
            # wire exactly once - on the very first call - and never again.
            #
            # The reset is here rather than in herald/ because herald/ is
            # frozen under ROADMAP Law 1. The provider is behind the
            # resident-voice wrapper, so `_inner` is how the seam reaches it;
            # `getattr` walks the wrapper's __getattr__ when it is not.
            forget_the_cached_tool_wire(self.primary_adapter)

        # Diagnostic: how many tools this turn will actually be able to call.
        #
        # 5,995 of 6,110 agent turns in one run went to Bedrock with no tools
        # at all, and the agents said so plainly - "all execute without
        # visible feedback ... my location never changes". Logged per turn
        # until the cause is closed out, because every downstream symptom
        # (no speech, no work, no authoring) is this one number being zero.
        logger.info(
            "tick tools: registry=%d defs=%d",
            len(getattr(self.tools, "tools", {}) or {}),
            len(self.tools.to_tool_defs()) if self.tools else -1,
        )

        # One turn at a time, and the recorders started only when this tick
        # actually gets to run.
        #
        # handle_input blocks on the thread's own turn lock, but
        # start_turn() ran BEFORE it and overwrites whatever was in flight.
        # So a tick arriving while the previous turn was still working threw
        # away that turn's accumulated actions, and the ExecutionSummary
        # that arrived moments later flushed a record with the NEW
        # perception, no actions, and 0 turns.
        #
        # That is exactly what "0 turn(s) · 0 tool call(s) · exit cancelled"
        # was. Measured: three engines of ten cancelled EVERY session -
        # n02, n07, n09 - while making 25, 56 and 19 successful model calls
        # respectively. The work happened; the record of it was clobbered.
        # They were the three whose turns took longer than their cadence.
        async with self._turn_lock:
            self.session_log.start_turn(perception_text)
            # Same signal, second consumer: the session log records what the
            # turn DID, the memory stream records what the agent is left
            # KNOWING. Both flush on the engine's own ExecutionSummary.
            self.memory_stream.start_turn(perception_text)
            # ENG-22. A new tick is judged on itself, not on the morning.
            await self.forget_the_last_ticks_struggle()
            self.last_turn_steps = 0
            self.last_turn_hit_budget = False
            # B3: the per-tick call budget, honoured through a knob the
            # engine already has. `RequestOptions.max_turns` is what
            # `thread.py` loops over (`for turn in range(state.max_turns)`)
            # and it sets exit_reason="max_turns" when it runs out, so the
            # seam needs no engine change at all and ROADMAP Law 1 holds.
            #
            # One resident's tick was measured at FIFTY model calls, and two
            # residents with the same brief differed eightfold in what a
            # morning cost. That is the difference between $2.00 per
            # agent-day and the roadmap's $0.10.
            options = (
                RequestOptions(max_turns=call_budget)
                if call_budget and call_budget > 0
                else None
            )
            result = await self.coordinator.handle_input(
                perception_text, thread_id=self.thread_id, options=options
            )

        # The engine reports its own count when it hands back a result
        # object; the session log's ExecutionSummary carries it otherwise.
        # Read here rather than guessed, because "how many steps" is the
        # number the resident is about to be told.
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


# B3's companion setting, read once at import. The world decides what to
# serve (app/world/call_budget.py::local_tool_schemas is the same knob on
# the other side); this only decides whether the engine bothers refetching
# every tick to pick the change up.
LOCAL_TOOL_SCHEMAS = os.environ.get(
    "KASUKABE_LOCAL_TOOL_SCHEMAS", "1"
).strip().lower() not in {"0", "false", "no", "off"}

# ENG-20. There is deliberately no compaction threshold set anywhere in
# this file, and it is worth one paragraph saying why, because there used
# to be one and it looked like the number that mattered.
#
# Boot wrote 0.12 into each agent's own SQLite - roughly 20,000 tokens of a
# 163,840-token window - on the reasoning that a resident whose thread is
# PINNED FOR THE LIFE OF THE RUN should not drag an ever-growing
# conversation up to an assistant's 0.80 line. The reasoning was sound and
# the mechanism was not: `CompactionConfig.load` reads the value out of
# SQLite and stores it on the config object, and nothing then asks the
# config object for it. `context_engineering.needs_compaction(used_tokens,
# context_window)` is what decides, it has no parameter a persisted value
# could arrive through, and it compares against a module constant of 0.80.
# So every compaction in all 23 runs fired on 0.80 while this file said
# 0.12 - and a reader looking for what bounds a resident's prompt found
# the wrong number in the right-looking place.
#
# What does bound it: the context window in src/sim/model_profile.py, and
# the history bound in bounded_history.py (ENG-02).


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
    for _ in range(8):  # a chain, not a cycle; bounded so it cannot hang
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

    # Seed herald's catalog with exactly the one allowlisted model so
    # cost-estimation / tier-resolution helpers work without a live citadel
    # sync (see src.sim.model_profile.register_into_catalog_overlay).
    register_into_catalog_overlay()

    db = SqlitePool(settings.sqlite_db_path)
    await db.connect()
    from src.infra.migrations import run_migrations

    await run_migrations(db, settings.migrations_dir or None)

    working_memory = create_working_memory(session_id="", embedder=None, db=db)

    # Two adapter instances, one model id: the primary thread and the
    # learning centre pipeline are separate consumers architecturally (see
    # LCPipeline below), but both resolve through the same guarded call with
    # no argument, so both are provably MODEL_ID (src.sim.model_profile's
    # "no model-choice mechanic" - see its module docstring).
    primary_adapter = guarded_create_llm(settings)
    lc_adapter = guarded_create_llm(settings)

    # thread.py compacts every turn with a second billed call whose prompt
    # asks for "[user] what they said / [engine] what you did". In a town
    # there is no user and no engine, and the model answers in the genre it
    # is asked for: run 19 has Tobias recording that "user placed me at
    # h_quarry" and Ainsley inventing a pantry of "3 grain, 2 meat" that no
    # resident held, both written into their own memories and reasoned from
    # afterwards. 165 of that run's 810 calls were this summariser - a
    # fifth of the calls and $0.43 - so it is not a side channel.
    #
    # thread.py is frozen and correct for its own product, so the prompt is
    # rewritten on its way out, exactly as tool_surface.py takes the same
    # engine's assistant-shaped tools off a resident's registry.
    primary_adapter = speak_as_resident(primary_adapter, agent_name=profile.agent_id)

    # ENG-21. A monologue that runs into the 768-token response cap must not
    # buy a second model call out of the world's two-call floor.
    #
    # thread.py's MAX_TOKENS recovery `continue`s the SAME bounded loop the
    # seam passes the world's call budget into as `max_turns`, so one
    # over-long sentence became "You ran out of energy for thinking last
    # turn after 2 steps" with nothing attempted. MAX_CONTINUATIONS is a
    # local variable in a frozen file; the adapter is built here, so the
    # cap is applied where the stop reason crosses the seam. See
    # sim/bounded_monologue.py for the whole argument.
    #
    # Wrapped around speak_as_resident rather than under it: the two touch
    # different methods (`stream_with_tools` here, `generate` there) and
    # each proxies everything else down, so the order is free - but
    # `forget_the_cached_tool_wire` now has two links to walk to reach the
    # provider, which is why it walks the whole chain.
    primary_adapter = end_the_turn_on_a_cut_monologue(primary_adapter)

    # A tool call whose arguments run into that same 768-token cap is the
    # half bounded_monologue deliberately left alone. `herald/provider.py`
    # parses the accumulated arguments unguarded at `content_block_stop`,
    # so incomplete JSON raises out of the stream and the coordinator fails
    # the whole task - 41 turns across 33 residents in the 50-resident run,
    # every one a billed call that did nothing. Outermost, so it also
    # covers anything the wrappers below it may raise on the same stream.
    primary_adapter = keep_the_turn_on_a_half_written_call(primary_adapter)

    # The other two ways a turn goes quiet, neither of which raises.
    #
    # `app/proof/agility.py` sends this chain streams a different provider
    # would produce. A tool block whose `content_block_start` never
    # arrives is discarded by herald without an error while `message_stop`
    # still says `tool_use` - the turn claims to have used tools it did
    # not deliver. And a call written out in a markup the broker does not
    # recover (it recovers Kimi's; the next model will not use Kimi's)
    # is billed, produces text, and changes nothing.
    #
    # This counts them. It does not recover them: the tool's name never
    # crossed the wire in the first case, and guessing at an unknown
    # markup in the second is how you call the wrong verb. Control flow
    # is untouched - `thread.py` already ends the turn on
    # `not turn_tool_calls`. What was wrong was the record, which made a
    # turn that tried and failed look like a resident that chose not to.
    #
    # Outermost, so it sees the stream every wrapper below it produced.
    primary_adapter = notice_a_turn_that_tried_to_act(primary_adapter)

    emitter = EventEmitter()

    world = world_client or world_tools.WorldClient(world_base_url, agent_id=profile.agent_id)
    tools: ToolRegistry = await world_tools.build_world_tool_registry(world)

    # Session log (The 250 section 32): one JSON line per turn, four
    # required fields (input, reasoning, actions, done status), for every
    # engine process without exception. Subscribes to `emitter` - a
    # public seam the frozen engine already emits ThinkingDelta/
    # ToolExecution/ExecutionSummary events through - so nothing under
    # src/engine_core/ changes. `recall_session_log` is registered onto
    # the same tool registry the world:* tools live on, so the model can
    # call it exactly like any other tool if its context is fresh or it's
    # lost track of what it was doing.
    session_log_path = Path(profile.september_home) / "session_log.jsonl"
    session_log = SessionLogWriter(profile.agent_id, session_log_path)
    session_log.attach(emitter)
    tools.register(build_session_log_tool(session_log_path))

    # The memory stream (memory_stream.py) subscribes to the same emitter
    # and turns each completed turn into observation / plan / reflection
    # records. Until this existed the `memories` table - queried by
    # world/agent_viewer_api.py since genesis - had no writer at all, so
    # every agent's memory panel was permanently empty.
    # The sink is what connects the two halves. Without it the stream wrote
    # a file on this engine's disk and stopped there - world/wiring.py's
    # TraceStore.record_memory_if_new subscribes to `agent.memory.recorded`
    # and would never have seen one.
    memory_stream_path = Path(profile.september_home) / MEMORY_STREAM_FILENAME
    memory_stream = MemoryStreamWriter(
        profile.agent_id,
        memory_stream_path,
        sink=world.record_memory,
    )
    memory_stream.attach(emitter)
    # Writing memories without a way to read them back is a log, not a memory
    # stream. `recall_memories` is the reader, registered onto the same
    # registry the world:* tools live on so the model can call it like any
    # other tool - and granted below, because check_permission() denies a
    # registered tool by default.
    tools.register(build_memory_stream_tool(memory_stream_path))

    # The second question a resident can ask its own memory.
    #
    # Recall runs once per turn, automatically, on what the resident was
    # just told. If it misses there was no way to ask again - and
    # `Search.unified()`, the hybrid cross-store retrieval that would
    # answer, has had no callers anywhere in the tree since it was
    # written. It is also the ONLY path that reads `learning_signals`, the
    # table `world_corrections.py` now writes every world refusal into, so
    # without this a correction issued this morning is durable,
    # searchable, and unreachable until the next batch runs.
    #
    # Built with the signal store and nothing else: this process wires no
    # episodic, knowledge or social subsystem (see this module's own
    # docstring), so `unified()` degrades to its signals leg, which guards
    # its own absence. That is honest rather than clever - what a resident
    # can search is what this engine actually keeps.
    from src.engine_core.search import Search
    from src.learning_centre.signals import SignalStore
    from src.sim.memory_query import (
        build_search_memory_tool,
        install_search_memory_permissions,
    )

    tools.register(build_search_memory_tool(Search(db, signal_store=SignalStore(db))))

    # RC-1 (ROADMAP.md section 1.2): without a consciousness_mgr, thread.py
    # leaves sys_prompt=None for every turn - a model handed 30 world:*
    # tool schemas and no identity does what a tool-using model does with
    # an empty prompt: treats perception text as a task and emits an
    # orchestration plan, not a person living in a town. bootstrap.py's own
    # ConsciousnessManager wiring (its Soul/SocialGraph/EpisodicMemory/
    # KnowledgeStore path) renders the generic Sept engine identity, not a
    # Kasukabe persona - CORE_SOUL never reads genesis.json. So this
    # process builds its own ConsciousnessManager with no engine memory
    # subsystems attached (every one is Optional and build() no-ops on
    # None, per that class's own code) and pre-registers ONE stable
    # section carrying this agent's real persona text. thread.py's
    # register_prompt_sections() only fires "if not
    # self.consciousness_mgr.sections" (thread.py:2796), so registering
    # here first is what keeps the frozen engine's generic identity from
    # ever overwriting this section - no change to thread.py itself.
    #
    # The persona text itself lives on disk, not in an env var: world/
    # run_genesis.py's start_agents() writes it to
    # "<home_root>/<agent_id>/persona.txt" (= profile.september_home)
    # before this process is spawned - the same directory SQLite already
    # lives under, so no new env var or wire contract is needed.
    # ENG-01. Build the stores the learning centre writes into.
    #
    # This line used to be `MemorySystem(working_memory=working_memory)` and
    # nothing else, and `ConsciousnessManager()` with no arguments. Every
    # other component was None, and `MemorySystem` raises RuntimeError on a
    # write to a None component - thirteen raise sites. The learning
    # pipeline catches those into `result.errors` and carries on, so the
    # batch reported success while writing nothing: run 23 shows
    # `auto:learning_centre.*` calls at 55-108K input tokens returning 6-14
    # output tokens, over and over, for nothing. Run 22 left 7,138 signals
    # unconsolidated.
    #
    # `consolidate()` returns early when no episode and no knowledge was
    # written, so soul updates never ran either. Every compounding quantity
    # this project measures - soul drift, the social graph, C1/C4/C5, skill
    # evolution - was zero by construction rather than by run length.
    #
    # All four stores take `embedding_service=None` and skip the vector
    # insert when there is no embedder, so keyword search still works and
    # the town pays for no embedding calls. The tables already exist:
    # `migrations/001_memory_schema.sql` creates them on the engine DB.
    episodic_memory = EpisodicMemory(db, None)
    knowledge_store = KnowledgeStore(db, None)
    social_graph = SocialGraph(db, None)
    soul = Soul(db)

    consciousness_mgr = ConsciousnessManager(
        # Passed so the DYNAMIC layer has something to render. With all four
        # None it rendered nothing, which is why a resident's prompt never
        # carried what it had learned.
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
    # Deliberately never calling coordinator.set_router(): self.router stays
    # None, ThreadRouter is never constructed, DISPATCH_MODEL /
    # DISPATCH_MODEL_ALTERNATIVE are never resolved into an adapter by
    # anything in this process (see module docstring).

    # ENG-02. BEFORE get_or_create_thread, which is what propagates the
    # store onto the thread. The operator's decision of 2026-09-04: history
    # is bounded in the seam, by a rule we can state, instead of by an
    # emergency compaction that fired on every call in all 23 runs.
    kept = bound_history_in_the_seam(coordinator)
    # ENG-04. Also before get_or_create_thread: the thread copies the store
    # reference, so a swap afterwards leaves it writing through the old one.
    #
    # Coordinator spawns flush_signals after EVERY turn, and flush_wm
    # re-inserted the whole working-memory log each time with no watermark -
    # turn 50 re-writing the 49 turns before it. TEST_RUN.md measured the
    # result: 118,179 replayed records.
    if bound_signal_growth(coordinator):
        logger.info("signal flushing watermarked: each WM entry is stored once")
    logger.info("history bounded in the seam: %d most recent messages", kept)

    thread_id = pinned_thread_id(profile.agent_id)
    thread = await coordinator.get_or_create_thread(thread_id)
    # AFTER the thread is built, because that call is what registers the
    # engine's own built-ins onto the registry this module passed in.
    # Seven of them describe a user, a mission brief and a task content
    # store, none of which exist in a town - see tool_surface.py for the
    # measurement and for why the removal belongs here rather than in
    # frozen thread.py.
    prune_to_the_towns_tools(thread)
    world_tools.install_permissions(thread)
    install_session_log_permissions(thread)
    install_memory_stream_permissions(thread)
    # The fourth. `search_memory` was registered here and never granted a
    # rule, so `check_permission()` denied every call before the handler -
    # 29 of them across civic12 and the two 50-resident runs, each one
    # billed, answered nothing, and recorded with no status at all because
    # a denial fires no result event. See memory_query.py's rule for why
    # the silence is what kept it hidden.
    install_search_memory_permissions(thread)

    # Pin BEFORE the first turn. Coordinator.set_thread_pinned requires the
    # index entry to already exist, which normally only happens after a turn
    # completes (see Coordinator.update_thread_index) - seeding the entry
    # directly, with pinned=True set up front, means the very first tick
    # already gets pin semantics: exempt from the idle reaper and from
    # ask_user's wait timeout, and (defense in depth) enforce_pins would
    # downgrade a stray triage-routed segment aimed here even though this
    # thread_id is never fed through triage at all in this topology.
    coordinator.thread_index[thread_id] = ThreadIndexEntry(
        thread_id=thread_id,
        pinned=True,
        summary=f"Kasukabe agent {profile.agent_id}",
    )

    memory_system = MemorySystem(
        working_memory=working_memory,
        # ENG-01: the four that were None. See the block above.
        episodic_memory=episodic_memory,
        knowledge_store=knowledge_store,
        social_graph=social_graph,
        soul=soul,
    )
    from src.learning_centre.pipeline import LCPipeline

    # The pipeline's four encoding prompts describe "a personal AI
    # assistant's memory" and ask for facts about "the user, their team,
    # tools, environment" - 'deploys on Tuesdays', 'checks Grafana first'.
    # A miller has none of those and there is no user, so run 25's batches
    # cast Rosalind Vance as the operator of an assistant and wrote her
    # morning up as a productivity report: "The user exhibits consistent
    # location-based workflow optimization". That is what she then recalls
    # as her own memory, and there is nothing in it she can act on.
    #
    # Installed BEFORE the pipeline is constructed, which is before the
    # first `SimLearningGate.on_tick`. It rebinds module attributes the
    # frozen pipeline reads at call time; it edits nothing in
    # learning_centre/, so Law 1 holds. See resident_learning_prompts.py.
    from src.sim.resident_learning_prompts import install as install_resident_prompts

    install_resident_prompts()

    lc_pipeline = LCPipeline(
        adapter=lc_adapter,
        db=db,
        memory_system=memory_system,
        settings=settings,
        # ENG-01: when a batch actually writes something, the cached prompt
        # prefix is stale - it no longer reflects what this resident knows.
        on_data_changed=consciousness_mgr.invalidate,
    )
    # The interval is passed HERE, and that is the whole fix. The gate has
    # had this field since the cadence work and this line constructed it
    # with the default of 0, so the waking interval never once ran and
    # `learning_ran` stayed zero in every session log - the same
    # built-and-never-invoked failure the evaluation spec's L1 layer exists
    # to catch. See config.learn_awake_interval_ticks.
    learning_gate = SimLearningGate(
        pipeline=lc_pipeline,
        awake_interval_ticks=learn_awake_interval_ticks(),
        # Evaluation spec section 5.2. `genesis.py` has assigned this
        # resident an arm since genesis was written and nothing honoured
        # it, so the `no_learning` control behaved exactly like the
        # treatment and C1 was a correlation dressed as a comparison.
        learns=resident_learns(),
    )

    # ROADMAP T4, the world as corrector. Every refusal the world issues
    # arrives at WorldClient.call_tool and has, until now, gone no further
    # than the text the resident reads that turn - so a town that refused
    # the same resident the same way every morning taught it nothing
    # between mornings, and the evaluation spec's C5 ("correction signals
    # emitted by the world and consumed by the pipeline") could not exceed
    # zero. The store is the one the learning centre already batches from,
    # so a refusal recorded here is consolidated on the next sleep tick
    # with no second pipeline and no new schema.
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
        # Deliberately 0, not the world's current version: boot must not add a
        # network call of its own. The first tick sees a mismatch, refetches
        # once, registers nothing (it already has these), and settles. One
        # redundant fetch per engine lifetime buys a boot path that stays
        # exactly as reachable as it was.
    )
