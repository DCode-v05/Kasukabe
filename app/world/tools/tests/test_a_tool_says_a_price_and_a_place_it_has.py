"""Tool text states prices and places the town actually has.

`ECON-27` and `TOOL-24` are the same species: resident-facing text that
asserts a fact the code does not hold. A resident cannot check either
claim; it acts on them.

ECON-27 names `buy_energy`'s "Cost: 2 sim-currency" against a schedule of
base 0. Checking it turned up two worse things in the same sentence:

  - "a fixed rate of 10,000 sim to the dollar" while `SIM_PER_USD` is
    5,000. ECON-05 halved the rate and this text was never touched, so
    the description of the one act that keeps a resident alive misquoted
    its own price by a factor of two.
  - "fixed" stopped being true a second time the moment ECON-30 made the
    government's credit-store margin a real deduction.

Both figures are now derived from the constants, so the next re-rating
cannot leave a lie here.

ECON-27's other half is `check_debts`, which promised "upkeep arrears"
that nothing records (ECON-15) and told residents to pay with `transfer`,
which refuses every non-agent recipient so a head-tax debt cannot be paid
that way at all (ECON-14). Both findings are still OPEN, so the honest
fix is to stop promising rather than to describe a route that ends in a
refusal.

TOOL-24: the tool index said "Only at government_complex" while
`list_places` calls the same ground "Kasukabe Government Complex". A
name plus `move` is the entire recovery path an index row offers, and it
pointed at a name the map does not use.

WHAT THIS DOES NOT DO. A first sweep of every "Cost: N sim" string in the
registry reported twelve mismatches. That was the regex, not the code -
descriptions phrase it "Cost: 5 sim plus 6 per batch" and the sweep
compared the first number against base+per-unit. A second attempt still
produced eleven false positives. Eleven descriptions are therefore NOT
touched here: mass-rewriting prose on a parse that has been wrong twice
would be worse than the drift it claims to fix.
"""
from __future__ import annotations

import pytest

from world.ledger.money import SIM_PER_USD, SIM_PER_USD_CENT
from world.tools.place_aliases import display_place
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map


# ── ECON-27 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buy_energy_quotes_the_real_rate(world, registry) -> None:
    """The 2x lie. A resident budgeting from this bought half the
    thinking it expected."""
    description = registry.tools["buy_energy"].description

    assert f"{SIM_PER_USD:,}" in description, (
        f"ECON-27: buy_energy does not quote the real rate. SIM_PER_USD is "
        f"{SIM_PER_USD:,} and the description says something else - so the "
        "tool that keeps a resident alive misstates its own price"
    )
    assert "10,000 sim to the dollar" not in description


@pytest.mark.asyncio
async def test_buy_energy_does_not_invent_a_fee(world, registry) -> None:
    """The finding's own half: base 0, and the description said 2."""
    from world.ledger.action_costs import DEFAULT_SCHEDULE

    rule = DEFAULT_SCHEDULE.rule_for("buy_energy")
    assert rule.base_cost == 0, "premise: the schedule charges nothing"
    assert "Cost: 2 sim" not in registry.tools["buy_energy"].description


@pytest.mark.asyncio
async def test_buy_energy_admits_the_margin_can_move(world, registry) -> None:
    """ECON-30 made the government's margin real. Calling the rate
    "fixed" was true once and is not now."""
    description = registry.tools["buy_energy"].description
    assert "fixed rate" not in description
    assert "margin" in description


@pytest.mark.asyncio
async def test_check_debts_promises_only_routes_that_work(
    world, registry
) -> None:
    """It offered `transfer` for a head-tax debt, and `transfer` refuses
    every non-agent recipient (ECON-14). Following your own toolbox into
    a refusal is worse than being told less."""
    description = registry.tools["check_debts"].description

    assert "upkeep arrears" not in description, (
        "ECON-27/ECON-15: check_debts still promises arrears that nothing "
        "in the town ever records"
    )
    assert "pay_wage" in description, "the route that does work is still named"


@pytest.mark.asyncio
async def test_the_cent_figure_matches_the_constant(world, registry) -> None:
    """Two numbers in one sentence, and both were stale. The per-cent
    figure said 100 when it is 50."""
    from world.ledger.money import SIM_PER_ENERGY_UNIT

    description = registry.tools["buy_energy"].description
    # The unit a resident reads is energy now, not cents; the per-unit figure
    # still has to be the constant's, never a number typed beside it.
    assert f"{SIM_PER_ENERGY_UNIT} sim buys 1 energy" in description


# ── TOOL-24 ─────────────────────────────────────────────────────────────

def test_a_gate_is_named_the_way_the_map_names_it() -> None:
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX

    assert display_place(LOCATION_GOVERNMENT_COMPLEX) == "Kasukabe Government Complex"


def test_a_place_with_no_sector_keeps_its_own_name() -> None:
    """Better named than not named. Returning "elsewhere" for every
    unmapped gate would take away the one thing an index row provides."""
    assert display_place("patent_office") == "patent_office"


def test_no_gate_at_all_reads_as_elsewhere() -> None:
    assert display_place(None) == "elsewhere"
    assert display_place("") == "elsewhere"


@pytest.mark.asyncio
async def test_an_index_row_names_a_place_list_places_also_names(
    world, registry
) -> None:
    """The finding, end to end: the name in the reduced row must be
    findable in the list a resident would look it up in."""
    from world.tools.primitives.places import known_places
    from world.tools.registry import _index_row

    spec = registry.tools["vote"]
    assert isinstance(spec.location_gate, str), "premise: vote is gated"

    row = _index_row(spec, world)
    named = row["description"].split("Only at ", 1)[1].split(";", 1)[0]

    place_names = {p["place_id"] for p in known_places(world)}
    assert named in place_names, (
        f"TOOL-24: the index row sends a resident to {named!r}, which does "
        "not appear in list_places - so the name plus `move` recovery path "
        "the row exists for points at nothing"
    )


def test_the_reverse_map_is_derived_not_typed() -> None:
    """One definition. A hand-typed reverse table drifts the first time
    the forward one is edited."""
    from world.tools.place_aliases import (
        SECTOR_TO_TOOL_LOCATION,
        TOOL_LOCATION_TO_SECTOR,
    )

    assert TOOL_LOCATION_TO_SECTOR == {
        v: k for k, v in SECTOR_TO_TOOL_LOCATION.items()
    }
