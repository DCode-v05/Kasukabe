"""Two threads of one turn must not share a block id.

15. Append-stream ids (text/reasoning/voice) were `{btype}:{turn_id}`. TextDelta
    and ThinkingDelta carry no turn_id, so every segment of a fanned-out turn
    folded to the same `text:{rid}`. The envelope thread_id stayed correct and
    the sink routed correctly, so nothing on the wire looked wrong - but the
    contract documents append-by-id, so a client rendered ONE text block with
    two threads' prose interleaved token by token, and `voice:{rid}` made the
    product's TTS speak two threads as a single utterance. VoiceDelta already
    had a turn_id override for sub-agent narration; the separation was simply
    never extended to sibling threads.

16. make_done_block and make_aggregate_done_block both hard-coded
    `done:{scope_id}`, so a fanned-out turn emitted N per-segment dones plus one
    aggregate under a single id. Under the contract's replace-by-id
    reconciliation the turn ended with one done entry whose thread_id had
    mutated from thread-a to thread-b to null - losing exactly the per-thread
    identity server.py's own comment promises.

Every fan-out test in the suite hand-constructs Blocks with literal ids, so
BlockSerializer.block_id was exercised by nothing.
"""

import pytest

from src.engine_core.blocks import (
    BlockSerializer,
    make_aggregate_done_block,
    make_done_block,
)
from src.engine_core.events import TextDelta, ThinkingDelta, VoiceDelta


RID = "req-abc"


def reconcile(blocks) -> dict:
    """The client-side rule the contract documents: replace by id."""
    out: dict = {}
    for b in blocks:
        out[b.id] = b
    return out


class TestAppendStreamsAreThreadScoped:
    @pytest.mark.parametrize(
        "make",
        [
            lambda tid, txt: TextDelta(thread_id=tid, text=txt),
            lambda tid, txt: ThinkingDelta(thread_id=tid, text=txt),
            lambda tid, txt: VoiceDelta(thread_id=tid, text=txt),
        ],
        ids=["text", "reasoning", "voice"],
    )
    def test_sibling_threads_get_distinct_ids(self, make):
        s = BlockSerializer(RID)
        a = s.fold(make("thread-a", "Deploying the patch now"))
        b = s.fold(make("thread-b", "Drafting the memo outline"))
        assert a.id != b.id, f"both segments folded to {a.id}"
        assert a.thread_id == "thread-a" and b.thread_id == "thread-b"

    def test_deltas_of_one_thread_still_append_to_one_block(self):
        """The whole point of an append stream must survive the change."""
        s = BlockSerializer(RID)
        ids = {
            s.fold(TextDelta(thread_id="thread-a", text=chunk)).id
            for chunk in ("Deploy", "ing the ", "patch")
        }
        assert len(ids) == 1

    def test_the_id_still_carries_the_turn(self):
        s = BlockSerializer(RID)
        block = s.fold(TextDelta(thread_id="thread-a", text="hi"))
        assert block.id == "text:req-abc:thread-a"

    def test_a_threadless_delta_keeps_the_plain_turn_scope(self):
        """Engine-origin speech (coordinator.say) carries no thread."""
        s = BlockSerializer(RID)
        assert s.fold(TextDelta(thread_id="", text="hi")).id == "text:req-abc"

    def test_sub_agent_voice_stays_separate_and_gains_the_thread(self):
        """VoiceDelta's turn_id override already split sub-agent narration from
        the parent's; it must keep doing so, now on both axes."""
        s = BlockSerializer(RID)
        parent = s.fold(VoiceDelta(thread_id="thread-a", text="hi"))
        agent = s.fold(
            VoiceDelta(thread_id="thread-a", text="sub", turn_id=f"{RID}-agent7")
        )
        sibling_agent = s.fold(
            VoiceDelta(thread_id="thread-b", text="sub", turn_id=f"{RID}-agent7")
        )
        assert len({parent.id, agent.id, sibling_agent.id}) == 3

    def test_two_threads_prose_does_not_interleave_under_reconciliation(self):
        """The user-visible symptom, reproduced end to end."""
        s = BlockSerializer(RID)
        blocks = []
        for chunk_a, chunk_b in zip(["Deploying ", "the patch"], ["Drafting ", "the memo"]):
            blocks.append(s.fold(TextDelta(thread_id="thread-a", text=chunk_a)))
            blocks.append(s.fold(TextDelta(thread_id="thread-b", text=chunk_b)))

        appended: dict = {}
        for b in blocks:
            appended.setdefault(b.id, []).append(b.payload["text"])

        assert len(appended) == 2
        assert "".join(appended[f"text:{RID}:thread-a"]) == "Deploying the patch"
        assert "".join(appended[f"text:{RID}:thread-b"]) == "Drafting the memo"


class TestPerSegmentDonesSurviveReconciliation:
    def test_two_segment_dones_and_the_aggregate_have_three_ids(self):
        a = make_done_block(RID, {"status": "completed"}, "thread-a", terminal=False)
        b = make_done_block(RID, {"status": "failed"}, "thread-b", terminal=False)
        agg = make_aggregate_done_block(
            RID,
            [
                {"thread_id": "thread-a", "status": "completed"},
                {"thread_id": "thread-b", "status": "failed"},
            ],
        )
        assert len({a.id, b.id, agg.id}) == 3

    def test_reconciliation_keeps_every_thread(self):
        """Before the fix this collapsed to ONE entry whose thread_id had
        mutated from thread-a to thread-b to null."""
        a = make_done_block(RID, {"status": "completed"}, "thread-a", terminal=False)
        b = make_done_block(RID, {"status": "failed"}, "thread-b", terminal=False)
        agg = make_aggregate_done_block(RID, [])

        state = reconcile([a, b, agg])
        assert len(state) == 3
        by_thread = {blk.thread_id: blk.payload.get("status") for blk in state.values()}
        assert by_thread["thread-a"] == "completed"
        assert by_thread["thread-b"] == "failed"

    def test_the_aggregate_keeps_the_plain_id(self):
        assert make_aggregate_done_block(RID, []).id == f"done:{RID}"

    def test_a_single_thread_turns_terminal_done_is_unchanged(self):
        """Only the non-terminal per-segment done gets the thread suffix, so a
        normal turn's terminal done keeps the id clients already key on."""
        done = make_done_block(RID, {"status": "completed"}, "thread-a")
        assert done.id == f"done:{RID}"
        assert done.thread_id == "thread-a"

    def test_the_terminal_flag_is_still_what_closes_the_stream(self):
        seg = make_done_block(RID, {"status": "completed"}, "thread-a", terminal=False)
        assert seg.payload["terminal"] is False
        final = make_done_block(RID, {"status": "completed"}, "thread-a")
        assert "terminal" not in final.payload
