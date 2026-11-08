"""A pinned thread must be protected by code, not only by the triage prompt.

"Never route new input into a CLAIMED thread" is asserted as absolute in three
docstrings (server.py, schema.py, thread_router.py) and was enforced in exactly
one place: the words of the triage prompt. parse_dispatch is handed known_ids -
every thread id, with no pin information - so the designated enforcement point
structurally could not apply the rule. A non-compliant {verdict: "continue",
target: "thread-x"} produced a perfectly well-formed segment, handle_input took
dispatch.executable verbatim, and a full turn ran on a thread the product
believes only it can write to: tools executed, thread state mutated, success
reported. No log, no warning, no downgrade.

build_thread_index's own docstring records that models ignored the CLAIMED
marker under urgent input in live qualification, which is why the prompt alone
was never enough.
"""

import pytest

from src.engine_core.coordinator import Coordinator
from src.engine_core.schema import Dispatch, DispatchSegment, ThreadIndexEntry


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
    def __init__(self, thread_id, log):
        self.thread_id = thread_id
        self.log = log
        self.cancel_requested = False

    async def execute(self, message, thread_id=None, **kwargs):
        self.log.append((thread_id, message))
        return f"done:{thread_id}"


class ScriptedRouter:
    """A triage model that ignores the CLAIMED marker, as they demonstrably do."""

    def __init__(self, segments):
        self.segments = segments

    async def dispatch(self, index_text, message, known_ids):
        return Dispatch(
            segments=[DispatchSegment(**s) for s in self.segments],
            reasoning="urgent",
        )


def make_coordinator(monkeypatch, segments, pinned=()):
    coord = Coordinator(adapter=None, emitter=FakeEmitter(), memory=FakeMemory(), db=None)
    log: list[tuple[str, str]] = []
    coord.execution_log = log
    coord.router = ScriptedRouter(segments)

    for tid in ("thread-x", "thread-y"):
        coord.threads[tid] = RecordingThread(tid, log)
        coord.thread_index[tid] = ThreadIndexEntry(
            thread_id=tid, summary=f"{tid} work", pinned=tid in pinned
        )

    async def get_or_create(thread_id):
        if thread_id not in coord.threads:
            coord.threads[thread_id] = RecordingThread(thread_id, log)
        return coord.threads[thread_id]

    monkeypatch.setattr(coord, "get_or_create_thread", get_or_create)

    async def noop_index(*a, **k):
        pass

    monkeypatch.setattr(coord, "update_thread_index", noop_index)
    return coord


CONTINUE_INTO_X = [{"extract": "urgent follow-up", "verdict": "continue", "target": "thread-x"}]
RECALL_FROM_X = [{"extract": "what did we decide", "verdict": "recall", "target": "thread-x"}]


class TestExplicitThreadIdStaysExempt:
    """Direct execution by thread_id is documented as unaffected by pinning."""

    @pytest.mark.asyncio
    async def test_an_explicit_pinned_thread_id_still_executes(self, monkeypatch):
        coord = make_coordinator(monkeypatch, CONTINUE_INTO_X, pinned=("thread-x",))
        dispatch = await coord.build_dispatch("do it", thread_id="thread-x")
        assert dispatch.segments[0].verdict == "continue"
        assert dispatch.segments[0].target == "thread-x"

        await coord.handle_input("do it", thread_id="thread-x")
        assert [t for t, _ in coord.execution_log] == ["thread-x"]
