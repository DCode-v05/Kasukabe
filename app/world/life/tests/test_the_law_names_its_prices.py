"""A rule with no number is not a rule anybody can obey.

ECON-24. The law block every resident is booted with told them the town
has rules and never what they cost. It named no upkeep amount, so the
one bill nobody can opt out of - DESIGN.md section 3's load-bearing
"doing nothing is also a way to die" - was a fact about the world that
no resident could plan around; the perception brief said "upkeep is
charged to your household every day" and stopped there. It said nothing
at all about action costs, so `say`, the only tool that reaches another
person, had a price nobody had ever been told. And it promised both a
dormancy and a rescue while naming neither the balance that triggers the
first nor the fee that buys the second, which makes the rescue
unaskable-for: a resident cannot request what it cannot price, and a
neighbour standing over them cannot decide whether to pay. DESIGN.md
section 4 asks for residents "told the prices and the rules"; they were
told the rules.

The numbers are added by IMPORT, never by typing, and one test here
exists solely to prove it. `buy_energy`'s own tool description said ten
thousand sim to the dollar for months after the rate became five
thousand, because it was a string in a description and nothing made it
follow the constant. A law block that lies about a price is worse than
one that omits it: an omitted price sends a resident to go and look, and
a wrong one does not. So `test_a_re_rate_moves_the_law_rather_than_
leaving_it_stale` re-points each constant and asserts the rendered text
moves with it - which fails immediately if any figure was transcribed.

The wiring tests drive `build_soul_seed` and `default_broadcast_message`
rather than `law_of_the_town_rules` alone, because those two are what a
resident and an operator broadcast actually receive: a rule that exists
in the tuple and never reaches the rendered block is the same as no
rule at all.
"""

from __future__ import annotations

import pytest

# The law states energy, not dollars (world/ledger/money.py). Every figure is
# still derived from the constant that charges it; only the unit changed.
from world.ledger.money import energy_units, format_energy

from world.ledger.action_costs import ActionCostCategory, ActionCostRule, DEFAULT_RULE, SCHEDULE_TABLE
from world.life import soul_seed
from world.life.constants import (
    DORMANCY_DEATH_HOURS,
    DORMANCY_THRESHOLD_USD_CENTS,
    REVIVAL_COST_SIM,
    REVIVAL_USD_CREDIT_CENTS,
)
from world.life.models import GenesisAgent, MaritalStatus, Sex
from world.life.soul_seed import (
    build_soul_seed,
    default_broadcast_message,
    format_sim,
    format_usd_cents,
    law_of_the_town_rules,
)
from world.life.upkeep import (
    HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD,
    HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD,
    UPKEEP_FOOD_SIM_PER_DAY,
    UPKEEP_HEAT_SIM_PER_DAY,
    UPKEEP_SHELTER_SIM_PER_DAY,
    UPKEEP_TOTAL_SIM_PER_DAY,
)


def make_agent(**overrides: object) -> GenesisAgent:
    defaults: dict[str, object] = dict(
        agent_id="a0001",
        name="Yuki Tanaka",
        age_years=34.0,
        sex=Sex.FEMALE,
        household_id="h0001",
        marital_status=MaritalStatus.SINGLE,
        occupation="trader",
        personality_traits=["careful"],
        habits=["walks the market at dawn"],
        bio="Yuki Tanaka is a 34-year-old resident of Kasukabe.",
        ablation_arm="full",
        usd_wallet_cents=200,
        starting_sim_balance=5_000,
    )
    defaults.update(overrides)
    return GenesisAgent.model_validate(defaults)


def law_text() -> str:
    return " ".join(law_of_the_town_rules())


def test_the_law_names_the_daily_upkeep_bill_and_who_carries_it() -> None:
    text = law_text()
    assert format_sim(UPKEEP_TOTAL_SIM_PER_DAY) in text, (
        "ECON-24: the one cost no resident can avoid was never priced to them"
    )
    for part in (
        UPKEEP_FOOD_SIM_PER_DAY,
        UPKEEP_SHELTER_SIM_PER_DAY,
        UPKEEP_HEAT_SIM_PER_DAY,
    ):
        assert str(part) in text
    assert "household" in text
    assert str(HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD) in text
    assert str(HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD) in text


def test_the_law_names_what_an_action_costs() -> None:
    text = law_text()
    assert format_sim(SCHEDULE_TABLE["say"].min_cost) in text, (
        "ECON-24: speaking is the only tool that reaches another person "
        "and its price had never been stated anywhere a resident reads"
    )
    assert format_sim(DEFAULT_RULE.min_cost) in text, (
        "a tool with no row of its own is not free, and the law has to say so"
    )
    assert "sim balance" in text or "sim-currency" in text


def test_the_law_names_the_dormancy_threshold_and_the_price_of_a_rescue() -> None:
    text = law_text()
    assert f"falls to {energy_units(DORMANCY_THRESHOLD_USD_CENTS):,}" in text
    assert format_sim(REVIVAL_COST_SIM) in text, (
        "ECON-24: a rescue nobody can price is a rescue nobody can ask for"
    )
    assert format_energy(REVIVAL_USD_CREDIT_CENTS) in text
    assert f"{int(DORMANCY_DEATH_HOURS)} game-hours" in text


def test_the_seed_a_resident_is_booted_with_carries_every_price() -> None:
    """The wiring half. `build_soul_seed` is what `run_genesis` writes
    into persona.txt and what the verb-reach harness hands an engine; a
    rule that lives only in the tuple reaches nobody."""
    text = build_soul_seed(make_agent())
    for figure in (
        format_sim(UPKEEP_TOTAL_SIM_PER_DAY),
        format_sim(SCHEDULE_TABLE["say"].min_cost),
        format_sim(REVIVAL_COST_SIM),
        format_energy(REVIVAL_USD_CREDIT_CENTS),
        f"falls to {energy_units(DORMANCY_THRESHOLD_USD_CENTS):,}",
    ):
        assert figure in text, f"{figure} never reached the booted seed"


def test_the_operator_broadcast_carries_the_same_prices() -> None:
    """The reminder and the genesis law render from one tuple, so an
    address to the nation can never quote a different upkeep bill than
    the one a resident was born knowing."""
    message = default_broadcast_message()
    for rule in law_of_the_town_rules():
        assert rule in message


def test_a_re_rate_moves_the_law_rather_than_leaving_it_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason every figure is imported. Re-point each constant and
    the rendered law has to follow it - which it cannot do if the number
    was typed into the sentence, which is exactly how a tool description
    came to advertise an exchange rate that had changed months earlier.
    """
    monkeypatch.setattr(soul_seed, "UPKEEP_TOTAL_SIM_PER_DAY", 4_321)
    monkeypatch.setattr(soul_seed, "REVIVAL_COST_SIM", 7_777)
    monkeypatch.setattr(soul_seed, "REVIVAL_USD_CREDIT_CENTS", 313)
    monkeypatch.setattr(soul_seed, "DORMANCY_THRESHOLD_USD_CENTS", 2)
    monkeypatch.setattr(
        soul_seed,
        "DEFAULT_RULE",
        ActionCostRule(category=ActionCostCategory.FEE, base_cost=99, min_cost=99),
    )
    monkeypatch.setattr(
        soul_seed,
        "SCHEDULE_TABLE",
        {"say": ActionCostRule(category=ActionCostCategory.SOCIAL, base_cost=42, min_cost=42)},
    )

    text = law_text()
    for moved in (
        format_sim(4_321),
        format_sim(7_777),
        format_energy(313),
        f"falls to {energy_units(2):,}",
        format_sim(99),
        format_sim(42),
    ):
        assert moved in text, f"{moved} did not follow its constant: it was transcribed"

    # And the real upkeep bill is gone, so nothing is quoting both at
    # once. Only upkeep is checked for absence: the revival fee and the
    # exchange rate are the same number by construction
    # (REVIVAL_COST_SIM is SIM_PER_USD, see constants.py), so the real
    # figure legitimately survives in the un-patched exchange rule.
    assert format_sim(UPKEEP_TOTAL_SIM_PER_DAY) not in text
