"""A resumed run tells its recorder who lives in the town.

Run 28b - run 28 resumed at 18:00 into a fresh trace store - logged, within
a minute of its first tick:

    world recorder dropped a agent.utterance.spoken: speaker 'n16' was
    never seeded (12 dropped this run)

and kept going. The store had 0 agents and 0 businesses. `known_agent_ids`,
the recorder's list of who exists, is filled in exactly one place -
`TraceStore.seed_agent` - and that is only called on the fresh-genesis path.
A resume restored the whole town into the world and told the recorder
nothing, so:

  * every utterance was dropped (the `utterances` foreign key has nobody to
    point at);
  * every transaction and event was written with no resident on it - 228
    sim transactions with neither side named, 1,103 of 1,429 events blank;
  * and the restored invocation ring was drained from position 0, filing
    run 28's 1,204 tool calls into the continuation's trace a second time.

CORE-13 had already made `seed_agent` register a resident whose row exists,
so the same call is right for a fresh store and for the same one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path: Path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded", world_port=0, budget_cap_cents=40_000,
    ))


async def _a_run_on_disk(tmp_path: Path, monkeypatch) -> int:
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "first.db"))
    first = _runner(tmp_path)
    await first.boot(resume=False)
    await first.start_agents()
    await first.run_burst(20)
    await first.shutdown()
    return first.world_state.current_tick


async def _rows(runner: GenesisRunner, table: str, column: str = "id") -> list[dict]:
    return await runner.trace_store.client.select(table, columns=column, filters={}, order=None, limit=10_000)


@pytest.mark.asyncio
async def test_a_resume_into_a_new_store_knows_every_resident(tmp_path, monkeypatch):
    await _a_run_on_disk(tmp_path, monkeypatch)
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "continuation.db"))

    second = _runner(tmp_path)
    await second.boot(resume=True)

    residents = set(second.world_state.agents)
    assert set(second.trace_store.known_agent_ids) == residents, (
        "the recorder does not know the restored cast, so every remark any of "
        "them makes is dropped as 'speaker was never seeded'"
    )
    assert {row["id"] for row in await _rows(second, "agents")} == residents
    owned_by_residents = {
        b.business_id for b in second.world_state.businesses.values()
        if b.owner_agent_id in residents
    }
    assert {row["id"] for row in await _rows(second, "businesses")} == owned_by_residents
    await second.shutdown()


@pytest.mark.asyncio
async def test_a_resume_into_the_same_store_knows_them_too(tmp_path, monkeypatch):
    """The rows are already there. CORE-13's path: register, don't fail."""
    await _a_run_on_disk(tmp_path, monkeypatch)

    second = _runner(tmp_path)
    await second.boot(resume=True)

    assert set(second.trace_store.known_agent_ids) == set(second.world_state.agents)
    await second.shutdown()


@pytest.mark.asyncio
async def test_the_restored_invocation_log_is_not_filed_twice(tmp_path, monkeypatch):
    stopped_at = await _a_run_on_disk(tmp_path, monkeypatch)
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "continuation.db"))

    second = _runner(tmp_path)
    await second.boot(resume=True)
    assert second.world_state.tool_invocations, "the fixture restored no invocations to test against"

    written = await second.drain_tool_invocations()

    assert written == 0, (
        f"{written} calls from the checkpointed run were filed into the "
        "continuation's trace again"
    )
    old = [row for row in await _rows(second, "tool_invocations", "tick") if row["tick"] <= stopped_at]
    assert not old
    await second.shutdown()
