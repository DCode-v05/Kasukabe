"""M0 — replay is keyed by thread, so ?after=<seq> never leaks across threads."""

from src.engine_core.blocks import Block, ThreadBlockBuffers


def blk(thread_id, n):
    return Block(type="text", payload={"n": n}, thread_id=thread_id, id=f"text:{thread_id}:{n}")


def test_replay_returns_only_the_asked_thread():
    buf = ThreadBlockBuffers()
    buf.append(blk("t1", 1))
    buf.append(blk("t2", 1))
    buf.append(blk("t1", 2))
    buf.append(blk("t2", 2))

    t1 = buf.replay("t1", after=0)
    assert [blk.payload["n"] for _, blk in t1] == [1, 2]
    assert all(blk.thread_id == "t1" for _, blk in t1)

    t2 = buf.replay("t2", after=0)
    assert all(blk.thread_id == "t2" for _, blk in t2)


def test_after_seq_filters_within_thread_only():
    buf = ThreadBlockBuffers()
    s1 = buf.append(blk("t1", 1))
    buf.append(blk("t2", 1))  # t2's seq 1 — must not appear in t1 replay
    s2 = buf.append(blk("t1", 2))
    s3 = buf.append(blk("t1", 3))

    # replay t1 after its own seq s1 → only t1's later blocks
    got = buf.replay("t1", after=s1)
    seqs = [seq for seq, _ in got]
    assert seqs == [s2, s3]
    assert all(blk.thread_id == "t1" for _, blk in got)


def test_unknown_thread_replays_empty():
    buf = ThreadBlockBuffers()
    buf.append(blk("t1", 1))
    assert buf.replay("nope", after=0) == []
