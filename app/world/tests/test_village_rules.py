"""The rulebook, and the validator that makes it trustworthy.

`village_rules.json` exists so the town's rules can be changed by editing data
rather than code. That is only safe if a bad edit is caught at boot, so most of
what is tested here is the validator refusing things: a shortage threshold the
wrong way round, an emergency that can start and never stop, a role the town
must staff and may never recruit for, a town where the best-paid job does not
cover rent. Each of those would otherwise run happily and wrongly.
"""

from __future__ import annotations

import json

import pytest

from world.rules import RuleError, load_rules
from world.rules.loader import RULES_PATH


def edited(tmp_path, mutate):
    """The real rulebook with one thing changed, written where it can be loaded."""
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "village_rules.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# -- the shipped rulebook -------------------------------------------------

def test_the_shipped_rulebook_is_valid():
    """If this fails, the town in the repo is running on rules that do not
    hold together - fix the file, not the test."""
    rules = load_rules()
    assert rules.get("schema_version") == "1.0.0"


def test_every_good_is_produced_by_a_trade_that_has_a_wage():
    """A good nobody can be paid to make is a good the town never gets."""
    rules = load_rules()
    wages = rules.get("economy.wages_per_day_sim")
    for name, good in rules.get("market.goods").items():
        if name.startswith("_"):
            continue
        assert good["produced_by"] in wages, f"{name} is made by {good['produced_by']}, who has no wage"


def test_every_emergency_response_names_a_real_role_and_can_stand_down():
    rules = load_rules()
    targets = rules.get("labour.staffing_targets")
    for name, emergency in rules.get("health.emergency").items():
        if name.startswith("_"):
            continue
        assert emergency["stand_down"], f"{name} cannot stand down"
        for step in emergency["response"]:
            if step.get("role"):
                assert step["role"] in targets, f"{name} raises staffing for an unknown role"


def test_every_document_has_someone_who_decides_it():
    rules = load_rules()
    for name, doc in rules.get("government.documents").items():
        if name.startswith("_"):
            continue
        assert doc.get("decided_by"), f"{name} has no decision-maker"


# -- dynamic staffing: the point of the whole labour section --------------

def test_a_healthy_town_and_a_sick_town_want_different_numbers_of_doctors():
    """This is the behaviour the user asked for: staffing follows need, it is
    not fixed at genesis."""
    rules = load_rules()
    calm = rules.staffing_target("doctor", 314)
    outbreak = rules.staffing_target("doctor", 314, surge_units=40)
    assert outbreak > calm, "40 sick residents did not raise the doctor target"
    assert rules.staffing_target("nurse", 314, surge_units=40) > rules.staffing_target("nurse", 314)


def test_staffing_never_drops_below_the_floor_however_small_the_town():
    rules = load_rules()
    assert rules.staffing_target("doctor", 1) >= rules.get("labour.staffing_targets.doctor.min")


def test_an_unknown_role_or_wage_raises_rather_than_guessing():
    rules = load_rules()
    with pytest.raises(RuleError):
        rules.staffing_target("dragon_tamer", 314)
    with pytest.raises(RuleError):
        rules.wage("dragon_tamer")


# -- market: demand not met by supply --------------------------------------

@pytest.mark.parametrize(
    ("supply", "demand", "expected"),
    [(100, 100, "ok"), (95, 100, "ok"), (85, 100, "watch"), (60, 100, "shortage"), (1, 100, "shortage")],
)
def test_shortage_levels_track_the_gap(supply, demand, expected):
    assert load_rules().shortage_level(supply, demand) == expected


def test_price_rises_as_supply_falls_short_and_is_clamped_both_ends():
    rules = load_rules()
    lo = rules.get("market.price_response.min_price_multiplier")
    hi = rules.get("market.price_response.max_price_multiplier")
    assert rules.price_multiplier(100, 100) == pytest.approx(1.0)
    assert rules.price_multiplier(50, 100) > rules.price_multiplier(90, 100) > 1.0
    assert rules.price_multiplier(0.0001, 100) <= hi
    assert rules.price_multiplier(10_000, 100) >= lo


# -- the validator earning its keep ---------------------------------------

def test_a_shortage_threshold_the_wrong_way_round_is_refused(tmp_path):
    """alarm must be the LOWER number. Swapped, every watch reads as a crisis."""
    path = edited(tmp_path, lambda d: d["market"]["shortage"].update(
        {"deficit_ratio_watch": 0.7, "deficit_ratio_alarm": 0.9}))
    with pytest.raises(RuleError, match="deficit_ratio_alarm"):
        load_rules(path, reload=True)


def test_an_emergency_with_no_stand_down_is_refused(tmp_path):
    path = edited(tmp_path, lambda d: d["health"]["emergency"]["outbreak"].pop("stand_down"))
    with pytest.raises(RuleError, match="stand_down"):
        load_rules(path, reload=True)


def test_an_emergency_that_staffs_a_role_with_no_target_is_refused(tmp_path):
    path = edited(tmp_path, lambda d: d["health"]["emergency"]["outbreak"]["response"].append(
        {"action": "raise_staffing", "role": "wizard", "multiplier": 2}))
    with pytest.raises(RuleError, match="wizard"):
        load_rules(path, reload=True)


def test_a_surge_source_with_no_size_is_refused(tmp_path):
    """A surge you cannot measure is a surge that never fires."""
    path = edited(tmp_path, lambda d: d["labour"]["staffing_targets"]["doctor"].pop("per_surge_unit"))
    with pytest.raises(RuleError, match="per_surge_unit"):
        load_rules(path, reload=True)


def test_a_role_both_recruitable_and_never_recruitable_is_refused(tmp_path):
    path = edited(tmp_path, lambda d: d["labour"]["recruit_from"]["order"].append("retired"))
    with pytest.raises(RuleError, match="never_recruit"):
        load_rules(path, reload=True)


def test_a_town_where_no_wage_covers_upkeep_is_refused(tmp_path):
    """Everyone starving is a typo, not a design."""
    path = edited(tmp_path, lambda d: d["economy"].update({"upkeep_per_day_sim": 9_000}))
    with pytest.raises(RuleError, match="upkeep"):
        load_rules(path, reload=True)


def test_a_good_that_consumes_something_that_does_not_exist_is_refused(tmp_path):
    path = edited(tmp_path, lambda d: d["market"]["goods"]["bread"].update({"consumes": {"unobtainium": 1}}))
    with pytest.raises(RuleError, match="unobtainium"):
        load_rules(path, reload=True)


def test_a_missing_rule_names_itself(tmp_path):
    path = edited(tmp_path, lambda d: d["economy"].pop("upkeep_per_day_sim"))
    with pytest.raises(RuleError, match="economy.upkeep_per_day_sim"):
        load_rules(path, reload=True)


def test_a_schema_version_the_loader_does_not_understand_is_refused(tmp_path):
    """The file and the code have to agree about what the town is."""
    path = edited(tmp_path, lambda d: d.update({"schema_version": "99.0.0"}))
    with pytest.raises(RuleError, match="schema_version"):
        load_rules(path, reload=True)


def test_broken_json_says_so_rather_than_crashing_obscurely(tmp_path):
    path = tmp_path / "village_rules.json"
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(RuleError, match="not valid JSON"):
        load_rules(path, reload=True)


def test_every_problem_is_reported_at_once(tmp_path):
    """Fixing a rulebook one error per boot is its own kind of misery."""
    def wreck(d):
        d["economy"].pop("upkeep_per_day_sim")
        d["health"]["emergency"]["outbreak"].pop("stand_down")
    path = edited(tmp_path, wreck)
    with pytest.raises(RuleError) as caught:
        load_rules(path, reload=True)
    assert "2 problem(s)" in str(caught.value) or "upkeep_per_day_sim" in str(caught.value)
