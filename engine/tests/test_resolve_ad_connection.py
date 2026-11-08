"""Tests for ExecutionThread.resolve_ad_connection (provenance-aware selection
+ cheap HITL disambiguation).

resolve_ad_connection only touches ``self.ad_connection_choice`` and
``self.await_user_input``, so a bare instance (object.__new__) with those two
attributes set exercises it without the full ExecutionThread constructor.
"""

from __future__ import annotations

import pytest

from src.engine_core.thread import ExecutionThread


def make_thread():
    t = object.__new__(ExecutionThread)
    t.ad_connection_choice = {}
    return t


def cand(cid, rank, prov="user", created="2026-01-01", account=None):
    return {
        "connection_id": cid,
        "status": "ACTIVE",
        "provenance": prov,
        "provenance_rank": rank,
        "account_ref": account,
        "owner_ref": None,
        "created_at": created,
    }


async def fail_if_called(*args, **kwargs):
    raise AssertionError("await_user_input must not be called")


@pytest.mark.asyncio
async def test_single_top_rank_no_prompt():
    t = make_thread()
    t.await_user_input = fail_if_called
    chosen = await t.resolve_ad_connection("gmail", [cand("a", 2), cand("b", 1)])
    assert chosen == "a"
    assert t.ad_connection_choice["gmail"] == "a"


@pytest.mark.asyncio
async def test_remembered_choice_reused():
    t = make_thread()
    t.ad_connection_choice["gmail"] = "b"
    t.await_user_input = fail_if_called
    chosen = await t.resolve_ad_connection("gmail", [cand("a", 2), cand("b", 2)])
    assert chosen == "b"


@pytest.mark.asyncio
async def test_tie_prompts_once_and_caches():
    t = make_thread()
    calls = []

    async def fake_prompt(q, **kw):
        calls.append(kw.get("options"))
        return "b"

    t.await_user_input = fake_prompt
    cands = [cand("a", 2, created="2026-01-01"), cand("b", 2, created="2026-01-02")]
    assert await t.resolve_ad_connection("gmail", cands) == "b"
    assert await t.resolve_ad_connection("gmail", cands) == "b"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_more_than_four_ties_fallback_recent():
    t = make_thread()
    t.await_user_input = fail_if_called
    cands = [cand(f"c{i}", 2, created=f"2026-01-0{i}") for i in range(1, 6)]
    chosen = await t.resolve_ad_connection("gmail", cands)
    assert chosen == "c5"


@pytest.mark.asyncio
async def test_prompt_failure_falls_back():
    t = make_thread()

    async def boom(q, **kw):
        raise RuntimeError("no channel")

    t.await_user_input = boom
    cands = [cand("a", 2, created="2026-01-01"), cand("b", 2, created="2026-01-02")]
    assert await t.resolve_ad_connection("gmail", cands) == "b"


@pytest.mark.asyncio
async def test_empty_candidates_returns_none():
    t = make_thread()
    t.await_user_input = fail_if_called
    assert await t.resolve_ad_connection("gmail", []) is None


@pytest.mark.asyncio
async def test_reply_not_matching_falls_back():
    t = make_thread()

    async def bad_reply(q, **kw):
        return "does-not-exist"

    t.await_user_input = bad_reply
    cands = [cand("a", 2, created="2026-01-01"), cand("b", 2, created="2026-01-02")]
    assert await t.resolve_ad_connection("gmail", cands) == "b"
