"""ENG-02: history is bounded in the seam, not by emergency compaction.

The engine's compaction preflight is

    post_tokens + sys_tokens + tools_tokens + RESERVE_FLOOR >= context_window

with `RESERVE_FLOOR = 25_000` as an absolute constant. The sim window was
12,500, so that test was true with **zero messages** - every model call in
every one of the 23 runs took the emergency path. Tool results gutted, the
day-0 06:20 perception pinned into every prompt forever, assistant-genre
"[Post-compact tool reminder]" text in a villager's mouth, and a cold
prompt cache on every call.

The operator's decision of 2026-09-04 was that history is bounded in the
seam. These pin all three parts of that.
"""
from __future__ import annotations

import pytest

RESERVE_FLOOR = 25_000  # src/engine_core/context_engineering.py:31


def test_the_window_no_longer_makes_compaction_unconditional():
    """The arithmetic that made every call an emergency."""
    from src.sim.model_profile import SIM_CONTEXT_WINDOW

    assert SIM_CONTEXT_WINDOW > RESERVE_FLOOR, (
        f"the sim context window is {SIM_CONTEXT_WINDOW} and RESERVE_FLOOR "
        f"alone is {RESERVE_FLOOR}, so the compaction preflight is true "
        "before a single message exists and every call takes the emergency "
        "path (ENG-02)"
    )
    # Room for the tool schemas too (~16K by the engine's own estimator),
    # or the preflight is still true on a turn with no history.
    assert SIM_CONTEXT_WINDOW > RESERVE_FLOOR + 16_000 + 2_000


def test_the_engine_is_told_not_to_rewrite_the_prefix():
    """DISABLE_AUTO_COMPACT, or the seam and the engine fight over history."""
    from src.sim.config import raw_process_env

    env = raw_process_env(_a_profile())
    assert env.get("DISABLE_AUTO_COMPACT") == "1", (
        "the engine will keep compacting underneath the seam's bound, which "
        "is what pinned the day-0 perception into every prompt"
    )


def _a_profile():
    from src.sim.config import SimProfile

    return SimProfile(
        agent_id="n01",
        broker_base_url="http://x",
        broker_api_key="t",
        home_root=".",
    )


class _Rows:
    """A stand-in for the parent store's DB read."""

    def __init__(self, rows):
        self.rows = rows


@pytest.mark.asyncio
async def test_only_the_recent_tail_is_handed_to_a_turn(monkeypatch):
    from src.engine_core.conversation_store import ConversationStore
    from src.sim.bounded_history import BoundedConversationStore

    everything = [
        {"seq": i, "role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(100)
    ]

    async def fake_load(self, thread_id):
        return everything

    monkeypatch.setattr(ConversationStore, "load_messages", fake_load)

    store = BoundedConversationStore(db=None, keep=10)
    got = await store.load_messages("kasukabe-agent-n01")

    assert len(got) <= 10, f"handed {len(got)} messages to a turn, not the bound"
    assert got[-1]["content"] == "m99", "the tail must be the RECENT end"
    assert store.messages_dropped == 90
    assert all(m["content"] != "m0" for m in got), (
        "the resident's first stored message - the day-0 06:20 perception - "
        "is still in the prompt; that is the thing emergency compaction "
        "pinned there for 23 runs"
    )


@pytest.mark.asyncio
async def test_a_short_thread_is_untouched(monkeypatch):
    from src.engine_core.conversation_store import ConversationStore
    from src.sim.bounded_history import BoundedConversationStore

    three = [{"seq": i, "role": "user", "content": f"m{i}"} for i in range(3)]

    async def fake_load(self, thread_id):
        return three

    monkeypatch.setattr(ConversationStore, "load_messages", fake_load)
    store = BoundedConversationStore(db=None, keep=24)
    assert await store.load_messages("t") == three
    assert store.messages_dropped == 0


@pytest.mark.asyncio
async def test_the_window_never_opens_on_an_orphaned_tool_result(monkeypatch):
    """A `tool` message whose call was dropped is uninterpretable, and some
    providers reject the request outright."""
    from src.engine_core.conversation_store import ConversationStore
    from src.sim.bounded_history import BoundedConversationStore

    rows = (
        [{"seq": 0, "role": "user", "content": "old"}]
        + [{"seq": 1, "role": "assistant", "content": "calling"}]
        + [{"seq": 2, "role": "tool", "content": "result"}]
        + [{"seq": 3, "role": "assistant", "content": "done"}]
    )

    async def fake_load(self, thread_id):
        return rows

    monkeypatch.setattr(ConversationStore, "load_messages", fake_load)
    store = BoundedConversationStore(db=None, keep=2)
    got = await store.load_messages("t")
    assert got[0]["role"] in ("user", "assistant"), (
        f"the window opens on a {got[0]['role']} message whose call was dropped"
    )
