"""Sim-agent adapter: the world server's tick/perception receiver over HTTP.

Task 5's "thin sim-agent adapter": build_sim_agent_app() wraps ONE
already-booted SimEngineState (pinned_boot.boot_sim_engine) in a small
FastAPI app the world server calls once per tick this agent needs to act
on. Three routes:

    POST /tick   -> drives the pinned thread (awake) or the learning-centre
                    sleep gate (asleep); never both in the same tick
    POST /learn  -> one learning batch now, whatever the clock says. The
                    runner calls it for every resident at shutdown, so the
                    last stretch of a run is consolidated instead of killed
    GET  /health -> liveness + the one model this agent (and every herald
                    consumer inside it) is pinned to

Tool-call translation (world_tools.py) already happened by the time /tick
returns a response: every world tool the model called during the turn was
already POSTed to the world server via the ToolDef handlers wired in
pinned_boot.py. The /tick response is a tick summary for the world server's
own bookkeeping (what did the agent say, did learning run), not a second
channel tool calls travel through.

DESIGN.md section 1: "Agents sleep ~8 game-hours (no interactive LLM calls;
learning pipeline runs then)." req.is_sleeping therefore gates which work
gets to run at all this tick, not just whether learning happens: a sleeping
agent's perception is recorded (world server's job, not ours) but never
reaches the primary adapter.

What a tick costs in wall-clock is this module's problem, because the
runner puts a deadline on it. An awake tick is one turn and nothing else:
the learning batch is started after that turn and is not waited for (see
BackgroundLearning, ENG-17). A sleeping tick is the batch and nothing
else, and waits for it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.sim.goal_reflection import GoalReflection
from src.sim.model_profile import MODEL_ID
from src.sim.pinned_boot import SimEngineState

logger = logging.getLogger(__name__)

# The world's own clock: `world/core/clock.py` divides a game-day into 144
# ten-game-minute ticks. Only used as the fallback when a caller does not
# send `game_day`, and named rather than inlined so the assumption is
# visible - a hard-coded 144 buried in an expression is how a night
# quietly becomes something other than a night.
TICKS_PER_DAY = 144


class TickRequest(BaseModel):
    tick: int = Field(
        ..., description="World server's tick counter (DESIGN.md section 1: 10 game-minutes each)."
    )
    is_sleeping: bool = Field(
        False, description="True during this agent's ~8 game-hour sleep window."
    )
    perception: str = Field(
        "", description="Free-text perception/event summary for this tick."
    )
    game_day: int | None = Field(
        None,
        description=(
            "ENG-24: which game-day this tick belongs to, so the sleep "
            "reflection can run once per resident per NIGHT rather than "
            "once per sleeping tick. Falls back to tick // 144 when the "
            "caller does not send it, which is the clock the world uses."
        ),
    )
    call_budget: int | None = Field(
        None,
        description=(
            "B3: how many model calls this tick may make, computed by the "
            "world from the resident's own wallet. None leaves the engine's "
            "default in place."
        ),
    )


class TickResponse(BaseModel):
    thread_id: str
    tick: int
    is_sleeping: bool
    reply: str
    learning_ran: bool
    # B3. The world owns the wallet and therefore owns the sentence a
    # resident reads about it; the engine reports what it used and lets the
    # world decide what to say. See world/call_budget.py.
    steps_used: int = 0
    budget_hit: bool = False
    # ENG-24. What the night's reflection actually changed, not that it
    # was attempted - run 22's lesson, where "20 of 20 consolidated" sat
    # over zero written rows because `result is not None` read as success.
    reflected: bool = False
    goal_revisions: int = 0


def _is_unconfigured(error: object) -> bool:
    """Whether a pipeline error is 'the store was never built' (ENG-01).

    `MemorySystem`'s thirteen raise sites all say some version of "not
    configured" when a component is None. Matching on the text is ugly, and
    it is what the pipeline gives us: it stringifies the exception into
    `result.errors` rather than keeping the type.
    """
    text = str(error).lower()
    return "not configured" in text or "is none" in text


class LearnResponse(BaseModel):
    """POST /learn: one batch, on demand. See the route for why it exists."""

    thread_id: str
    learning_ran: bool
    runs_triggered: int
    # What the batch actually ENCODED, not merely that it was attempted.
    #
    # Run 22 drained twenty of twenty residents 235ms after the broker
    # halted dispatch, reported "20 of 20 consolidated", and wrote zero
    # knowledge rows while 7,138 signals stayed unconsolidated. Every
    # model call the pipeline made was refused, the pipeline returned a
    # result object anyway, and `result is not None` read that as success.
    # A green line over work that did not happen is worse than a red one,
    # because nobody investigates it.
    knowledge_written: int = 0
    errors: int = 0


class BackgroundLearning:
    """The learning batch, moved off the tick's critical path.

    ENG-17. `/tick` used to `await learning_gate.on_tick(False)` and only
    then call `send_tick`. That batch is five to eight model calls of up to
    108K input tokens each (run 23), it draws on the same kimi lane as the
    turn, and it sat inside the runner's `tick_timeout_s` - so a resident
    could spend its whole dispatch window consolidating yesterday, be
    abandoned mid-batch by the runner's `asyncio.wait_for`, and never take
    the turn at all. That abandonment is ENG-12, the finding this one
    shares a budget with; consolidation is bookkeeping about turns that
    have already happened, and it was being paid for out of the turn that
    had not happened yet.

    So the batch is started after the turn returns, and not awaited. Three
    consequences, all deliberate:

      - Only one batch at a time. A tick arriving while one is still
        running starts nothing and waits for nothing.
      - The batch now overlaps the NEXT tick's turn on the same lane
        rather than delaying this one. That is the trade the review asked
        for: the turn's latency is bounded and the batch's is not, because
        only one of the two has a resident waiting on it.
      - `learning_ran` reports the batch that FINISHED since the last
        tick. The runner reads that field and prints it as what the town
        learned, so it must never claim a result the engine does not have
        yet; a batch started on this tick is reported on the next one.
    """

    def __init__(self, gate: Any) -> None:
        self._gate = gate
        # Held so the task is not garbage-collected mid-flight: asyncio
        # keeps only a weak reference to a running task.
        self._task: asyncio.Task | None = None
        self._landed = False

    @property
    def in_flight(self) -> bool:
        return self._task is not None and not self._task.done()

    def start_after_the_turn(self) -> bool:
        """Schedule one batch. Returns whether this call started it."""
        if self.in_flight:
            logger.debug("learning batch still running; not starting a second")
            return False
        self._task = asyncio.create_task(self._in_the_background())
        return True

    async def run_while_asleep(self) -> bool:
        """Run a batch and wait for it.

        A sleeping tick takes no turn (DESIGN.md section 1), so there is no
        turn latency to protect and the sleep window is the pipeline's own.
        Its result is therefore known when the response is written and can
        be reported on the spot, exactly as it always was.
        """
        await self.settle()
        return await self._one_batch(True)

    async def settle(self) -> None:
        """Wait for an in-flight batch to finish, if there is one.

        Called before the sleep-onset batch and before the end-of-run
        drain, so this engine never has two pipelines writing the same
        stores - and, at the drain, never one still writing while the
        SQLite pool is being closed underneath it.
        """
        task = self._task
        if task is not None and not task.done():
            await task

    def take_landed(self) -> bool:
        """Whether a background batch has completed since this was last
        asked. Read once per tick, into the response."""
        landed, self._landed = self._landed, False
        return landed

    async def _in_the_background(self) -> None:
        if await self._one_batch(False):
            self._landed = True

    async def _one_batch(self, is_sleeping: bool) -> bool:
        try:
            result = await self._gate.on_tick(is_sleeping)
            return result is not None
        except Exception as exc:  # noqa: BLE001
            # `SimLearningGate._run` already swallows a pipeline failure,
            # but this is now an unawaited task, and an unawaited task that
            # raises is a warning at interpreter shutdown that nobody
            # reads. A failed consolidation must not be able to reach the
            # turn, and it cannot reach it from here.
            logger.warning("background learning batch failed: %s", exc)
            return False


class HealthResponse(BaseModel):
    agent_id: str
    thread_id: str
    # WIRE-02. How this engine's link to the world has been behaving.
    #
    # Zeroes on a healthy engine, so a runner reading it sees nothing
    # until there is something to see. Run `deni-a6f9eb4-5h` lost 97
    # calls to transport with no instrument anywhere reporting it.
    world_calls_attempted: int = 0
    world_calls_refused: int = 0
    world_transport_failures: int = 0
    world_transport_recovered: int = 0
    # Singular by design: every herald consumer this agent has (main thread
    # and learning centre alike) is pinned to this one model id. Two fields
    # here would misstate the architecture even if both held the same
    # value - see src/sim/model_profile.py's module docstring.
    model: str


def build_sim_agent_app(engine: SimEngineState) -> FastAPI:
    """One FastAPI app per booted agent. Not a shared multi-agent server -
    each of the 250 microVMs (DESIGN.md section 4) runs its own engine
    process and therefore its own instance of this app."""

    app = FastAPI(title=f"kasukabe-sim-agent-{engine.profile.agent_id}")

    # ENG-17. The one place a learning batch is started, so there is one
    # answer to "is a batch running" and one place that answer is kept.
    # Put on app.state so a caller (and a test) can wait for it without
    # reaching into the route's closure.
    learning = BackgroundLearning(engine.learning_gate)
    app.state.background_learning = learning

    # ENG-24. Same reasoning as the line above: one object, one answer to
    # "has this resident reflected tonight", reachable from a test.
    reflect_on_goals = GoalReflection(engine)
    app.state.goal_reflection = reflect_on_goals

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        wire = engine.world.wire_health() if engine.world is not None else {}
        return HealthResponse(
            agent_id=engine.profile.agent_id,
            thread_id=engine.thread_id,
            model=MODEL_ID,
            **wire,
        )

    @app.post("/tick", response_model=TickResponse)
    async def tick(req: TickRequest) -> TickResponse:
        # CORE-11. A tick that arrives while a turn is running is REFUSED,
        # not queued.
        #
        # What happened before: `send_tick` takes `_turn_lock`, so a second
        # POST blocked on it. The runner's `asyncio.wait_for` gave up after
        # `tick_timeout_s` and marked the agent decided - but uvicorn does
        # not cancel a handler when its client disconnects, so the first
        # turn kept running and kept being billed, and the second request
        # kept waiting. Three ticks later the runner POSTed again, that
        # request queued behind the same lock, and when it finally ran it
        # ran a turn against a perception several ticks stale. Money spent,
        # actions taken late, and `timed_out_ticks` incremented with no way
        # to attribute the late tool calls to anything. Run 23: ten turns.
        #
        # 409 rather than 429: this is not a rate limit the caller should
        # back off and retry on a timer, it is a statement that the
        # resource is in a conflicting state right now. The runner reads it
        # as "still busy" and skips the resident for this tick, which is
        # the same outcome its timeout-based guess reached - except in
        # milliseconds instead of `tick_timeout_s`, and by knowing instead
        # of assuming.
        if engine.turn_in_flight:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "turn_in_flight",
                    "agent_id": engine.profile.agent_id,
                    "tick": req.tick,
                    "message": (
                        "a turn is already running for this agent; this tick "
                        "was refused rather than queued behind it, because a "
                        "queued tick runs against a stale perception"
                    ),
                },
            )

        if req.is_sleeping:
            # No interactive LLM calls while asleep (DESIGN.md section 1) -
            # only the sleep-onset learning batch, on the same kimi-k2.5
            # model, may run. It is awaited: there is no turn on this path,
            # so there is no turn latency for it to spend.
            ran = await learning.run_while_asleep()
            # ENG-24, §5.3: "sleep reflection posts revise_goals once per
            # resident per night on the LC adapter."
            #
            # AFTER the learning batch, not before: the batch is what
            # turns the day's signals into something durable, and a
            # resident reflecting on a day it has not consolidated yet is
            # reflecting on less. Both are sleep work and neither has a
            # turn waiting on it, so the order is free to be the right
            # one.
            #
            # Its failures never reach this response as an exception -
            # `reflect` swallows them and reports - because a night's
            # thinking is not worth losing the learning batch over.
            night = req.game_day if req.game_day is not None else req.tick // TICKS_PER_DAY
            reflection = await reflect_on_goals.reflect(night)
            return TickResponse(
                thread_id=engine.thread_id,
                tick=req.tick,
                is_sleeping=True,
                reply="",
                learning_ran=ran,
                reflected=reflection.ran,
                goal_revisions=reflection.revisions,
            )

        # Awake: the turn first, and the batch after it and off the wire.
        #
        # ENG-17. This used to await the gate HERE, before the turn, so a
        # resident's dispatch window was spent consolidating yesterday
        # before it was allowed to act today. See BackgroundLearning.
        reply = await engine.send_tick(req.perception, call_budget=req.call_budget)
        learning.start_after_the_turn()
        return TickResponse(
            thread_id=engine.thread_id,
            tick=req.tick,
            is_sleeping=False,
            reply=reply,
            # What LANDED, not what was just started. This field used to be
            # hardcoded False, which was true while the gate only fired at
            # sleep onset and became a lie the moment the waking interval
            # was wired - a batch could run on an awake tick and every run
            # report would still say the town learned nothing. The runner
            # reads exactly this field (HttpAgentGateway.send_tick), so it
            # must report a result the engine actually has: a batch started
            # on this tick is reported on the next.
            learning_ran=learning.take_landed(),
            steps_used=engine.last_turn_steps,
            budget_hit=engine.last_turn_hit_budget,
        )

    @app.post("/learn", response_model=LearnResponse)
    async def learn() -> LearnResponse:
        """Run one learning batch now, whatever the clock says.

        The end-of-run drain. A run that stops at noon leaves a morning's
        signals sitting in `learning_signals` with `processed = 0`, and the
        engine process is killed seconds later - so the last stretch of every
        run has always been thrown away. The runner calls this for every
        resident before it tears the supervisor down.

        Never raises: `trigger_extra` swallows a pipeline failure and returns
        None, and a shutdown path that can fail is a shutdown path that
        leaves engines running.
        """
        # ENG-17. The drain now has to consider a batch it did not start:
        # since the awake batch runs in the background, one may still be
        # writing when the runner calls this, and the process is killed
        # seconds after it answers.
        await learning.settle()
        result = await engine.learning_gate.trigger_extra()
        # ENG-13. `learning_ran` meant two different things on the two
        # routes - `/tick` reported "a batch was attempted", `/learn`
        # reported "something was written" - and the sim suite was red on
        # the difference. One meaning now, the stricter one, on both.
        #
        # `learning_ran` now means "something was learned", not "a batch was
        # attempted" - see LearnResponse for the run that made the
        # distinction necessary.
        written = int(getattr(result, "knowledge_written", 0) or 0)
        episodes = int(getattr(result, "episodes_created", 0) or 0)
        failures = list(getattr(result, "errors", []) or [])
        # ENG-01. A batch that wrote nothing BECAUSE nothing was configured
        # is a different failure from a batch that had nothing to say, and
        # for 23 runs the two looked identical from here.
        #
        # `MemorySystem` raises RuntimeError on a write to a None component;
        # the pipeline catches those into `errors` and carries on. So a
        # resident could burn 55-108K input tokens on a consolidation batch,
        # return 6 output tokens, write not one row, and report a clean
        # `learning_ran=False` that read as "quiet morning". Run 22 left
        # 7,138 signals unconsolidated exactly this way.
        if failures and all(_is_unconfigured(f) for f in failures):
            logger.error(
                "learning wrote NOTHING because its stores are not "
                "configured (%d errors, all 'not configured'). This is a "
                "wiring fault, not a quiet resident: every compounding "
                "measurement - soul drift, the social graph, C1/C4/C5 - is "
                "zero by construction until it is fixed. First error: %s",
                len(failures), failures[0],
            )
        return LearnResponse(
            thread_id=engine.thread_id,
            learning_ran=bool(result is not None and (written or episodes)),
            runs_triggered=engine.learning_gate.runs_triggered,
            knowledge_written=written,
            errors=len(failures),
        )

    return app
