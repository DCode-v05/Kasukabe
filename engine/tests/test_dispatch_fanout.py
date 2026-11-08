"""Fan-out - one input becoming several turns, and the rules that govern it.

These drive the real Coordinator. The LLM adapter and the ExecutionThread are
substituted (an LLM call and a full agent loop are not what is under test), but
every seam that decides WHAT runs - triage, coalescing, verdict handling,
status answering, failure propagation - is the production code path.
"""

import asyncio

import pytest

from src.engine_core.coordinator import (
    Coordinator,
    citations_gate_var,
    craft_gate_var,
    craft_wait_var,
    media_gates_var,
    voice_gate_var,
    voice_primary_var,
)
from src.engine_core.events import TextDelta
from src.engine_core.schema import Dispatch, DispatchSegment, ThreadIndexEntry


GATES_LOG: list = []


def observed_gates() -> dict:
    """The gate contextvars as the running segment sees them."""
    return {
        "craft": craft_gate_var.get(),
        "craft_wait": craft_wait_var.get(),
        "citations": citations_gate_var.get(),
        "voice": voice_gate_var.get(),
        "voice_primary": voice_primary_var.get(),
        "media": dict(media_gates_var.get() or {}),
    }


def spoken(coord):
    """Text the engine itself put on the wire, in order.

    Engine-origin speech carries no thread_id - that is how a reader tells the
    system speaking from one piece of work reporting.
    """
    return [e.text for e in coord.emitter.events if isinstance(e, TextDelta) and not e.thread_id]


class FakeEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, ev):
        self.events.append(ev)


class FakeMemory:
    async def write(self, *a, **k):
        pass

    async def read(self, *a, **k):
        return []


class RecordingThread:
    """Stands in for ExecutionThread: records what it was asked to run.

    It reads the gate CONTEXTVARS rather than its own kwargs. That is the
    stronger assertion: it proves the value was set in the segment's own
    context copy (taken at create_task time) rather than merely handed to the
    caller, which is the exact seam the fan-out call site has to get right.
    """

    def __init__(self, thread_id, log, fail=False, delay=0.0):
        self.thread_id = thread_id
        self.log = log
        self.fail = fail
        self.delay = delay
        self.cancel_requested = False
        self.gates_seen: list[dict] = []

    async def execute(self, message, thread_id=None, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.log.append((thread_id, message))
        gates = observed_gates()
        self.gates_seen.append(gates)
        GATES_LOG.append((thread_id, gates))
        if self.fail:
            raise RuntimeError(f"{thread_id} blew up")
        return f"done:{thread_id}"


def make_coordinator(monkeypatch, threads=(), fail_on=(), delay_on=()):
    GATES_LOG.clear()
    coord = Coordinator(adapter=None, emitter=FakeEmitter(), memory=FakeMemory(), db=None)
    log: list[tuple[str, str]] = []
    coord.execution_log = log

    for tid in threads:
        coord.threads[tid] = RecordingThread(tid, log)
        coord.thread_index[tid] = ThreadIndexEntry(thread_id=tid, summary=f"{tid} work")

    async def get_or_create(thread_id):
        if thread_id not in coord.threads:
            coord.threads[thread_id] = RecordingThread(
                thread_id,
                log,
                fail=thread_id in fail_on,
                delay=0.05 if thread_id in delay_on else 0.0,
            )
        t = coord.threads[thread_id]
        t.fail = thread_id in fail_on
        t.delay = 0.05 if thread_id in delay_on else 0.0
        return t

    monkeypatch.setattr(coord, "get_or_create_thread", get_or_create)

    async def noop_index(*a, **k):
        pass

    monkeypatch.setattr(coord, "update_thread_index", noop_index)
    return coord


def dispatch_of(*segments, reasoning="test"):
    return Dispatch(segments=list(segments), reasoning=reasoning)


class TestFanOut:
    @pytest.mark.asyncio
    async def test_three_segments_run_as_three_turns(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a", "thread-b"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="ask one", verdict="continue", target="thread-a"),
                DispatchSegment(extract="ask two", verdict="open", title="New"),
                DispatchSegment(extract="ask three", verdict="recall", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        result = await coord.handle_input("ask one, ask two, ask three")

        assert len(coord.execution_log) == 3
        assert ("thread-a", "ask one") in coord.execution_log
        assert ("thread-b", "ask three") in coord.execution_log
        new_runs = [r for r in coord.execution_log if r[1] == "ask two"]
        assert len(new_runs) == 1 and new_runs[0][0] not in ("thread-a", "thread-b")
        assert "done:" in result

    @pytest.mark.asyncio
    async def test_segments_run_concurrently_not_serially(self, monkeypatch):
        coord = make_coordinator(
            monkeypatch, threads=["thread-a", "thread-b"], delay_on=("thread-a", "thread-b")
        )

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="one", verdict="continue", target="thread-a"),
                DispatchSegment(extract="two", verdict="continue", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        start = asyncio.get_event_loop().time()
        await coord.handle_input("one and two")
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.09, f"turns serialized ({elapsed:.3f}s)"

    @pytest.mark.asyncio
    async def test_same_target_segments_coalesce_into_one_turn(self, monkeypatch):
        """Two segments aimed at one thread would serialize behind that
        thread's turn lock. Merge them instead - one turn, both extracts."""
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="first point", verdict="continue", target="thread-a"),
                DispatchSegment(extract="second point", verdict="continue", target="thread-a"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("first point second point")

        assert len(coord.execution_log) == 1
        tid, text = coord.execution_log[0]
        assert tid == "thread-a"
        assert "first point" in text and "second point" in text

    @pytest.mark.asyncio
    async def test_each_open_segment_gets_its_own_thread(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="one", verdict="open"),
                DispatchSegment(extract="two", verdict="open"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("one and two")

        ids = {tid for tid, _ in coord.execution_log}
        assert len(ids) == 2


class TestReturnedVerdicts:
    @pytest.mark.asyncio
    async def test_claimed_segment_never_executes(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(
                    extract="push the vendor", verdict="claimed", related_ids=["thread-a"]
                )
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("push the vendor")
        assert coord.execution_log == []

    @pytest.mark.asyncio
    async def test_contested_segment_never_executes(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a", "thread-b"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(
                    extract="the thing",
                    verdict="contested",
                    related_ids=["thread-a", "thread-b"],
                )
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("the thing")
        assert coord.execution_log == []

    @pytest.mark.asyncio
    async def test_withheld_input_says_why_instead_of_going_silent(self, monkeypatch):
        """Nothing ran, so no thread exists to explain the silence.

        The dispatch block carries the verdicts, but a reader owed an answer
        gets one in words rather than only a machine-readable verdict.
        """
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(
                    extract="push the vendor", verdict="claimed", related_ids=["thread-a"]
                ),
                reasoning="You are already pushing the vendor on another piece of work.",
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("push the vendor")

        assert spoken(coord) == ["You are already pushing the vendor on another piece of work."]

    @pytest.mark.asyncio
    async def test_mixed_runs_executable_and_withholds_claimed(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="new work", verdict="open"),
                DispatchSegment(
                    extract="claimed work", verdict="claimed", related_ids=["thread-a"]
                ),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("new work and claimed work")

        assert len(coord.execution_log) == 1
        assert coord.execution_log[0][1] == "new work"

    @pytest.mark.asyncio
    async def test_dispatch_block_carries_every_segment(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="run me", verdict="open"),
                DispatchSegment(
                    extract="resolve me", verdict="claimed", related_ids=["thread-a"]
                ),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("run me and resolve me")

        routing = [e for e in coord.emitter.events if type(e).__name__ == "RoutingDecision"]
        assert len(routing) == 1
        verdicts = [s["verdict"] for s in routing[0].segments]
        assert verdicts == ["open", "claimed"]
        assert routing[0].thread_id == ""


class TestStatusVerdict:
    @pytest.mark.asyncio
    async def test_status_answers_without_spawning_work(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        class MainAdapter:
            def __init__(self):
                self.prompts = []

            async def generate(self, messages, system=None):
                self.prompts.append((messages, system))
                return "You have one thing in flight. Want me to change anything?"

        coord.adapter = MainAdapter()

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(DispatchSegment(extract=message, verdict="status"))

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        answer = await coord.handle_input("what's going on?")

        assert coord.execution_log == []
        assert "in flight" in answer
        assert coord.adapter.prompts
        system_text = coord.adapter.prompts[0][1][0].text
        assert "one voice" in system_text.lower()
        assert "never mention threads" in system_text.lower()

    @pytest.mark.asyncio
    async def test_status_degrades_without_an_adapter(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])
        coord.adapter = None

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(DispatchSegment(extract=message, verdict="status"))

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        answer = await coord.handle_input("what's going on?")
        assert isinstance(answer, str) and answer
        assert coord.execution_log == []
        assert spoken(coord) == [answer]

    @pytest.mark.asyncio
    async def test_status_answer_reaches_the_wire(self, monkeypatch):
        """A status input spawns no thread, so nothing else can speak for it.

        Returning the answer up the call stack delivers it to nobody - the
        caller reads handle_input for a thread id and discards the rest.
        """
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        class MainAdapter:
            async def generate(self, messages, system=None):
                return "One thing is in flight."

        coord.adapter = MainAdapter()

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(DispatchSegment(extract=message, verdict="status"))

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("what's going on?")

        assert spoken(coord) == ["One thing is in flight."]


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_one_failed_segment_does_not_lose_its_siblings(self, monkeypatch):
        coord = make_coordinator(
            monkeypatch, threads=["thread-a", "thread-b"], fail_on=("thread-b",)
        )

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="good", verdict="continue", target="thread-a"),
                DispatchSegment(extract="bad", verdict="continue", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        result = await coord.handle_input("good and bad")

        assert "done:thread-a" in result
        assert "failed" in result and "thread-b" in result

    @pytest.mark.asyncio
    async def test_total_failure_raises(self, monkeypatch):
        coord = make_coordinator(
            monkeypatch, threads=["thread-a", "thread-b"], fail_on=("thread-a", "thread-b")
        )

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="one", verdict="continue", target="thread-a"),
                DispatchSegment(extract="two", verdict="continue", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        with pytest.raises(RuntimeError):
            await coord.handle_input("one and two")

    @pytest.mark.asyncio
    async def test_single_segment_failure_still_raises(self, monkeypatch):
        """The pre-fan-out contract: one segment behaves exactly as before."""
        coord = make_coordinator(monkeypatch, threads=["thread-a"], fail_on=("thread-a",))

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="boom", verdict="continue", target="thread-a")
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        with pytest.raises(RuntimeError):
            await coord.handle_input("boom")


class TestBypasses:
    @pytest.mark.asyncio
    async def test_explicit_thread_id_skips_triage(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])
        called = {"router": False}

        class Router:
            async def dispatch(self, *a, **k):
                called["router"] = True
                raise AssertionError("triage must not run for an explicit thread_id")

        coord.router = Router()
        await coord.handle_input("direct message", thread_id="thread-a")

        assert called["router"] is False
        assert coord.execution_log == [("thread-a", "direct message")]

    @pytest.mark.asyncio
    async def test_no_threads_yet_skips_triage(self, monkeypatch):
        coord = make_coordinator(monkeypatch)

        class Router:
            async def dispatch(self, *a, **k):
                raise AssertionError("triage must not run with an empty index")

        coord.router = Router()
        await coord.handle_input("first ever message")
        assert len(coord.execution_log) == 1

    @pytest.mark.asyncio
    async def test_thread_id_box_binds_before_execution(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="m", verdict="continue", target="thread-a")
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        box = [""]
        await coord.handle_input("m", thread_id_box=box)
        assert box[0] == "thread-a"


class TestStatusAlongsideWork:
    """An input can ask a question AND give an instruction. Doing one and
    silently dropping the other is not an answer."""

    def coordinator_with_status(self, monkeypatch, segments):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        class MainAdapter:
            async def generate(self, messages, system=None):
                return "Two things are in flight. Want me to change anything?"

        coord.adapter = MainAdapter()

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(*segments)

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        return coord

    @pytest.mark.asyncio
    async def test_status_is_answered_even_when_work_runs(self, monkeypatch):
        coord = self.coordinator_with_status(
            monkeypatch,
            [
                DispatchSegment(extract="what's going on?", verdict="status"),
                DispatchSegment(extract="book the flights", verdict="open"),
            ],
        )
        result = await coord.handle_input("what's going on? also book the flights")

        assert len(coord.execution_log) == 1, "the work must still run"
        assert coord.execution_log[0][1] == "book the flights"
        assert "in flight" in result, "the question must still be answered"
        assert spoken(coord) == [
            "Two things are in flight. Want me to change anything?"
        ], "the answer must reach the wire, not just the return value"

    @pytest.mark.asyncio
    async def test_status_alone_still_spawns_nothing(self, monkeypatch):
        coord = self.coordinator_with_status(
            monkeypatch, [DispatchSegment(extract="what's up?", verdict="status")]
        )
        result = await coord.handle_input("what's up?")
        assert coord.execution_log == []
        assert "in flight" in result

    @pytest.mark.asyncio
    async def test_answer_survives_every_instruction_failing(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"], fail_on=("thread-a",))

        class MainAdapter:
            async def generate(self, messages, system=None):
                return "One thing is in flight."

        coord.adapter = MainAdapter()

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="status?", verdict="status"),
                DispatchSegment(extract="do it", verdict="continue", target="thread-a"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        result = await coord.handle_input("status? do it")
        assert "in flight" in result
        assert "failed" in result


class TestDuplicateOpenSegments:
    """Two segments describing the same new work are one piece of work said
    twice - a thread each would hand the user twins for one intent."""

    @pytest.mark.asyncio
    async def test_identical_open_segments_share_one_thread(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="plan the offsite dinner", verdict="open"),
                DispatchSegment(extract="plan the offsite dinner", verdict="open"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("plan the offsite dinner")
        assert len({tid for tid, _ in coord.execution_log}) == 1

    @pytest.mark.asyncio
    async def test_overlapping_open_segments_share_one_thread(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="plan the offsite dinner", verdict="open"),
                DispatchSegment(extract="plan the offsite", verdict="open"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("plan the offsite dinner")
        assert len({tid for tid, _ in coord.execution_log}) == 1

    @pytest.mark.asyncio
    async def test_distinct_new_work_still_gets_its_own_thread(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="book the flights", verdict="open"),
                DispatchSegment(extract="draft the job description", verdict="open"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("book the flights and draft the job description")
        assert len({tid for tid, _ in coord.execution_log}) == 2


class TestCallerBindingIsNotOverwritten:
    @pytest.mark.asyncio
    async def test_prebound_thread_survives(self, monkeypatch):
        """The stream is already registered against the caller's binding;
        repointing slot 0 would orphan anything fanned under it."""
        coord = make_coordinator(monkeypatch, threads=["thread-a"])
        box = ["thread-a"]
        await coord.handle_input("direct", thread_id="thread-a", thread_id_box=box)
        assert box[0] == "thread-a"

    @pytest.mark.asyncio
    async def test_every_fanned_thread_is_bound(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a", "thread-b"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="one", verdict="continue", target="thread-a"),
                DispatchSegment(extract="two", verdict="continue", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        box = [""]
        await coord.handle_input("one and two", thread_id_box=box)
        assert set(box) == {"thread-a", "thread-b"}


class TestGatesReachEveryFannedSegment:
    """The seam the merge just fixed, and which nothing asserted.

    The fan-out call site forwards voice/voice_primary/image/audio/video into
    asyncio.create_task. Commit a1ba699 fixed a NameError there and 17 tests
    caught it - but a NameError raises. A WRONG VALUE had no test at all: the
    parser would still produce narration and media blocks, the gates would drop
    them before emit, and the client would see a silent, text-only turn.

    The branch guard is ``if len(turns) == 1 and status_task is None``, so a
    single work segment accompanied by a status segment takes the fan-out path
    too. Both shapes are covered here.
    """

    GATES = dict(
        craft=True,
        craft_wait=False,
        citations=True,
        voice=True,
        voice_primary=True,
        image=True,
        audio=True,
        video=True,
    )

    def assert_all_gates_arrived(self, expected_threads):
        assert {tid for tid, _ in GATES_LOG} == set(expected_threads)
        for tid, gates in GATES_LOG:
            assert gates["craft"] is True, tid
            assert gates["craft_wait"] is False, tid
            assert gates["citations"] is True, tid
            assert gates["voice"] is True, tid
            assert gates["voice_primary"] is True, tid
            assert gates["media"] == {"image": True, "audio": True, "video": True}, tid

    @pytest.mark.asyncio
    async def test_every_segment_of_a_fanned_turn_gets_the_gates(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a", "thread-b"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="one", verdict="continue", target="thread-a"),
                DispatchSegment(extract="two", verdict="continue", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("one and two", **self.GATES)
        self.assert_all_gates_arrived(["thread-a", "thread-b"])

    @pytest.mark.asyncio
    async def test_a_work_segment_beside_a_status_segment_gets_them_too(self, monkeypatch):
        """One work segment + one status segment still takes the fan-out path,
        so this shape reads as an intermittent failure if the gates are lost."""
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="do the thing", verdict="continue", target="thread-a"),
                DispatchSegment(extract="what is running", verdict="status"),
            )

        async def fake_status(message):
            return "nothing is running"

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        monkeypatch.setattr(coord, "answer_status", fake_status)
        await coord.handle_input("do the thing, and what is running", **self.GATES)
        self.assert_all_gates_arrived(["thread-a"])

    @pytest.mark.asyncio
    async def test_the_single_segment_path_gets_them_as_well(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="just one", verdict="continue", target="thread-a"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("just one", **self.GATES)
        self.assert_all_gates_arrived(["thread-a"])

    @pytest.mark.asyncio
    async def test_defaults_leave_every_segment_gated_off(self, monkeypatch):
        coord = make_coordinator(monkeypatch, threads=["thread-a", "thread-b"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="one", verdict="continue", target="thread-a"),
                DispatchSegment(extract="two", verdict="continue", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("one and two")
        assert len(GATES_LOG) == 2
        for tid, gates in GATES_LOG:
            assert gates["voice"] is False, tid
            assert gates["citations"] is False, tid
            assert gates["media"] == {"image": False, "audio": False, "video": False}, tid

    @pytest.mark.asyncio
    async def test_one_gate_reaching_only_the_first_segment_is_caught(self, monkeypatch):
        """Guards the shape of the original defect: gates read from the caller's
        context instead of each segment's own copy would agree on segment one
        and diverge on the rest. Asserting per-thread, not in aggregate, is what
        makes that visible."""
        coord = make_coordinator(monkeypatch, threads=["thread-a", "thread-b"])

        async def fake_dispatch(message, thread_id, direct=False):
            return dispatch_of(
                DispatchSegment(extract="one", verdict="continue", target="thread-a"),
                DispatchSegment(extract="two", verdict="continue", target="thread-b"),
            )

        monkeypatch.setattr(coord, "build_dispatch", fake_dispatch)
        await coord.handle_input("one and two", voice=True, image=True)

        per_thread = {tid: g for tid, g in GATES_LOG}
        assert per_thread["thread-a"]["voice"] is True
        assert per_thread["thread-b"]["voice"] is True
        assert per_thread["thread-a"]["media"]["image"] is True
        assert per_thread["thread-b"]["media"]["image"] is True
