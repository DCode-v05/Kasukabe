"""A scenario file says when the sim starts and what is already happening in it.

Every run so far has been "boot the default town at tick 0 and see what
happens". The questions the checklist still has need a run that starts at a
chosen tick, with a chosen situation already in place - a government already
seated, an election already coming up - so that what is being measured happens
inside the hour the budget buys, instead of three game-days in.

`world/scenario.py` is that file, and nothing else: it reads, validates and
describes. It does not boot a world, touch a charter or import the runner. The
wiring that applies it is separate, and so are these tests: everything here is
about whether a scenario is understood and whether a bad one fails loudly, at
load time, instead of forty minutes into a paid run.
"""

from __future__ import annotations

import json

import pytest

from world.scenario import Scenario

MINIMAL = {
    "name": "plain",
    "world": {"genesis": "world/genesis_8.json", "start_tick": 36, "stop_tick": 150},
}


def write(tmp_path, body: dict, name: str = "s.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(body))
    return str(path)


def test_it_reads_when_the_sim_starts_and_stops(tmp_path) -> None:
    s = Scenario.load(write(tmp_path, MINIMAL))

    assert s.name == "plain"
    assert s.world.start_tick == 36
    assert s.world.stop_tick == 150
    assert s.world.genesis == "world/genesis_8.json"


def test_an_election_can_open_when_the_sim_does(tmp_path) -> None:
    body = dict(MINIMAL, election={"opens_at": "start"},
                charter={"candidacy_window_hours": 4, "campaign_period_hours": 4,
                         "voting_window_hours": 4, "election_interval_hours": 24})

    s = Scenario.load(write(tmp_path, body))

    assert s.election_opening_tick() == 36, "the election opens on the tick the run starts"
    # 6 ticks to the game-hour: candidacy 36-60, campaign 60-84, voting 84-108.
    assert s.election_phase_ticks() == (36, 60, 84, 108)
    fields = s.charter_overrides()
    assert fields["candidacy_window_days"] == pytest.approx(1 / 6)
    assert fields["election_interval_days"] == pytest.approx(1.0)


def test_a_government_can_already_be_sitting(tmp_path) -> None:
    body = dict(MINIMAL, government={"incumbents": [
        {"agent_id": "a0047", "seat": "prime_minister"},
        {"agent_id": "a0064", "seat": "minister"},
    ]})

    s = Scenario.load(write(tmp_path, body))

    assert s.prime_minister() == "a0047"
    assert s.ministers() == ("a0064",)


def test_the_default_is_the_town_as_it_has_always_booted(tmp_path) -> None:
    s = Scenario.load(write(tmp_path, MINIMAL))

    assert s.charter_overrides() == {}, "no charter block means the genesis charter, untouched"
    assert s.incumbents == ()
    assert s.election_opening_tick() == 36, "with no election block, it opens when the run does"
    assert s.world.cadence is None and s.world.budget_cap_usd is None


@pytest.mark.parametrize("bad, says", [
    ({"world": {"genesis": "g.json", "start_tick": 150, "stop_tick": 36}}, "stop_tick"),
    ({"world": {"genesis": "g.json", "start_tick": -1, "stop_tick": 10}}, "start_tick"),
    ({"world": {"genesis": "g.json", "start_tick": 0, "stop_tick": 10, "cadence": 0}}, "cadence"),
    ({"world": {"genesis": "g.json", "start_tick": 0, "stop_tick": 10}, "typo_block": {}}, "typo_block"),
    ({"world": {"genesis": "g.json", "start_tick": 0, "stop_tick": 10, "budget_cap_usd": 0}}, "budget_cap_usd"),
])
def test_a_scenario_that_cannot_run_is_refused_at_load(tmp_path, bad, says) -> None:
    body = dict(bad)
    body.setdefault("name", "bad")

    with pytest.raises(ValueError) as caught:
        Scenario.load(write(tmp_path, body))

    assert says in str(caught.value)


def test_an_election_longer_than_its_own_interval_is_refused(tmp_path) -> None:
    body = dict(MINIMAL, charter={"candidacy_window_hours": 12, "campaign_period_hours": 12,
                                  "voting_window_hours": 12, "election_interval_hours": 24})

    with pytest.raises(ValueError, match="election_interval_hours"):
        Scenario.load(write(tmp_path, body))


def test_an_election_the_run_would_miss_is_refused(tmp_path) -> None:
    """The bug this file exists to prevent: candidacy opening before the run does."""
    body = dict(MINIMAL, election={"opens_at": 12})

    with pytest.raises(ValueError, match="opens_at"):
        Scenario.load(write(tmp_path, body))

    ok = Scenario.load(write(tmp_path, dict(MINIMAL, election={"opens_at": "genesis"}), "ok.json"))
    assert ok.election_opening_tick() == 0, "'genesis' is the old behaviour, asked for on purpose"


def test_more_incumbents_than_seats_is_refused(tmp_path) -> None:
    body = dict(MINIMAL, charter={"pm_seats": 1, "minister_seats": 1},
                government={"incumbents": [
                    {"agent_id": "a1", "seat": "prime_minister"},
                    {"agent_id": "a2", "seat": "minister"},
                    {"agent_id": "a3", "seat": "minister"},
                ]})

    with pytest.raises(ValueError, match="minister"):
        Scenario.load(write(tmp_path, body))


def test_an_incumbent_who_is_not_in_the_cast_is_refused(tmp_path, monkeypatch) -> None:
    """A mistyped agent id must not survive to a paid run: the cast is right there."""
    cast = tmp_path / "cast.json"
    cast.write_text(json.dumps({"agents": [{"agent_id": "a0047", "name": "Giselle", "age_years": 50}]}))
    body = {"name": "typo", "world": {"genesis": str(cast), "start_tick": 0, "stop_tick": 10},
            "government": {"incumbents": [{"agent_id": "a0999", "seat": "prime_minister"}]}}

    with pytest.raises(ValueError, match="a0999"):
        Scenario.load(write(tmp_path, body))


def test_an_incumbent_too_young_to_stand_is_refused(tmp_path) -> None:
    cast = tmp_path / "cast.json"
    cast.write_text(json.dumps({"agents": [{"agent_id": "kid", "name": "Perrin", "age_years": 16}]}))
    body = {"name": "child-pm", "world": {"genesis": str(cast), "start_tick": 0, "stop_tick": 10},
            "charter": {"suffrage_min_age": 18},
            "government": {"incumbents": [{"agent_id": "kid", "seat": "prime_minister"}]}}

    with pytest.raises(ValueError, match="16"):
        Scenario.load(write(tmp_path, body))


def test_it_says_in_words_what_it_will_do(tmp_path) -> None:
    """The run report and the manifest carry this, so a reader knows what was set up."""
    body = dict(MINIMAL, election={"opens_at": "start"},
                charter={"candidacy_window_hours": 4, "campaign_period_hours": 4, "voting_window_hours": 4,
                         "election_interval_hours": 24, "minister_seats": 2},
                government={"incumbents": [{"agent_id": "a0047", "seat": "prime_minister"}]})

    lines = "\n".join(Scenario.load(write(tmp_path, body)).describe())

    assert "tick 36" in lines and "tick 150" in lines
    assert "a0047" in lines
    assert "candidacy" in lines and "108" in lines, "the tick of the count is the thing to check"


def test_the_shipped_scenarios_all_load() -> None:
    """A scenario file nobody can load is a trap laid for the next operator."""
    from pathlib import Path

    shipped = sorted((Path(__file__).resolve().parents[1] / "scenarios").glob("*.json"))

    assert shipped, "world/scenarios/ must hold at least the election scenario"
    for path in shipped:
        scenario = Scenario.load(str(path))
        assert scenario.describe(), path.name
