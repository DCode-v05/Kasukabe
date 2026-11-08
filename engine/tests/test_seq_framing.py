"""M0 — `seq:` SSE framing (idx→seq) and per-thread monotonic seq."""

from src.engine_core.blocks import Block, sse_frame, ThreadBlockBuffers


def test_frame_uses_seq_not_id_line():
    block = Block(type="done", payload={"status": "completed"}, thread_id="t1", id="done:r1")
    frame = sse_frame(block, 3)
    assert frame.startswith("seq: 3\n")
    assert "event: block\n" in frame
    assert "\nid: " not in frame  # the old idx framing is gone
    assert "cursor: " not in frame  # no cursor passed → no cursor line (back-compat)


def test_frame_with_cursor_emits_cursor_line_after_seq():
    """Approach A: a cursor (per-thread buffer index) is written on its own
    ``cursor:`` line, right after the first ``seq:`` line and before the
    ``event: block`` line. seq stays the FIRST line of the frame."""
    block = Block(type="text", payload={"text": "hi"}, thread_id="t1", id="text:r1")
    frame = sse_frame(block, 2, 5)
    assert frame.startswith("seq: 2\n")  # seq is still the first line
    assert "cursor: 5\n" in frame
    # cursor line comes before the event line
    assert frame.index("cursor: 5\n") < frame.index("event: block")
    # exact ordering of the first three framing lines
    assert frame.startswith("seq: 2\ncursor: 5\nevent: block\n")


def test_seq_is_per_thread_monotonic():
    buf = ThreadBlockBuffers()
    s1 = buf.append(Block(type="text", payload={}, thread_id="t1"))
    s2 = buf.append(Block(type="text", payload={}, thread_id="t1"))
    # a different thread starts its own counter at 1
    o1 = buf.append(Block(type="text", payload={}, thread_id="t2"))
    s3 = buf.append(Block(type="text", payload={}, thread_id="t1"))
    assert [s1, s2, s3] == [1, 2, 3]
    assert o1 == 1
