"""A scenario file actually decides how the world boots.

`world/scenario.py` reads and validates; these tests are about the wiring that
obeys it. Four things have to be true, and none of them were before:

- the world starts on the tick the scenario names;
- the election opens when the run does, not at tick 0. `seed_genesis` called
  `schedule_election(0)` no matter what the clock said, so a run started at dawn
  (tick 36) was already a quarter of the way through its own candidacy window
  before anyone woke up, and a run that starts later still would have missed
  candidacy entirely;
- the charter carries the scenario's rules, so an election can be held in one
  game-day instead of three;
- the seats named in the scenario are already held when the first tick runs, so
  the first election is contested by a government that has something to lose.

All of it boots in mock mode: no engines, no Bedrock, no money.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from world.run_genesis import (
    DAWN_TICK,
    GenesisRunner,
    GenesisRunnerConfig,
    apply_scenario,
    build_arg_parser,
    config_from_args,
)

SCENARIO = str(Path(__file__).resolve().parents[1] / "scenarios" / "election_at_dawn_8.json")


def make_runner(tmp_path: Path, **overrides: object) -> GenesisRunner:
    kwargs: dict[str, object] = dict(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    )
    kwargs.update(overrides)
    scenario_path = kwargs.pop("scenario_path", None)
    config = GenesisRunnerConfig(**kwargs)
    if scenario_path:
        # The same fold `--scenario` and KASUKABE_SCENARIO do: a config that
        # merely NAMES a scenario has not been shaped by one.
        config = apply_scenario(config, str(scenario_path))
    return GenesisRunner(config)


async def booted(tmp_path: Path, **overrides: object) -> GenesisRunner:
    runner = make_runner(tmp_path, **overrides)
    await runner.boot(resume=False)
    return runner


@pytest.mark.asyncio
async def test_the_world_starts_on_the_tick_the_scenario_names(tmp_path: Path) -> None:
    runner = await booted(tmp_path, scenario_path=SCENARIO)

    assert runner.tick_engine.clock.tick_index == 36
    assert runner.world_state.current_tick == 36
    await runner.shutdown()


@pytest.mark.asyncio
async def test_the_election_opens_when_the_run_does(tmp_path: Path) -> None:
    runner = await booted(tmp_path, scenario_path=SCENARIO)

    elections = await runner.world_state.charter.store.list_elections()
    assert len(elections) == 1
    election = elections[0]
    # Four game-hours a phase, six ticks to the hour, from the tick the run starts.
    assert (election.candidacy_opens_tick, election.candidacy_closes_tick) == (36, 60)
    assert (election.voting_opens_tick, election.voting_closes_tick) == (84, 108)
    await runner.shutdown()


@pytest.mark.asyncio
async def test_dawn_alone_no_longer_starts_a_town_mid_candidacy(tmp_path: Path) -> None:
    """The bug, with no scenario in sight: candidacy used to open at tick 0."""
    runner = await booted(tmp_path, start_at_dawn=True)

    election = (await runner.world_state.charter.store.list_elections())[0]

    assert election.candidacy_opens_tick == DAWN_TICK, (
        "an election whose candidacy opened before the world did cost the town "
        "36 ticks of its own first window"
    )
    await runner.shutdown()


@pytest.mark.asyncio
async def test_the_charter_carries_the_scenarios_rules(tmp_path: Path) -> None:
    runner = await booted(tmp_path, scenario_path=SCENARIO)

    charter = await runner.world_state.charter.store.get_charter()

    assert charter.candidacy_window_days == pytest.approx(1 / 6)
    assert charter.campaign_period_days == pytest.approx(1 / 6)
    assert charter.voting_window_days == pytest.approx(1 / 6)
    assert charter.election_interval_days == pytest.approx(1.0)
    assert (charter.pm_seats, charter.minister_seats) == (1, 2)
    assert charter.suffrage_min_age == 18
    await runner.shutdown()


@pytest.mark.asyncio
async def test_a_government_is_already_sitting_when_the_run_starts(tmp_path: Path) -> None:
    runner = await booted(tmp_path, scenario_path=SCENARIO)

    state = await runner.world_state.charter.store.get_government_state()

    assert state.pm_agent_id == "a0047"
    assert state.minister_agent_ids == ["a0090", "a0042"]
    assert state.is_sitting("a0090"), "a sitting minister may use the seat's tools"
    assert state.caretaker is True, "nobody voted for them; DESIGN section 10 must not count a term"
    assert state.terms_served == {}
    await runner.shutdown()


@pytest.mark.asyncio
async def test_the_incumbents_may_stand_in_the_election_they_are_defending(tmp_path: Path) -> None:
    """The point of the scenario: seats are defended, not merely filled."""
    from world.charter.models import LOCATION_TOWN_HALL
    from world.tools.primitives.civic import eligibility_facts_for

    runner = await booted(tmp_path, scenario_path=SCENARIO)
    world = runner.world_state
    election = (await world.charter.store.list_elections())[0]

    await world.charter.elections.declare_candidacy(
        election.election_id, "a0047", eligibility_facts_for(world, world.agents["a0047"]),
        40, LOCATION_TOWN_HALL,
    )
    await world.charter.elections.declare_candidacy(
        election.election_id, "a0040", eligibility_facts_for(world, world.agents["a0040"]),
        41, LOCATION_TOWN_HALL,
    )

    standing = set((await world.charter.store.list_elections())[0].candidates)
    assert standing == {"a0047", "a0040"}, "the sitting PM and a newcomer, on the same ballot"
    await runner.shutdown()


def test_the_command_line_takes_a_scenario() -> None:
    args = build_arg_parser().parse_args(["--scenario", SCENARIO])

    config = config_from_args(args)

    assert config.scenario_path == SCENARIO
    assert config.start_tick == 36
    assert config.max_ticks == 150
    assert config.budget_cap_cents == 1900
    assert str(config.genesis_path).endswith("genesis_8_funded.json")


def test_a_scenario_that_cannot_run_is_refused_before_anything_boots(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"name": "broken", "world": {"start_tick": 100, "stop_tick": 10}}')

    with pytest.raises((ValueError, SystemExit)) as caught:
        config_from_args(build_arg_parser().parse_args(["--scenario", str(broken)]))

    assert "stop_tick" in str(caught.value)


@pytest.mark.asyncio
async def test_the_run_records_its_own_setup_where_the_report_reads_it(tmp_path: Path) -> None:
    """A score with no situation beside it invites a comparison nobody checked."""
    import sqlite3

    from world.run_report import scenario_section

    seen: list[object] = []
    runner = make_runner(tmp_path, scenario_path=SCENARIO)
    await runner.boot(resume=False)

    async def spy(event: object) -> None:      # the bus awaits its subscribers
        seen.append(event)

    runner.world_state.event_bus.subscribe("system", spy)
    await runner.record_the_scenario()
    await runner.shutdown()

    assert [e for e in seen if e.type == "system.scenario.set"], "the trace must carry the setup"
    payload = [e for e in seen if e.type == "system.scenario.set"][0].payload
    assert payload["name"] == "election-at-dawn-8"
    assert payload["start_tick"] == 36 and payload["stop_tick"] == 150

    db = tmp_path / "report-trace.db"
    conn = sqlite3.connect(db)
    conn.execute("create table events (type text, payload text, tick integer)")
    conn.execute("insert into events values ('system.scenario.set', ?, 36)", (json.dumps(payload),))
    conn.commit()
    conn.close()

    text = "\n".join(scenario_section(str(db)))
    assert text.startswith("## The situation this run was set up in")
    assert "prime minister a0047" in text and "counted at tick 108" in text


def test_a_run_without_a_scenario_says_nothing_about_one(tmp_path: Path) -> None:
    import sqlite3

    from world.run_report import scenario_section

    db = tmp_path / "plain.db"
    conn = sqlite3.connect(db)
    conn.execute("create table events (type text, payload text, tick integer)")
    conn.commit()
    conn.close()

    assert scenario_section(str(db)) == []
    assert scenario_section(str(tmp_path / "missing.db")) == []


def test_the_setup_event_is_one_the_trace_keeps() -> None:
    """Published and never recorded is the split the recorder counters exist to catch."""
    from world.run_genesis import is_worth_recording

    assert is_worth_recording("system.scenario.set"), (
        "the report reads the trace, so a run's own setup has to earn a row"
    )
