"""Sim-currency is the town's money. Dollars are energy, and only energy.

The mechanics already held this, and it was checked before this file was
written rather than assumed. No tool takes a currency. Every trade, sale,
wage, tax, fee, upkeep charge and debt in runs 25 and 26 was sim. The only
dollar movements were thinking burned, sim exchanged for thinking, a revival
and an estate, and 0 of 505 utterances across both runs named a dollar
amount.

What did NOT hold was what residents were told. The law opened "Thinking
costs real money ... billed against your own wallet in real US dollars";
every turn read "Energy: $5.34 in your wallet"; `buy_energy` said sim
"becomes the dollars that pay for your own thought ... This is the only way
a dollar wallet ever goes up". A unit described as money, in a wallet, with a
dollar sign, is a unit a resident is invited to use as money.

So every surface a resident reads is checked here for the vocabulary of
money applied to energy, and the mechanics are swept so that "nothing lets a
resident trade dollars" stays a property of the code rather than of the runs
that happened to be made.

Found on the way: `revive` said "Pay 10,000 sim" for a fee of
`REVIVAL_COST_SIM` = 5,000. ECON-05 halved the rate and the sentence was
never touched, so the law and the tool quoted two prices for the same act.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from world.ledger.money import (
    ENERGY_UNITS_PER_USD_CENT,
    SIM_PER_ENERGY_UNIT,
    SIM_PER_USD,
    SIM_PER_USD_CENT,
    energy_units,
)
from world.life.constants import (
    DORMANCY_THRESHOLD_USD_CENTS,
    REVIVAL_COST_SIM,
    REVIVAL_USD_CREDIT_CENTS,
)
from world.life.soul_seed import build_soul_seed, law_of_the_town_rules
from world.retrieval_depth import format_volatile_line

MONEY_WORDS = re.compile(r"\$|\bdollars?\b|\busd\b|\bwallet\b|\bcents?\b", re.IGNORECASE)

PRIMITIVES = Path(__file__).resolve().parents[1] / "primitives"


def test_the_law_never_describes_energy_as_money():
    offenders = [rule for rule in law_of_the_town_rules() if MONEY_WORDS.search(rule)]
    assert not offenders, "the law still speaks of dollars:\n" + "\n".join(offenders)


def test_the_law_says_sim_is_the_only_money_and_energy_is_not():
    text = " ".join(law_of_the_town_rules())
    assert "Sim-currency is the only money in Kasukabe" in text
    assert "it is not money" in text
    assert "nobody can pay it, trade it, lend it or give it" in text


def test_the_opening_balance_is_stated_in_energy():
    seed = build_soul_seed(
        SimpleNamespace(name="Ulrike Greenhow", usd_wallet_cents=200, starting_sim_balance=5000)
    )
    assert "2,000 energy" in seed
    assert not MONEY_WORDS.search(seed), seed


def test_every_turn_shows_energy_in_units():
    line = format_volatile_line({
        "usd_wallet_cents": 534,
        "last_turn_usd_cents": 0.5,
        "runway_game_days": 8.1,
        "sim_balance": 3799,
    })
    assert "Energy: 5,340." in line
    assert "Your last turn used 5 energy." in line
    assert "3799 sim" in line
    assert not MONEY_WORDS.search(line), line


def test_a_turn_smaller_than_one_unit_says_so_rather_than_zero():
    line = format_volatile_line({"usd_wallet_cents": 100, "last_turn_usd_cents": 0.02})
    assert "under 1 energy" in line


def test_buy_energy_and_revive_speak_in_energy(registry):
    for name in ("buy_energy", "revive"):
        description = registry.tools[name].description
        assert not MONEY_WORDS.search(description), f"{name}: {description}"


def test_revive_quotes_the_fee_it_actually_charges(registry):
    description = registry.tools["revive"].description
    assert f"{REVIVAL_COST_SIM:,} sim" in description
    if REVIVAL_COST_SIM != 10_000:
        assert "10,000" not in description, "the stale ECON-05 price is back"


def test_one_unit_of_energy_costs_a_whole_number_of_sim():
    assert SIM_PER_ENERGY_UNIT * ENERGY_UNITS_PER_USD_CENT == SIM_PER_USD_CENT
    assert SIM_PER_USD % SIM_PER_ENERGY_UNIT == 0


def test_the_thresholds_residents_are_told_are_whole():
    assert energy_units(DORMANCY_THRESHOLD_USD_CENTS) == DORMANCY_THRESHOLD_USD_CENTS * 10
    assert energy_units(REVIVAL_USD_CREDIT_CENTS) == REVIVAL_USD_CREDIT_CENTS * 10


def test_no_tool_lets_a_resident_choose_a_currency(registry):
    """If a params model ever grows a `currency` field, energy has become
    something a resident can pay with."""
    offenders = [
        name for name, spec in registry.tools.items()
        if "currency" in spec.params_model.model_fields
    ]
    assert not offenders, offenders


def test_no_primitive_moves_dollars_by_itself():
    """Dollars move through three ledger paths only - burn, the sim-to-energy
    exchange, and an estate - none of them written in a tool handler. A
    handler constructing a USD movement is a resident paying in energy."""
    offenders = [
        path.name for path in sorted(PRIMITIVES.glob("*.py"))
        if "Currency.USD" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders
