"""M2 — a craft's OWN wait axis controls whether the stream halts (awaiting).

Drives ``server.fan_block`` directly (same harness as test_awaiting_no_orphan):
a craft block enqueues AWAIT_SENTINEL only when craft_wait (the per-stream master
switch) AND the craft's own payload["wait"] are both set. ask_user/permission
(CraftRequest, wait defaults True) keep pausing exactly as in M1.
"""

import asyncio
import json
import pytest

from src.engine_core.blocks import Gates, BlockSerializer
from src.engine_core.events import CraftAuthored, CraftRequest
import src.server as server


def make_state(monkeypatch):
    class FakeBuffers:
        def __init__(self):
            self.seq = 0

        def append(self, block):
            self.seq += 1
            return self.seq

    class FakeTurnBuffers:
        def append(self, turn_id, block):
            return 0

    class FakeState:
        def __init__(self):
            self.block_buffers = FakeBuffers()
            self.turn_buffers = FakeTurnBuffers()

    monkeypatch.setattr(server, "state", FakeState())


def reg(thread_id, gates):
    q = asyncio.Queue()
    server.active_streams[object()] = server.StreamReg(
        queue=q,
        thread_id_box=[thread_id],
        gates=gates,
    )
    return q


def drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def has_sentinel(items, block_id):
    return any(
        isinstance(x, tuple) and x and x[0] == server.AWAIT_SENTINEL and x[1] == block_id
        for x in items
    )


def authored(**kw):
    return BlockSerializer("r1").fold(CraftAuthored(thread_id="thr1", **kw))


@pytest.fixture(autouse=True)
def clean_streams():
    server.active_streams.clear()
    yield
    server.active_streams.clear()


def test_wait_true_craft_emits_await_sentinel(monkeypatch):
    make_state(monkeypatch)
    q = reg("thr1", Gates(craft=True, craft_wait=True))
    server.fan_block(
        authored(
            block_id="craft:thr1:1",
            key="c",
            surface="inline",
            format="html",
            wait=True,
            content="<p>ok?</p>",
            final=True,
        )
    )
    assert has_sentinel(drain(q), "craft:thr1:1")


def test_wait_false_craft_streams_without_awaiting(monkeypatch):
    make_state(monkeypatch)
    q = reg("thr1", Gates(craft=True, craft_wait=True))
    server.fan_block(
        authored(
            block_id="craft:thr1:2",
            key="d",
            surface="panel",
            format="react",
            wait=False,
            content="<App/>",
            final=True,
        )
    )
    items = drain(q)
    assert len(items) == 1  # the frame, no sentinel
    assert not has_sentinel(items, "craft:thr1:2")


def test_craft_wait_gate_off_never_awaits(monkeypatch):
    make_state(monkeypatch)
    q = reg("thr1", Gates(craft=True, craft_wait=False))
    server.fan_block(
        authored(
            block_id="craft:thr1:3",
            key="c",
            surface="inline",
            format="html",
            wait=True,
            content="x",
            final=True,
        )
    )
    assert not has_sentinel(drain(q), "craft:thr1:3")


def test_craft_gate_off_suppresses_the_block(monkeypatch):
    make_state(monkeypatch)
    q = reg("thr1", Gates(craft=False, craft_wait=True))
    server.fan_block(
        authored(
            block_id="craft:thr1:4",
            key="c",
            surface="inline",
            format="html",
            wait=True,
            content="x",
            final=True,
        )
    )
    assert drain(q) == []  # craft gate off → nothing reaches the stream


def test_ask_user_permission_still_awaits_by_default(monkeypatch):
    make_state(monkeypatch)
    q = reg("thr1", Gates(craft=True, craft_wait=True))
    # CraftRequest.wait defaults True → ask_user/permission pause as in M1.
    b = BlockSerializer("r1").fold(
        CraftRequest(thread_id="thr1", question="ok?", block_id="craft:thr1:5")
    )
    server.fan_block(b)
    assert has_sentinel(drain(q), "craft:thr1:5")


def block_dicts(items):
    out = []
    for x in items:
        if isinstance(x, str) and "data: " in x:
            out.append(json.loads(x.split("data: ", 1)[1].strip()))
    return out


def test_craft_off_degrades_prompt_to_text_and_still_awaits(monkeypatch):
    make_state(monkeypatch)
    q = reg("thr1", Gates(craft=False, craft_wait=True))
    # craft gate OFF: an engine-raised prompt must NOT vanish — it degrades to a
    # plain text block (same id) and still pauses so the turn can be answered.
    b = BlockSerializer("r1").fold(
        CraftRequest(
            thread_id="thr1",
            question="Delete 200 files?",
            options=[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}],
            block_id="craft:thr1:9",
        )
    )
    server.fan_block(b)
    items = drain(q)
    blocks = block_dicts(items)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"  # degraded, not craft
    assert blocks[0]["id"] == "craft:thr1:9"  # same id → block_input resumes it
    text = blocks[0]["payload"]["text"]
    assert "Delete 200 files?" in text and "Yes" in text and "No" in text
    assert has_sentinel(items, "craft:thr1:9")  # still awaits an answer


def test_craft_off_drops_authored_craft(monkeypatch):
    make_state(monkeypatch)
    q = reg("thr1", Gates(craft=False, craft_wait=True))
    # an authored craft (no "question") has nothing to degrade → suppressed
    server.fan_block(
        authored(
            block_id="craft:thr1:10",
            key="k",
            surface="inline",
            format="html",
            wait=True,
            content="x",
            final=True,
        )
    )
    assert drain(q) == []
