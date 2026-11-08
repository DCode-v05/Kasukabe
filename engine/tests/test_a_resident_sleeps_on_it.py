"""A resident thinks about what it wants once a night, and not otherwise.

`ENG-24`, the seam half. The world half gives a resident somewhere to
keep a goal and deterministic rules that move it - a pressing need
spawns one, a succeeding tool credits one, a refusal writes a belief,
the day boundary expires the short-horizon ones. Every one of those is
replayable and none of them asks a model anything, which is exactly why
they can only ever produce the goal the situation implies. "Get
something to eat" is a reflex.

§5.3's acceptance names the other half: *"sleep reflection posts
revise_goals once per resident per night on the LC adapter"*. This file
holds it to the four properties that would make it worthless if they
broke:

* **once a night, not once a sleeping tick** - a resident sleeps for
  many consecutive ticks, and a reflection per tick is a second turn
  loop at a second turn's price;
* **never while awake** - DESIGN.md section 1 puts no interactive model
  calls in the sleep window and no bookkeeping in the turn's window;
* **it writes through the world's own tools** - the engine does not get
  a private handle on `world.goals`, so a revision is gated, priced,
  validated and traced exactly as a resident's own call would be;
* **it never takes the sleep tick down with it** - a night's thinking is
  not worth losing the learning batch over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from src.sim.agent_adapter import build_sim_agent_app
from src.sim.goal_reflection import MAX_REVISIONS, GoalReflection, _parse_revisions


@dataclass
class FakeProfile:
    agent_id: str = "agent-9"


class FakeWorld:
    """The world as the seam sees it: one POST per tool call."""

    def __init__(self, standing=None):
        self.standing = standing if standing is not None else {
            "goals": [{"goal_id": "g1", "text": "Sell the wheat", "progress": 0.0}],
            "needs": [{"kind": "money", "level": 0.8}],
            "beliefs": [],
        }
        self.calls: list[tuple[str, dict]] = []
        self.refuse: set[str] = set()

    async def call_tool(self, tool_name: str, tool_input: dict) -> str:
        self.calls.append((tool_name, tool_input))
        if tool_name in self.refuse:
            return "Error: you are already pursuing 5 things"
        if tool_name == "recall_goals":
            return json.dumps(self.standing)
        return json.dumps({"ok": True})

    @property
    def called(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeAdapter:
    """The LC adapter. Records that it was asked, and what with."""

    def __init__(self, text="[]"):
        self.text = text
        self.calls = 0
        self.last_system = None
        self.last_max_tokens = None
        self.last_skip_cache_write = None

    async def generate_with_tools(
        self, messages, system=None, tools=None, super_thinking=False,
        max_output_tokens=None, skip_cache_write=False,
    ):
        self.calls += 1
        self.last_system = system
        self.last_max_tokens = max_output_tokens
        self.last_skip_cache_write = skip_cache_write

        class Response:
            pass

        response = Response()
        response.text = self.text
        response.tool_calls = []
        return response


class FakeLearningGate:
    def __init__(self):
        self.calls: list[bool] = []

    async def on_tick(self, is_sleeping: bool):
        self.calls.append(is_sleeping)
        return None

    async def trigger_extra(self):
        return None


@dataclass
class FakeEngine:
    profile: FakeProfile = field(default_factory=FakeProfile)
    thread_id: str = "kasukabe-agent-agent-9"
    learning_gate: FakeLearningGate = field(default_factory=FakeLearningGate)
    world: FakeWorld = field(default_factory=FakeWorld)
    lc_adapter: FakeAdapter = field(default_factory=FakeAdapter)
    sent_ticks: list[str] = field(default_factory=list)
    last_turn_steps: int = 0
    last_turn_hit_budget: bool = False
    busy: bool = False

    @property
    def turn_in_flight(self) -> bool:
        return self.busy

    async def send_tick(self, perception_text: str, call_budget=None) -> str:
        self.sent_ticks.append(perception_text)
        return f"reply to: {perception_text}"


# ── the parser ───────────────────────────────────────────────────────


def test_a_fenced_answer_is_not_thrown_away():
    """Models fence JSON about half the time; the thought still counts."""
    fenced = '```json\n[{"action": "new", "text": "Open a bakery"}]\n```'
    assert _parse_revisions(fenced) == [{"action": "new", "text": "Open a bakery"}]


def test_an_unparseable_answer_becomes_nothing_rather_than_a_guess():
    for junk in ("", "I think I should open a bakery.", "{}", "[1, 2, 3]", "["):
        assert _parse_revisions(junk) == [], junk


def test_prose_around_the_array_is_tolerated():
    said = 'Thinking it over.\n[{"action": "keep", "goal_id": "g1"}]\nThat is all.'
    assert _parse_revisions(said) == [{"action": "keep", "goal_id": "g1"}]


# ── once a night ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_resident_reflects_once_a_night_not_once_a_tick():
    engine = FakeEngine()
    engine.lc_adapter.text = '[{"action": "new", "text": "Find better work", "horizon": "week"}]'
    reflection = GoalReflection(engine)

    first = await reflection.reflect(game_day=3)
    assert first.ran and first.created == 1
    assert engine.lc_adapter.calls == 1

    # A resident sleeps for many consecutive ticks. Every one of them
    # calls this; only the first is allowed to cost anything.
    for _ in range(8):
        again = await reflection.reflect(game_day=3)
        assert not again.ran
        assert again.skipped_because == "already reflected on this day"
    assert engine.lc_adapter.calls == 1, "the night was paid for more than once"

    # The next night is a new night.
    await reflection.reflect(game_day=4)
    assert engine.lc_adapter.calls == 2


@pytest.mark.asyncio
async def test_the_night_is_claimed_before_the_work_not_after():
    """Or a second sleep tick starts a second call while the first is in flight."""
    engine = FakeEngine()
    reflection = GoalReflection(engine)
    assert reflection.due_for(3)
    await reflection.reflect(game_day=3)
    assert reflection.last_day == 3
    assert not reflection.due_for(3)


@pytest.mark.asyncio
async def test_a_quiet_night_costs_nothing():
    """No goals and nothing pressing: there is nothing to think about."""
    engine = FakeEngine(world=FakeWorld({"goals": [], "needs": [], "beliefs": []}))
    reflection = GoalReflection(engine)

    result = await reflection.reflect(game_day=1)

    assert not result.ran
    assert result.skipped_because == "nothing to reflect on"
    assert engine.lc_adapter.calls == 0, "an empty night still called the model"


# ── it writes through the world's own tools ──────────────────────────


@pytest.mark.asyncio
async def test_a_revision_is_an_ordinary_world_tool_call():
    """The engine gets no private handle on world.goals - app/CONVENTIONS.md."""
    engine = FakeEngine()
    engine.lc_adapter.text = json.dumps([
        {"action": "drop", "goal_id": "g1", "why": "it was never going to work"},
        {"action": "new", "text": "Learn the forge", "horizon": "week", "priority": 4},
    ])
    reflection = GoalReflection(engine)

    result = await reflection.reflect(game_day=2)

    assert result.dropped == 1 and result.created == 1 and result.revisions == 2
    assert engine.world.called == ["recall_goals", "retire_goal", "set_goal"]
    _, retire = engine.world.calls[1]
    assert retire["goal_id"] == "g1" and retire["done"] is False
    _, made = engine.world.calls[2]
    assert made["text"] == "Learn the forge"
    assert made["horizon"] == "week" and made["priority"] == 4


@pytest.mark.asyncio
async def test_keeping_a_goal_costs_no_world_call():
    engine = FakeEngine()
    engine.lc_adapter.text = '[{"action": "keep", "goal_id": "g1"}]'
    reflection = GoalReflection(engine)

    result = await reflection.reflect(game_day=2)

    assert result.ran and result.revisions == 0
    assert engine.world.called == ["recall_goals"]


@pytest.mark.asyncio
async def test_a_night_cannot_rewrite_a_whole_life():
    """MAX_REVISIONS, because each one is a call and money."""
    engine = FakeEngine()
    engine.lc_adapter.text = json.dumps(
        [{"action": "new", "text": f"goal {i}"} for i in range(10)]
    )
    reflection = GoalReflection(engine)

    result = await reflection.reflect(game_day=2)

    assert result.created == MAX_REVISIONS
    assert len(engine.world.called) == 1 + MAX_REVISIONS


@pytest.mark.asyncio
async def test_a_refused_revision_is_reported_not_swallowed():
    """The world may say no - it is the same gate a resident faces."""
    engine = FakeEngine()
    engine.world.refuse = {"set_goal"}
    engine.lc_adapter.text = '[{"action": "new", "text": "Open a bakery"}]'
    reflection = GoalReflection(engine)

    result = await reflection.reflect(game_day=2)

    assert result.ran and result.created == 0
    assert result.errors and "already pursuing" in result.errors[0]


@pytest.mark.asyncio
async def test_a_malformed_revision_is_skipped_and_named():
    engine = FakeEngine()
    engine.lc_adapter.text = json.dumps([
        {"action": "drop"},
        {"action": "new", "text": "   "},
        {"action": "invent_money"},
    ])
    reflection = GoalReflection(engine)

    result = await reflection.reflect(game_day=2)

    assert result.revisions == 0
    assert len(result.errors) == 3
    assert engine.world.called == ["recall_goals"]


# ── it never takes the tick down ─────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unreachable_world_is_a_quiet_night_not_a_crash():
    class Broken(FakeWorld):
        async def call_tool(self, tool_name, tool_input):
            raise RuntimeError("world server unreachable")

    engine = FakeEngine(world=Broken())
    result = await GoalReflection(engine).reflect(game_day=2)

    assert not result.ran and result.errors


@pytest.mark.asyncio
async def test_a_failed_model_call_is_a_quiet_night_not_a_crash():
    class Broken(FakeAdapter):
        async def generate_with_tools(self, *a, **k):
            raise RuntimeError("no lane capacity")

    engine = FakeEngine(lc_adapter=Broken())
    result = await GoalReflection(engine).reflect(game_day=2)

    assert not result.ran and result.errors


# ── and it happens on the sleeping tick, through /tick ───────────────


def test_a_sleeping_tick_reflects_and_says_what_it_changed():
    engine = FakeEngine()
    engine.lc_adapter.text = '[{"action": "new", "text": "Open a bakery"}]'
    client = TestClient(build_sim_agent_app(engine))  # type: ignore[arg-type]

    resp = client.post(
        "/tick", json={"tick": 300, "is_sleeping": True, "perception": "", "game_day": 2}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reflected"] is True
    assert body["goal_revisions"] == 1
    assert body["reply"] == "", "a sleeping resident took a turn"
    assert engine.sent_ticks == [], "a sleeping resident reached the pinned thread"
    assert "set_goal" in engine.world.called


def test_an_awake_tick_never_reflects():
    """DESIGN.md section 1. Reflection is sleep work; a turn is not."""
    engine = FakeEngine()
    client = TestClient(build_sim_agent_app(engine))  # type: ignore[arg-type]

    resp = client.post(
        "/tick", json={"tick": 301, "is_sleeping": False, "perception": "grain", "game_day": 2}
    )

    assert resp.json()["reflected"] is False
    assert engine.lc_adapter.calls == 0
    assert engine.world.called == []


def test_the_night_falls_back_to_the_worlds_own_clock():
    """A caller that sends no game_day still gets one night per night."""
    engine = FakeEngine()
    engine.lc_adapter.text = '[{"action": "keep", "goal_id": "g1"}]'
    client = TestClient(build_sim_agent_app(engine))  # type: ignore[arg-type]

    # 144 ticks to a game-day: 300 and 320 are the same night, 460 is not.
    client.post("/tick", json={"tick": 300, "is_sleeping": True, "perception": ""})
    client.post("/tick", json={"tick": 320, "is_sleeping": True, "perception": ""})
    assert engine.lc_adapter.calls == 1
    client.post("/tick", json={"tick": 460, "is_sleeping": True, "perception": ""})
    assert engine.lc_adapter.calls == 2


def test_the_reflection_does_not_poison_the_prompt_cache():
    """ENG-27: a fire-and-forget call must not seed a cache the next turn misses."""
    engine = FakeEngine()
    client = TestClient(build_sim_agent_app(engine))  # type: ignore[arg-type]
    client.post("/tick", json={"tick": 300, "is_sleeping": True, "perception": "", "game_day": 2})

    assert engine.lc_adapter.last_skip_cache_write is True
    assert engine.lc_adapter.last_max_tokens is not None


def test_the_learning_batch_runs_even_when_the_reflection_fails():
    """A night's thinking is not worth losing the consolidation over."""
    class Broken(FakeAdapter):
        async def generate_with_tools(self, *a, **k):
            raise RuntimeError("no lane capacity")

    engine = FakeEngine(lc_adapter=Broken())
    client = TestClient(build_sim_agent_app(engine))  # type: ignore[arg-type]

    resp = client.post(
        "/tick", json={"tick": 300, "is_sleeping": True, "perception": "", "game_day": 2}
    )

    assert resp.status_code == 200, "a failed reflection took the sleep tick down"
    assert engine.learning_gate.calls == [True], "the learning batch never ran"
    assert resp.json()["reflected"] is False
