"""The /tick HTTP receiver: awake ticks reach the pinned thread, asleep ticks
reach only the learning gate - never both, per DESIGN.md section 1 ("no
interactive LLM calls" during sleep).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from src.sim.agent_adapter import build_sim_agent_app
from src.sim.model_profile import MODEL_ID


@dataclass
class FakeProfile:
    agent_id: str = "agent-9"


class FakeResult:
    """Shaped like LCPipelineResult, which is what the route now reads.

    `learning_ran` used to mean "a batch was attempted" and now means
    "something was learned" - the distinction ENG-01 made necessary, since
    for 23 runs a batch could burn 55-108K input tokens, write not one row,
    and still report a clean run.
    """

    def __init__(self, *, knowledge_written=1, episodes_created=1, errors=None):
        self.knowledge_written = knowledge_written
        self.episodes_created = episodes_created
        self.errors = errors or []


class FakeLearningGate:
    def __init__(self):
        self.calls: list[bool] = []
        self.next_result = None
        self.extra_calls = 0
        self.extra_result = FakeResult()
        self.runs_triggered = 0

    async def on_tick(self, is_sleeping: bool):
        self.calls.append(is_sleeping)
        if self.next_result is not None:
            self.runs_triggered += 1
        return self.next_result

    async def trigger_extra(self):
        self.extra_calls += 1
        if self.extra_result is not None:
            self.runs_triggered += 1
        return self.extra_result


class FakeWorldLink:
    """The counters `WorldClient.wire_health` serves."""

    def __init__(self, **counts):
        self.counts = {
            "world_calls_attempted": 0,
            "world_calls_refused": 0,
            "world_transport_failures": 0,
            "world_transport_recovered": 0,
            **counts,
        }

    def wire_health(self) -> dict[str, int]:
        return dict(self.counts)


@dataclass
class FakeEngine:
    profile: FakeProfile = field(default_factory=FakeProfile)
    thread_id: str = "kasukabe-agent-agent-9"
    learning_gate: FakeLearningGate = field(default_factory=FakeLearningGate)
    sent_ticks: list[str] = field(default_factory=list)
    budgets: list[int | None] = field(default_factory=list)
    last_turn_steps: int = 0
    last_turn_hit_budget: bool = False
    busy: bool = False
    world: "FakeWorldLink" = field(default_factory=lambda: FakeWorldLink())

    @property
    def turn_in_flight(self) -> bool:
        return self.busy

    async def send_tick(
        self, perception_text: str, call_budget: int | None = None
    ) -> str:
        self.sent_ticks.append(perception_text)
        self.budgets.append(call_budget)
        return f"reply to: {perception_text}"


def make_client(engine: FakeEngine) -> TestClient:
    app = build_sim_agent_app(engine)  # type: ignore[arg-type]
    return TestClient(app)


def test_health_reports_the_one_model():
    engine = FakeEngine()
    client = make_client(engine)

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "agent-9"
    assert body["thread_id"] == "kasukabe-agent-agent-9"
    assert body["model"] == MODEL_ID == "moonshotai.kimi-k2.5"


def test_awake_tick_drives_the_pinned_thread_and_reports_no_batch_when_none_ran():
    engine = FakeEngine()
    client = make_client(engine)

    resp = client.post("/tick", json={"tick": 5, "is_sleeping": False, "perception": "you see grain"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "thread_id": "kasukabe-agent-agent-9",
        "tick": 5,
        "is_sleeping": False,
        "reply": "reply to: you see grain",
        "learning_ran": False,
        "steps_used": 0,
        "budget_hit": False,
        "reflected": False,
        "goal_revisions": 0,
    }
    assert engine.sent_ticks == ["you see grain"]


def test_a_waking_interval_batch_is_not_reported_on_the_tick_that_starts_it():
    """The run report reads exactly this field.

    It used to be hardcoded False on the awake path, which was true only
    while the gate fired at sleep onset alone. The moment the waking
    interval was wired, a batch could run mid-morning and every report
    would still say the town learned nothing - the same zero that three
    reports had already misread as "the residents did not learn".

    ENG-17 changes when the true answer is available, not whether it is
    given. The batch no longer runs inside the tick, so the tick that
    starts one cannot say how it went; it is reported on the tick after it
    lands. The other half of that contract - that a completed batch IS
    then reported - is pinned in
    tests/test_learning_does_not_spend_the_ticks_budget.py, which drives
    the loop directly and can settle it.
    """
    engine = FakeEngine()
    engine.learning_gate.next_result = "batch ran on the waking interval"
    client = make_client(engine)

    resp = client.post("/tick", json={"tick": 54, "is_sleeping": False, "perception": "noon"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["learning_ran"] is False
    assert body["reply"] == "reply to: noon"
    assert engine.sent_ticks == ["noon"]


def test_the_learn_route_consolidates_on_demand():
    """The end-of-run drain. A run that stops at noon used to throw the
    whole morning's signals away with the process."""
    engine = FakeEngine()
    client = make_client(engine)

    resp = client.post("/learn")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "thread_id": "kasukabe-agent-agent-9",
        "learning_ran": True,
        "runs_triggered": 1,
        "knowledge_written": 1,
        "errors": 0,
    }
    assert engine.learning_gate.extra_calls == 1
    assert engine.sent_ticks == []


def test_a_failed_drain_is_reported_not_raised():
    """`trigger_extra` returns None when the pipeline failed. A shutdown
    path that can raise is a shutdown path that leaves engines running.

    EVAL-28. This is the INVERSE case the review asked for, and the pair
    matters more than either half: `learning_ran` means "something was
    learned", not "a batch ran". The run-22 fix changed it to
    `bool(result is not None and (written or episodes))` because a batch
    that encoded nothing was reporting success - the runner prints this
    number as "N of 20 consolidated" and the eval spec's T7 gate reads it,
    so a green line over work that did not happen is worse than a red one.
    The test above pins the true case with one written row; this pins the
    false one.
    """
    engine = FakeEngine()
    engine.learning_gate.extra_result = None
    client = make_client(engine)

    resp = client.post("/learn")

    assert resp.status_code == 200
    assert resp.json()["learning_ran"] is False


def test_sleeping_tick_never_reaches_the_primary_adapter():
    engine = FakeEngine()
    engine.learning_gate.next_result = "ran"
    client = make_client(engine)

    resp = client.post("/tick", json={"tick": 6, "is_sleeping": True, "perception": "zzz"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == ""
    assert body["learning_ran"] is True
    assert engine.sent_ticks == []
    assert engine.learning_gate.calls == [True]


def test_the_call_budget_reaches_the_engine():
    """The world owns the wallet, so the world computes the budget and the
    engine is handed it. One resident's tick was measured at fifty model
    calls."""
    engine = FakeEngine()
    client = make_client(engine)

    client.post("/tick", json={
        "tick": 40, "is_sleeping": False, "perception": "morning", "call_budget": 5,
    })

    assert engine.budgets == [5]


def test_a_tick_with_no_budget_leaves_the_engines_default_alone():
    """An older world, or a run with the bound turned off, must behave
    exactly as it did before this existed."""
    engine = FakeEngine()
    client = make_client(engine)

    client.post("/tick", json={"tick": 40, "is_sleeping": False, "perception": "morning"})

    assert engine.budgets == [None]


def test_the_turn_reports_what_it_used_and_whether_it_ran_out():
    """The world renders the sentence, so the engine has to report the
    fact. A budget that is hit and not reported is a resident that stops
    mid-thought and is never told why."""
    engine = FakeEngine()
    engine.last_turn_steps = 6
    engine.last_turn_hit_budget = True
    client = make_client(engine)

    body = client.post("/tick", json={
        "tick": 40, "is_sleeping": False, "perception": "morning", "call_budget": 6,
    }).json()

    assert body["steps_used"] == 6
    assert body["budget_hit"] is True


def test_a_tick_is_refused_while_this_engine_is_busy():
    """CORE-11 through the same adapter these tests already drive. The
    behavioural coverage is in test_a_busy_engine_refuses_a_tick.py; this
    is here so the FakeEngine's new field cannot rot into an unused one."""
    engine = FakeEngine(busy=True)
    client = make_client(engine)

    resp = client.post("/tick", json={
        "tick": 4, "is_sleeping": False, "perception": "the square",
        "call_budget": 3,
    })

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "turn_in_flight"
    assert engine.sent_ticks == [], "the refused tick still ran a turn"
