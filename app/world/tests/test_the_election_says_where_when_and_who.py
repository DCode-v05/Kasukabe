"""The election notice says where, when and who - and says it on time.

The 50-resident run: all three `vote` calls were refused because voting had
not opened, and a nine-year-old was refused a candidacy. Each was a resident
acting on an election it had been told existed and not told the rules of.
The notice said the phase and who was standing. It did not say that
candidates declare and residents vote in person at the town hall, that
voting opens a full day after candidacy closes, or that it is for residents
of 18 and over. Those are the world's rules, stated as facts - not advice to
take part.

Second defect, found while checking the first: the notice was filled BEFORE
the election's phase advanced each tick, so it lagged by one. At dawn on day
0 every resident read "an election is scheduled" while candidacy had been
open since tick 0 - on the first turn anybody reads it.

Third, found while building scenarios: candidacy opened at tick 0 whatever
tick the world started on, so a town woken at dawn had already spent 36 ticks
of its own candidacy window asleep. The election is now scheduled from the
clock the run starts on, which moves every deadline below on by six game-hours
- the deadlines a resident reads are the ones it can actually act within.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.core.clock import game_time_label, timestamp_for
from world.retrieval_depth import format_civic_notice
from world.run_genesis import GenesisRunner, GenesisRunnerConfig

GENESIS = Path(__file__).resolve().parents[1] / "genesis_20_funded.json"


async def _town(tmp_path) -> GenesisRunner:
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0, start_at_dawn=True, genesis_path=GENESIS,
    ))
    await runner.boot(resume=False)
    return runner


def test_a_tick_reads_like_the_perception_clock() -> None:
    assert game_time_label(144) == "game-day 1, 00:00"
    assert game_time_label(36) == "game-day 0, 06:00"


@pytest.mark.asyncio
async def test_at_dawn_the_notice_says_candidacy_is_open(tmp_path) -> None:
    runner = await _town(tmp_path)
    await runner.charter_tick(timestamp_for(runner.world_state.tick()))

    text = format_civic_notice(runner.civic_notice_for(sorted(runner.world_state.agents)[0]))

    assert "open for candidates" in text, text


@pytest.mark.asyncio
async def test_the_notice_states_the_rules_of_taking_part(tmp_path) -> None:
    runner = await _town(tmp_path)
    await runner.charter_tick(timestamp_for(runner.world_state.tick()))

    text = format_civic_notice(runner.civic_notice_for(sorted(runner.world_state.agents)[0]))

    assert "in person at Kasukabe Government Complex" in text
    assert "aged 18 and over" in text
    assert "Candidacy closes game-day 1, 06:00" in text
    assert "voting opens game-day 2, 06:00" in text
    assert "voting closes game-day 3, 06:00" in text


@pytest.mark.asyncio
async def test_once_voting_is_open_it_says_when_it_closes_and_not_when_it_opens(tmp_path) -> None:
    runner = await _town(tmp_path)
    await runner.charter_tick(timestamp_for(336))

    text = format_civic_notice(runner.civic_notice_for(sorted(runner.world_state.agents)[0]))

    assert "open for voting" in text
    assert "voting closes game-day 3, 06:00" in text.lower()
    assert "voting opens" not in text.lower() and "candidacy closes" not in text.lower()


@pytest.mark.asyncio
async def test_it_states_rules_and_gives_no_advice(tmp_path) -> None:
    runner = await _town(tmp_path)
    await runner.charter_tick(timestamp_for(runner.world_state.tick()))

    text = format_civic_notice(runner.civic_notice_for(sorted(runner.world_state.agents)[0])).lower()

    for word in ("should", "must", "try to", "consider", "don't forget", "remember to", "make sure"):
        assert word not in text, f"the notice advises: {word!r}"


@pytest.mark.asyncio
async def test_every_adult_in_the_cast_may_stand_and_vote(tmp_path) -> None:
    """The operator's rule for this run: any resident of 18 or over takes part."""
    from world.charter.elections import check_suffrage
    from world.tools.primitives.civic import eligibility_facts_for

    runner = await _town(tmp_path)
    world = runner.world_state
    charter = await world.charter.store.get_charter()
    adults = [a for a, r in world.agents.items() if r.age_years >= 18]
    minors = [a for a, r in world.agents.items() if r.age_years < 18]
    assert len(adults) == 16 and len(minors) == 4

    for agent_id in adults:
        check_suffrage(charter, eligibility_facts_for(world, world.agents[agent_id]))
    for agent_id in minors:
        with pytest.raises(Exception):
            check_suffrage(charter, eligibility_facts_for(world, world.agents[agent_id]))
