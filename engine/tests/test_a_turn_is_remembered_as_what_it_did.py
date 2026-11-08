"""ENG-09: a resident's own history read as an essay, so it wrote essays.

From `docs/review/10-engine-seam-and-mind.md` (### ENG-09, P1): 152 of 349
sessions in run 23 were a single prose turn and no action at all. The first
cause the review names is structural rather than a matter of prompting: the
only thing stored for an assistant turn is `full_text` - the narration -
because frozen `thread.py:3098-3108` writes `json.dumps(full_text)` as the
assistant row of the conversation store and nothing else. So every earlier
"self" a resident meets in its own context is a paragraph of monologue with
no `tool_use` block in it, and the genre the model sees itself writing is
the genre it goes on to write. `bounded_history.py` narrowed which of those
paragraphs were loaded and left what was SAVED to the parent untouched,
which changed how many essays the resident read but not that they were
essays. The review's fix is an ACTION RECORD: what gets stored for later
replay is what the turn DID - the tool calls and what they returned - and,
for a turn that did nothing, exactly that fact, while `full_text` stays in
the session log for research. This file guards that. The unit tests below
drive `BoundedConversationStore.save_message` against a real
`SessionLogWriter` fed real `ToolExecution` events; the last test is the
wiring one and the one that matters, because every test above it would
stay green if `bound_history_in_the_seam` were never called from
`pinned_boot.py` at all - it boots a real engine, drives a real
`send_tick`, lets the frozen engine call `save_message` itself, and reads
the row back out of SQLite.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.engine_core.conversation_store import ConversationStore
from src.engine_core.events import EventEmitter, ExecutionSummary, TextDelta, ToolExecution
from src.sim.bounded_history import BoundedConversationStore, bound_history_in_the_seam
from src.sim.session_log import SessionLogWriter

TID = "kasukabe-agent-n01"

# The monologue a run-23 resident produced instead of acting. Nothing in
# this shape may end up as the assistant row of the conversation store.
A_MONOLOGUE = (
    "I consider the morning light on the square and reflect that a baker's "
    "life is made of small repetitions, each one a quiet argument with time."
)


def call(tool: str, **params) -> ToolExecution:
    return ToolExecution(
        thread_id=TID, tool_name=tool, source="engine", phase="call", params=params
    )


def result(tool: str, text: str, status: str = "success") -> ToolExecution:
    return ToolExecution(
        thread_id=TID, tool_name=tool, source="engine", phase="result",
        result=text, status=status,
    )


class _Saves(ConversationStore):
    """The parent store with its database taken out.

    Subclassing rather than monkeypatching `ConversationStore.save_message`
    globally: these tests care about what the SUBCLASS hands its parent, and
    a real subclass call chain is the thing under test.
    """

    def __init__(self) -> None:
        super().__init__(db=None)


@pytest.fixture
def writer(tmp_path: Path):
    """A real session log, attached to a real emitter.

    `attach` sets a PROCESS-WIDE global - `session_log._active_session_log`
    - because that is how the seam's one live caller finds the record
    without being handed it (`bound_history_in_the_seam(coordinator)` takes
    no `actions` argument, on purpose: "a fix that only works when a caller
    opts into it is a fix that is not running").

    That global used to outlive the test that set it. Every later test in
    the process inherited a session log it never asked for, and one test
    below silently depended on the inheritance: it passed when the whole
    file ran and failed under any `-k` selection that excluded the tests
    ahead of it. Which is the same disease as everything else here - a
    check that reports success for a reason that is not the one it names.

    So it is put back. A test that needs a live session log now says so by
    taking this fixture.
    """
    import src.sim.session_log as session_log_module

    before = session_log_module._active_session_log
    emitter = EventEmitter()
    w = SessionLogWriter("n01", tmp_path / "session_log.jsonl")
    w.attach(emitter)
    try:
        yield w, emitter
    finally:
        session_log_module._active_session_log = before


@pytest.fixture
def store(writer, monkeypatch):
    """A bounded store whose parent `save_message` records instead of writing."""
    w, _ = writer
    saved: list[tuple[str, str]] = []

    async def fake_save(self, thread_id, seq, role, content_json):
        saved.append((role, json.loads(content_json)))

    monkeypatch.setattr(ConversationStore, "save_message", fake_save)
    return BoundedConversationStore(db=None, keep=24, actions=w), saved


async def a_turn_that(writer, events, perception: str = "It is 07:10 on day 0.") -> None:
    w, emitter = writer
    w.start_turn(perception)
    for event in events:
        await emitter.emit(event)


# ── what a turn is remembered as ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turn_that_acted_is_stored_as_its_actions(writer, store):
    """The finding itself. The resident said something to a neighbour and
    bought bread; what it will read back next turn must be those two acts."""
    bounded, saved = store
    await a_turn_that(writer, [
        call("world:say", to="marta", content="Morning, Marta."),
        result("world:say", "Marta heard you."),
        call("world:buy", item="bread", quantity=2),
        result("world:buy", "Bought 2 bread for 60 sim."),
    ])

    await bounded.save_message(TID, 1, "assistant", json.dumps(A_MONOLOGUE))

    role, stored = saved[-1]
    assert role == "assistant"
    assert "world:say" in stored and "world:buy" in stored, (
        "ENG-09: the assistant turn stored for replay names none of the "
        f"actions the turn actually took: {stored!r}"
    )
    assert "Marta" in stored and "Bought 2 bread for 60 sim." in stored, (
        "the record names the tools but not who was addressed or what came "
        "back, so it cannot stand in for the turn"
    )
    assert A_MONOLOGUE not in stored, (
        "ENG-09: the narration is still what the next turn reads back as its "
        "own last self - the monologue genre is reinforced exactly as before"
    )


@pytest.mark.asyncio
async def test_a_silent_turn_is_stored_as_the_fact_that_it_was_silent(writer, store):
    """152 of 349 sessions looked like this. A turn that only wrote must
    read back as a turn that only wrote - not as a paragraph worth
    imitating."""
    bounded, saved = store
    await a_turn_that(writer, [TextDelta(thread_id=TID, text=A_MONOLOGUE)])

    await bounded.save_message(TID, 1, "assistant", json.dumps(A_MONOLOGUE))

    _, stored = saved[-1]
    assert A_MONOLOGUE not in stored, (
        "a turn that did nothing is stored as the essay it wrote instead"
    )
    # PROMPT-03 reworded the silent record to "no tool was called"; this
    # assertion still looked for the old words and had failed since.
    assert "no tool was called" in stored.lower(), (
        f"a silent turn must say so in as many words; it says {stored!r}"
    )


@pytest.mark.asyncio
async def test_a_refused_action_is_remembered_as_a_refusal(writer, store):
    """A resident that cannot tell a done thing from a refused one learns
    the wrong lesson from its own history."""
    bounded, saved = store
    await a_turn_that(writer, [
        call("world:buy", item="bread"),
        result("world:buy", "Error: you have no coin.", status="error"),
    ])

    await bounded.save_message(TID, 1, "assistant", json.dumps("..."))

    _, stored = saved[-1]
    assert "world:buy" in stored
    assert "no coin" in stored
    lowered = stored.lower()
    assert "fail" in lowered or "refus" in lowered, (
        f"a refusal reads as an accomplishment: {stored!r}"
    )


@pytest.mark.asyncio
async def test_the_record_stays_small(writer, store):
    """This row goes into every later prompt. An unbounded record would
    trade ENG-09 for ENG-02."""
    bounded, saved = store
    await a_turn_that(writer, [
        e
        for i in range(40)
        for e in (
            call("world:say", to=f"n{i:02d}", content="x" * 4000),
            result("world:say", "y" * 4000),
        )
    ])

    await bounded.save_message(TID, 1, "assistant", json.dumps("..."))

    _, stored = saved[-1]
    assert len(stored) < 4000, (
        f"one turn's stored record is {len(stored)} chars and every later "
        "prompt carries it"
    )
    assert "world:say" in stored


@pytest.mark.asyncio
async def test_the_perception_is_never_rewritten(writer, store):
    """Only the assistant row is the resident's own voice. The user row is
    the world talking, and it must arrive exactly as the world sent it."""
    bounded, saved = store
    await a_turn_that(writer, [call("world:say", to="marta", content="hello")])

    await bounded.save_message(TID, 0, "user", json.dumps("It is 07:10 on day 0."))

    assert saved[-1] == ("user", "It is 07:10 on day 0.")


@pytest.mark.asyncio
async def test_with_no_turn_in_flight_the_parent_content_stands(writer, store):
    """Safety. A save with nothing recorded - a path that never called
    `start_turn` - must degrade to the engine's own behaviour rather than
    inventing a silence it did not observe."""
    bounded, saved = store
    w, _ = writer
    w._current = None

    await bounded.save_message(TID, 1, "assistant", json.dumps(A_MONOLOGUE))

    assert saved[-1] == ("assistant", A_MONOLOGUE)


@pytest.mark.asyncio
async def test_the_narration_still_reaches_the_session_log(writer, store):
    """"Keep `full_text` in the session log for research" - ENG-09's own
    proviso. The record the model replays narrows; the record a researcher
    reads must not."""
    bounded, saved = store
    w, emitter = writer
    await a_turn_that(writer, [
        TextDelta(thread_id=TID, text=A_MONOLOGUE),
        call("world:say", to="marta", content="Morning."),
        result("world:say", "Marta heard you."),
    ])
    await bounded.save_message(TID, 1, "assistant", json.dumps(A_MONOLOGUE))
    await emitter.emit(ExecutionSummary(thread_id=TID, status="completed"))

    written = [
        json.loads(line)
        for line in w.log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert written[-1]["output"] == A_MONOLOGUE, (
        "the narration was narrowed out of the research record too"
    )


# ── the wiring: a real engine, a real turn, a real SQLite row ────────────


def world_that_answers_the_registry(agent_id: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": 0})
        # The memory stream POSTs the turn's records here; unrelated to this
        # finding, but a tick makes the call and a refusal is noise.
        if request.url.path == f"/agents/{agent_id}/memories":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected world call: {request.url.path}")

    from src.sim.world_tools import WorldClient

    return WorldClient(
        "http://world.test",
        agent_id=agent_id,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_a_real_tick_stores_what_it_did_not_what_it_said(tmp_path, monkeypatch):
    """THE WIRING TEST.

    Every test above passes `actions=` by hand and would stay green if
    `bound_history_in_the_seam` were deleted from `pinned_boot.py`, or if
    the store it builds never found the session log. This one boots a real
    engine, scripts one tool-calling turn followed by a paragraph of
    narration, and drives `send_tick` - the route `agent_adapter.py` takes
    on every awake tick. `save_message` is called by frozen
    `thread.py:3104`, not by this test, and the row is read back through
    the same `load_messages` the next turn's prompt is built from.

    `recall_session_log` is the tool because it is engine-side and needs no
    world POST - the same tool `test_sim_pinned_boot.py` uses to prove a
    real tool call reaches the session log.
    """
    from src.sim.config import SimProfile
    from src.sim.model_profile import MODEL_ID
    from src.sim.pinned_boot import boot_sim_engine
    from tests.sim_fake_citadel import (
        FakeBroker,
        install_fake_broker,
        text_turn_sse,
        tool_use_turn_sse,
    )

    monkeypatch.setenv("FORK_SUBAGENT", "false")
    broker = FakeBroker()
    install_fake_broker(monkeypatch, broker)

    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(
        profile,
        "http://world.test",
        world_client=world_that_answers_the_registry("agent-boot"),
    )
    try:
        broker.queue(MODEL_ID, "sse", tool_use_turn_sse("recall_session_log", {"turns": 3}))
        broker.queue(MODEL_ID, "sse", text_turn_sse(A_MONOLOGUE))
        await engine.send_tick("It is 07:10 on day 0. You are at home.")

        stored = await engine.coordinator.conversation_store.load_messages(engine.thread_id)
        assistant = [m for m in stored if m["role"] == "assistant"]
        assert assistant, "the turn stored no assistant row at all"
        content = assistant[-1]["content"]

        assert "recall_session_log" in content, (
            "ENG-09: the turn called a tool and the history it will replay "
            f"to itself says nothing about it: {content!r}"
        )
        assert A_MONOLOGUE not in content, (
            "ENG-09: the stored assistant turn is still the narration, so the "
            "next prompt still shows this resident a self that only writes"
        )
        assert engine.coordinator.conversation_store.assistant_turns_recorded_as_actions == 1

        # And now the thing the finding is actually about: the NEXT turn.
        # A stored record only matters if the resident reads it back, so
        # drive a second tick and look at what the assistant messages in
        # that request body say the resident was.
        broker.queue(MODEL_ID, "sse", text_turn_sse("Still thinking about it."))
        await engine.send_tick("It is 07:20 on day 0. You are at home.")

        second_request = broker.request_bodies[-1]
        on_the_wire = [
            (m.get("role"), json.dumps(m.get("content")))
            for m in second_request["messages"]
        ]
        # One message is right here: the first tick's only assistant row was
        # its record, which now travels inside the world's message.
        assert any("07:10" in text for _, text in on_the_wire), (
            "the second tick was sent with no history of the first"
        )
        assert any("recall_session_log" in text for _, text in on_the_wire), (
            "ENG-09: on its second tick the resident is shown nothing of what "
            f"it did on its first: {on_the_wire!r}"
        )
        assert not any(A_MONOLOGUE in text for _, text in on_the_wire), (
            "ENG-09: the paragraph is on the wire as the resident's last turn "
            "- which is the genre this finding says it goes on to copy"
        )
        # Run 28b: a record in the resident's own slot is copied too - 43% of
        # first calls after one wrote record-shaped text. So it reaches the
        # model as the world's words (resident_voice.py), not the resident's.
        assert not any(
            role == "assistant" and "RECORD OF THE TURN" in text for role, text in on_the_wire
        ), f"the record is on the wire in the resident's own voice: {on_the_wire!r}"
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_the_seam_entry_point_builds_a_store_that_records_actions(writer):
    """`pinned_boot.py` calls `bound_history_in_the_seam(coordinator)` with
    no keywords at all. Whatever that one call returns is the store the whole
    run saves through, so the action record has to be its default - not
    something a caller opts into."""

    class _Coordinator:
        conversation_store = ConversationStore(db=None)

    coordinator = _Coordinator()
    bound_history_in_the_seam(coordinator)

    assert isinstance(coordinator.conversation_store, BoundedConversationStore)
    assert coordinator.conversation_store.records_actions, (
        "the store the run saves through has no way to reach a turn's "
        "actions, so every assistant row is the narration again"
    )
