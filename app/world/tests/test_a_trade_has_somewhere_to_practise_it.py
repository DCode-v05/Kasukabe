"""A resident given a trade is given the premises that trade needs.

Delia Okonjo is the town's pharmacist. `genesis_20_funded.json` had no
pharmacy in it. She spent the morning of the 28 September run discovering
that, in her own words:

    The system does not know my business - the shelf refused my hand, the
    business id failed.

    The Willows has no registered business ID in her name. She has treated
    the sick there for years, stocked shelves, kept accounts, but the ledger
    shows nothing.

    the memory-that-is-not-my-own tells me i am the pharmacist. i know this.
    i live it. yet the system does not know my business

Her persona, her occupation and the map all agreed she ran a pharmacy. The
business registry was the one place that did not, so every verb that takes a
`business_id` refused her, and she could not stock a shelf, post a price or
draw from a till all day.

IT ALSO QUIETLY BROKE ECON-29. `economy_bridge` routes a `pharmacy_treatment`
fee to whichever business has `business_type == "pharmacy"` and falls back to
the treasury when there is none - which is exactly the defect ECON-29 was
written to fix ("a resident who paid a pharmacist for medicine was paying the
government, and the pharmacist earned nothing from the one service it exists
to sell"). The fix held in the code and was undone by a cast file, so the
pharmacist's income went to the treasury again with nothing logged.

Sixteen residents across four cast files were in this position: four bakers
with no bakery, four innkeepers with no inn, five shopkeepers with no shop,
and Delia.
"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict

import pytest

GENESIS = pathlib.Path(__file__).resolve().parents[2] / "world"
GENESIS_FILES = sorted(p.name for p in GENESIS.glob("genesis*.json"))

NEEDS_PREMISES = {
    "pharmacist": "pharmacy",
    "baker": "bakery",
    "miller": "mill",
    "grocer": "grocery",
    "grocery_store_owner": "grocery",
    "shopkeeper": "shop",
    "innkeeper": "inn",
    "cafe_owner": "cafe",
}


def _hands(data: dict) -> set[str]:
    """Everyone with a place in a business, as owner or as staff."""
    shops = data.get("small_businesses") or []
    who = {b["owner_agent_id"] for b in shops}
    who |= {s for b in shops for s in (b.get("employee_agent_ids") or [])}
    yard = data.get("construction_company")
    if yard:
        who.add(yard["owner_agent_id"])
        who |= set(yard.get("hand_agent_ids") or [])
    return who


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_nobody_is_given_a_trade_with_nowhere_to_practise_it(path: str) -> None:
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    placed = _hands(data)

    stranded = [
        f"{a['agent_id']} {a['name']} ({a['occupation']})"
        for a in (data.get("agents") or [])
        if a["occupation"] in NEEDS_PREMISES and a["agent_id"] not in placed
    ]

    assert not stranded, (
        f"{path} gives these residents a trade and no premises, so every verb "
        f"taking a business_id refuses them all run: {stranded}"
    )


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_the_trade_and_the_premises_are_the_same_trade(path: str) -> None:
    """A baker placed in a pharmacy is placed, and still cannot bake."""
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    shops = data.get("small_businesses") or []
    where = defaultdict(set)
    for b in shops:
        where[b["owner_agent_id"]].add(b["business_type"])
        for s in b.get("employee_agent_ids") or []:
            where[s].add(b["business_type"])

    for a in data.get("agents") or []:
        want = NEEDS_PREMISES.get(a["occupation"])
        if not want or a["agent_id"] not in where:
            continue
        assert any(want in t for t in where[a["agent_id"]]), (
            f"{path}: {a['name']} is a {a['occupation']} placed in "
            f"{sorted(where[a['agent_id']])} - no {want}"
        )


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_a_town_with_a_pharmacist_has_a_pharmacy_for_the_fees_to_reach(path: str) -> None:
    """ECON-29, guarded from the cast side.

    `economy_bridge.payee_for` sends a `pharmacy_treatment` fee to a business
    of type `pharmacy` and to the TREASURY when the town has none. A cast
    with a pharmacist and no pharmacy therefore reinstates the exact defect
    ECON-29 fixed, in a file no test was reading.
    """
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    pharmacists = [a for a in (data.get("agents") or []) if a["occupation"] == "pharmacist"]
    if not pharmacists:
        pytest.skip("no pharmacist in this cast")

    types = {b["business_type"] for b in (data.get("small_businesses") or [])}

    assert any("pharmacy" in t for t in types), (
        f"{path} has {len(pharmacists)} pharmacist(s) and no pharmacy, so every "
        "treatment fee they earn is paid to the treasury instead"
    )


def test_the_trade_list_is_about_premises_and_not_about_status() -> None:
    """The guard must not creep into trades that need no shop.

    A farmer, a forester, a miner and a day labourer all work somewhere the
    world already provides. Requiring premises for them would demand a
    business row for most of the town and say nothing true.
    """
    for free in ("farmer", "forester", "miner", "day_laborer", "artisan",
                 "trader", "office_administrator"):
        assert free not in NEEDS_PREMISES
