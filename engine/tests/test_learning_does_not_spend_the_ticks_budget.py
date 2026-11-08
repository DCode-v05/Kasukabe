"""ENG-17: the learning batch ran inline, before the turn, inside the
dispatch budget.

From `docs/review/10-engine-seam-and-mind.md`: "`agent_adapter.py:141-142`
awaits `learning_gate.on_tick(False)` (5-8 model calls, up to 108K tokens
each) before `send_tick`; it counts against the same 120 s and the same
Kimi lane. Fix: run it as a background task after the turn."

The two halves of that sentence are separate harms and this file pins
both. The batch is consolidation - bookkeeping about turns that have
already happened - and it was spending the seconds the runner had
allocated to the turn that had not happened yet. A resident could burn its
whole `tick_timeout_s` consolidating yesterday, be abandoned mid-batch by
`asyncio.wait_for`, and never take the turn at all; the engine meanwhile
kept running and kept being billed, which is ENG-12, the finding this one
shares a budget with. And because the await came FIRST, the cost was paid
before the turn even began, so the more a resident had to consolidate the
less likely it was to act.

`learning_ran` on the response therefore changes meaning, and the change
is the honest one: it reports the batch that FINISHED since the last tick
rather than one whose outcome is not known yet. The runner reads exactly
this field, so it must never claim a result the engine does not have.

The first test is the wiring test. Every other test here would stay green
if the route stopped scheduling anything at all, because they drive the
scheduler; that one drives `POST /tick` and watches the order the two
things actually happen in.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from src.sim.agent_adapter import build_sim_agent_app


class _Profile:
    agent_id = "a0042"


class _Gate:
    """Shaped like SimLearningGate, and slow on purpose.

    The real batch is five to eight model calls. A fake that returns
    instantly cannot show that the turn no longer waits for it, so this one
    blocks until the test lets it go.
    """

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[bool] = []
        self.extra_calls = 0
        self.runs_triggered = 0
        self.result: object | None = None
        self.raises = False
        self.hold: asyncio.Event | None = None

    async def on_tick(self, is_sleeping: bool):
        self.calls.append(is_sleeping)
        if self.hold is not None:
            await self.hold.wait()
        self.order.append("batch")
        self.runs_triggered += 1
        if self.raises:
            raise RuntimeError("the learning model was briefly unavailable")
        return self.result

    async def trigger_extra(self):
        self.extra_calls += 1
        self.order.append("drain")
        return None


class _Engine:
    """The smallest thing `build_sim_agent_app` accepts."""

    def __init__(self) -> None:
        self.profile = _Profile()
        self.thread_id = "kasukabe-agent-a0042"
        self.order: list[str] = []
        self.learning_gate = _Gate(self.order)
        self.last_turn_steps = 3
        self.last_turn_hit_budget = False
        self.turn_in_flight = False

    async def send_tick(self, perception: str, call_budget: int | None = None) -> str:
        self.order.append("turn")
        return f"reply to: {perception}"


def _tick(tick: int, *, sleeping: bool = False) -> dict:
    return {
        "tick": tick,
        "is_sleeping": sleeping,
        "perception": "you are in the square",
        "call_budget": 4,
    }


def client_for(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://engine")


async def post(client: AsyncClient, path: str, **kwargs):
    """Every request in this file has a deadline.

    Reverting the fix makes the handler await a gate this file holds shut,
    so an unbounded request would hang the whole suite rather than fail it -
    and nobody reads a run that never finishes.
    """
    return await asyncio.wait_for(client.post(path, **kwargs), timeout=5.0)


async def let_the_loop_run(times: int = 30) -> None:
    """Give every pending task a chance to reach whatever it blocks on.

    A single `sleep(0)` yields once, which is not enough for an ASGI
    request to travel from `client.post` into the route handler.
    """
    for _ in range(times):
        await asyncio.sleep(0)


# ── the wiring test ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tick_answers_while_the_batch_is_still_running():
    """THE WIRING TEST. Drives the real `POST /tick` through the real ASGI
    app, so the scheduling is the route's doing and not this test's.

    The gate is held open for the whole request, which is what makes this
    decisive rather than merely suggestive: a batch awaited ANYWHERE inside
    the handler - before the turn as the finding found it, or after the
    turn, which would still sit inside `tick_timeout_s` - cannot answer at
    all while the gate is held, and the `wait_for` below fails the test.
    Only a batch that is started and not awaited lets the response out.

    Then the hold is released and the batch is allowed to finish, because
    a "fix" that moved consolidation off the tick and dropped it would be
    worse than the finding: the run reports would go on saying the town
    learned nothing, and this time they would be right.
    """
    engine = _Engine()
    engine.learning_gate.hold = asyncio.Event()
    app = build_sim_agent_app(engine)

    async with client_for(app) as client:
        resp = await post(client, "/tick", json=_tick(1))

        assert resp.status_code == 200
        assert resp.json()["reply"] == "reply to: you are in the square"
        assert engine.order == ["turn"], (
            "ENG-17: the learning batch ran inside the tick. It is 5-8 model "
            "calls of up to 108K tokens on the same kimi lane, inside the "
            f"same tick_timeout_s the turn has to finish in (order: {engine.order})"
        )

        engine.learning_gate.hold.set()
        await app.state.background_learning.settle()

    assert engine.order == ["turn", "batch"], (
        "the batch was moved off the tick and then never ran at all - "
        "consolidation deleted is worse than consolidation mistimed"
    )


# ── what the response may honestly claim ─────────────────────────────────


@pytest.mark.asyncio
async def test_learning_ran_reports_the_batch_that_landed_not_the_one_just_started():
    """The runner reads this field and prints it as what the town learned.
    A batch whose result is not known yet cannot be reported either way, so
    it is reported on the tick after it lands."""
    engine = _Engine()
    engine.learning_gate.result = "a batch ran on the waking interval"
    app = build_sim_agent_app(engine)

    async with client_for(app) as client:
        first = await post(client, "/tick", json=_tick(1))
        assert first.json()["learning_ran"] is False, (
            "the tick that STARTED the batch cannot know how it went"
        )

        await app.state.background_learning.settle()

        second = await post(client, "/tick", json=_tick(2))
        assert second.json()["learning_ran"] is True, (
            "a batch that ran was never reported - three run reports have "
            "already read that zero as 'the residents did not learn'"
        )

        await app.state.background_learning.settle()
        third = await post(client, "/tick", json=_tick(3))
        assert third.json()["learning_ran"] is True

        # And it does not latch on forever: the fourth tick reports the
        # batch the third one started, and then there is nothing left.
        await app.state.background_learning.settle()


@pytest.mark.asyncio
async def test_a_quiet_gate_is_reported_as_no_batch():
    """The gate decides whether a batch runs at all (sleep onset, or the
    waking interval). When it declines, nothing is claimed."""
    engine = _Engine()
    engine.learning_gate.result = None
    app = build_sim_agent_app(engine)

    async with client_for(app) as client:
        resp = await post(client, "/tick", json=_tick(1))
        await app.state.background_learning.settle()
        second = await post(client, "/tick", json=_tick(2))

    assert resp.json()["learning_ran"] is False
    assert second.json()["learning_ran"] is False


# ── one batch at a time ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_tick_does_not_start_a_second_batch():
    """A batch outliving a tick is the normal case now, and two of them on
    one resident's kimi lane would be the cost this finding is about,
    doubled."""
    engine = _Engine()
    engine.learning_gate.hold = asyncio.Event()
    app = build_sim_agent_app(engine)

    async with client_for(app) as client:
        await post(client, "/tick", json=_tick(1))
        await post(client, "/tick", json=_tick(2))
        await post(client, "/tick", json=_tick(3))
        await let_the_loop_run()

        assert engine.learning_gate.calls == [False], (
            "three ticks started three overlapping batches"
        )
        engine.learning_gate.hold.set()
        await app.state.background_learning.settle()

    assert engine.order == ["turn", "turn", "turn", "batch"]


# ── the turn is never harmed by the batch ────────────────────────────────


@pytest.mark.asyncio
async def test_a_batch_that_raises_never_reaches_the_turn():
    """`SimLearningGate._run` already swallows a pipeline failure, but the
    batch is now an unawaited task and an unawaited task that raises is a
    warning nobody reads. It is caught where it is started."""
    engine = _Engine()
    engine.learning_gate.raises = True
    app = build_sim_agent_app(engine)

    async with client_for(app) as client:
        resp = await post(client, "/tick", json=_tick(1))
        await app.state.background_learning.settle()
        after = await post(client, "/tick", json=_tick(2))

    assert resp.status_code == 200
    assert resp.json()["reply"] == "reply to: you are in the square"
    assert after.status_code == 200
    assert after.json()["learning_ran"] is False


@pytest.mark.asyncio
async def test_a_sleeping_tick_still_consolidates_before_it_answers():
    """DESIGN.md section 1 gives the pipeline the sleep window, and a
    sleeping tick takes no turn - so there is no turn latency to protect
    and the sleep-onset batch keeps its old, awaited shape. Its result is
    known when the response is written, so it is reported on the spot."""
    engine = _Engine()
    engine.learning_gate.result = "consolidated at sleep onset"
    app = build_sim_agent_app(engine)

    async with client_for(app) as client:
        resp = await post(client, "/tick", json=_tick(9, sleeping=True))

    body = resp.json()
    assert body["learning_ran"] is True
    assert body["reply"] == ""
    assert engine.order == ["batch"], "a sleeping tick must never take a turn"
    assert engine.learning_gate.calls == [True]


@pytest.mark.asyncio
async def test_the_end_of_run_drain_waits_for_a_batch_still_in_flight():
    """`POST /learn` is the shutdown drain, and the process is killed
    seconds after it answers. Draining on top of a batch that is still
    running would have two pipelines writing the same stores while the
    SQLite pool is being closed underneath them."""
    engine = _Engine()
    engine.learning_gate.hold = asyncio.Event()
    app = build_sim_agent_app(engine)

    async with client_for(app) as client:
        await post(client, "/tick", json=_tick(1))
        drain = asyncio.create_task(post(client, "/learn"))
        await let_the_loop_run()
        assert engine.learning_gate.extra_calls == 0, (
            "the drain started while a background batch was still running"
        )

        engine.learning_gate.hold.set()
        resp = await asyncio.wait_for(drain, timeout=5.0)

    assert resp.status_code == 200
    assert engine.order == ["turn", "batch", "drain"]
