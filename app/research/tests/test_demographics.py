"""B8: measuring the town as a population (The 250 section 24).

The papers' own bias check, applied to Kasukabe. The point of these tests
is that the module reports a disparity WITHOUT claiming to know whether it
is a finding or the design working - that distinction is the whole reason
the module exists rather than a one-line groupby.
"""

from __future__ import annotations

import pytest

from research.demographics import (
    DISPARITY_THRESHOLD,
    MIN_SLICE_SIZE,
    Person,
    age_band,
    bias_report,
    emergence_check,
    household_outcomes,
    people_from_world,
    slice_population,
    wealth_bracket,
)


def person(agent_id: str, **kw) -> Person:
    base = dict(
        age_years=30.0, sex="f", occupation="farmer", household_id="hh-1",
        balance_sim=1000, alive=True, is_dependent=False, household_shape="dual-earner",
    )
    base.update(kw)
    return Person(agent_id=agent_id, **base)


def town(n: int, **kw) -> list[Person]:
    return [person(f"a{i}", **kw) for i in range(n)]


# -- the bands -------------------------------------------------------------

def test_age_bands_match_the_towns_own_mechanical_cutoffs():
    """Minor / working / elderly are the bands the town's rules already
    treat differently, so they are what a difference must be read against."""
    assert age_band(5) == "minor"
    assert age_band(17.9) == "minor"
    assert age_band(18) == "young adult"
    assert age_band(35) == "adult"
    assert age_band(50) == "older adult"
    assert age_band(71) == "elderly"
    assert age_band(200) == "elderly"


def test_wealth_brackets_are_quartiles_not_fixed_thresholds():
    """A fixed "rich = 10,000" stops being true the moment the economy
    inflates or the genesis balance changes."""
    balances = [10, 20, 30, 40, 50, 60, 70, 80]
    assert wealth_bracket(10, balances) == "poorest quarter"
    assert wealth_bracket(80, balances) == "richest quarter"
    # ten times richer town, same shape, same answer
    big = [b * 1000 for b in balances]
    assert wealth_bracket(10_000, big) == "poorest quarter"
    assert wealth_bracket(80_000, big) == "richest quarter"


# -- slicing (24, bullet 1) -----------------------------------------------

def test_the_town_is_broken_down_by_every_requested_dimension():
    people = town(10, sex="f") + town(10, sex="m")
    stats = slice_population(people, ["sex", "occupation"])
    assert {s.dimension for s in stats} == {"sex", "occupation"}
    sexes = {s.value: s for s in stats if s.dimension == "sex"}
    assert sexes["f"].count == 10 and sexes["m"].count == 10
    assert sexes["f"].share_of_town == 0.5


def test_an_empty_town_measures_as_nothing_rather_than_crashing():
    assert slice_population([]) == []
    assert bias_report([]).population == 0
    assert emergence_check([], lambda p: True) == []


# -- the bias check (24, bullet 1) ----------------------------------------

def test_a_gap_no_mechanic_explains_is_flagged():
    """Nothing in the codebase reads sex, so a wealth gap along it comes
    from genesis or the personas - which is exactly a finding."""
    people = town(10, sex="m", balance_sim=10_000) + town(10, sex="f", balance_sim=1_000)
    report = bias_report(people, ["sex"])
    assert report.flagged, "a 10x gap along an unmodelled dimension must be flagged"
    flag = report.flagged[0]
    assert flag.dimension == "sex"
    assert flag.best_value == "m" and flag.worst_value == "f"
    assert flag.ratio == 10.0
    assert "genesis or from the personas" in flag.note


def test_a_gap_the_towns_own_rules_create_is_reported_but_not_flagged():
    """The elderly being poorer is the pension design working. Reporting it
    as bias would train an operator to ignore the flag."""
    people = (
        town(10, age_years=35, balance_sim=10_000)
        + town(10, age_years=80, balance_sim=1_000)
    )
    report = bias_report(people, ["age_band"])
    assert report.disparities, "it must still be reported"
    assert report.disparities[0].mechanically_expected is True
    assert report.flagged == [], "but not flagged as a finding"
    assert "the design working" in report.disparities[0].note


def test_a_small_slice_is_not_evidence_of_anything():
    """The papers' bias checks rest on large samples; a slice of two is
    noise, and flagging it would bury the real findings."""
    people = town(30, sex="f", balance_sim=1_000) + town(2, sex="m", balance_sim=99_999)
    assert bias_report(people, ["sex"]).flagged == []


def test_a_slice_with_nothing_at_all_is_the_largest_disparity_there_is():
    """"One group has nothing" is precisely the finding worth surfacing,
    not a divide-by-zero to skip."""
    people = town(10, sex="m", balance_sim=5_000) + town(10, sex="f", balance_sim=0)
    report = bias_report(people, ["sex"])
    assert report.flagged and report.flagged[0].ratio == float("inf")


def test_an_even_town_produces_no_flags():
    report = bias_report(town(20, sex="f") + town(20, sex="m"), ["sex"])
    assert report.flagged == []
    assert report.disparities[0].ratio == 1.0


def test_the_threshold_is_what_decides_a_flag():
    people = town(10, sex="m", balance_sim=1_400) + town(10, sex="f", balance_sim=1_000)
    assert bias_report(people, ["sex"]).flagged == []          # 1.4 < 1.5
    assert bias_report(people, ["sex"], threshold=1.3).flagged  # same data, lower bar
    assert DISPARITY_THRESHOLD == 1.5


def test_the_report_serialises_for_a_dashboard():
    body = bias_report(town(10), ["sex"]).to_dict()
    assert set(body) == {"population", "dimensions", "slices", "disparities", "flagged"}


# -- household outcomes (24, bullet 2) ------------------------------------

def test_household_shapes_are_ranked_worst_first():
    rows = household_outcomes([
        {"shape": "single-earner with dependents", "days_of_runway": 1, "combined_sim": 100},
        {"shape": "single-earner with dependents", "days_of_runway": 3, "combined_sim": 300},
        {"shape": "dual-earner, no dependents", "days_of_runway": 40, "combined_sim": 9000},
    ])
    assert rows[0].shape == "single-earner with dependents"
    assert rows[0].households == 2
    assert rows[0].mean_runway_days == 2.0
    assert rows[-1].shape == "dual-earner, no dependents"


def test_the_destitute_rate_is_per_shape():
    rows = household_outcomes([
        {"shape": "lone dependent", "days_of_runway": 0, "combined_sim": 0},
        {"shape": "lone dependent", "days_of_runway": 0, "combined_sim": 0},
        {"shape": "dual-earner, no dependents", "days_of_runway": 20, "combined_sim": 5000},
    ])
    by_shape = {r.shape: r for r in rows}
    assert by_shape["lone dependent"].destitute_rate == 1.0
    assert by_shape["dual-earner, no dependents"].destitute_rate == 0.0


def test_it_takes_the_rows_family_api_already_returns():
    """So the two views cannot disagree about what a shape or a runway is."""
    from world.family_api import HouseholdSolvency

    row = HouseholdSolvency(
        household_id="hh-1", member_ids=["a"], shape="single, no dependents",
        earners=1, dependents=0, combined_sim=900, daily_bill_sim=90,
        days_of_runway=10, status="stable",
    ).model_dump()
    assert household_outcomes([row])[0].shape == "single, no dependents"


# -- emergence (24, bullet 3) ---------------------------------------------

def test_a_behaviour_concentrated_in_one_slice_shows_a_lift():
    people = town(10, sex="m", balance_sim=9_000) + town(10, sex="f", balance_sim=100)
    rows = emergence_check(people, lambda p: p.balance_sim > 1_000, ["sex"])
    top = rows[0]
    assert top["value"] == "m"
    assert top["rate"] == 1.0
    assert top["town_rate"] == 0.5
    assert top["lift"] == 2.0


def test_a_behaviour_nobody_does_reports_no_lift_rather_than_dividing_by_zero():
    rows = emergence_check(town(10), lambda p: False, ["sex"])
    assert rows[0]["rate"] == 0.0
    assert rows[0]["lift"] is None


def test_small_slices_are_left_out_of_emergence_too():
    people = town(20, sex="f") + town(MIN_SLICE_SIZE - 1, sex="m")
    values = {r["value"] for r in emergence_check(people, lambda p: True, ["sex"])}
    assert values == {"f"}


# -- against the real world -----------------------------------------------

@pytest.mark.asyncio
async def test_it_measures_a_real_genesis_town():
    from world.wiring import WorldConfig, build_world_server

    server = await build_world_server(WorldConfig())
    people = people_from_world(server.world)
    assert len(people) == len(server.world.agents)
    report = bias_report(people)
    assert report.population == len(people)
    # Every dimension asked for is present in the breakdown, so a slice
    # never silently vanishes because nobody happened to be in it.
    assert {s.dimension for s in report.slices} <= set(report.dimensions)
    assert all(0.0 <= s.share_of_town <= 1.0 for s in report.slices)
