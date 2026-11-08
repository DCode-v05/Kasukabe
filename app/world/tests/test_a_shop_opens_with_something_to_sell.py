"""Every shop in Kasukabe has been empty in every run ever made.

MEASURED across runs 5 to 8 of the series, from the residents' own failed
tool calls:

    world:buy   "b_bakery needs 2 bread, has 0"          x7
    world:sell  "no business with id 'harvey_oak_supply'" x5
    world:give  "you hold 0 stale bread, not 1"
    world:trade "you hold 0 bread, not 1"

Residents try to buy, sell, give and trade. Every attempt fails, because
there is nothing anywhere: `genesis_10.json` and `genesis_20.json` both
carry `"inventory": null, "stock": null` on every business, `SmallBusiness`
has no field for either, and pydantic drops them. Two declared keys, read
by nothing - the same shape as the dead compaction knob that was tuned
for weeks while deciding nothing.

WHAT IT EXPLAINS. The settlement gate has read zero on four consecutive
runs, and the reason is not that the residents will not bargain. It is
that a bakery with no bread is not a bakery, so there is nothing in the
town to bargain OVER. Run 6's transcript is six people discovering this
one after another: "The larder's bare", "Supply store was empty when I
went", "What can a hungry student do to earn bread?"

WHAT THIS IS NOT. It is not a subsidy to the residents. Nobody is given
anything: a shop is given the goods it is declared to sell, which is what
makes it the shop genesis already says it is. Residents still have empty
hands, still have to earn, and still have to walk to the counter and pay
- and the ledger invariant is untouched, because goods are not money.

IT DOES CHANGE THE INITIAL CONDITIONS, and that is stated rather than
buried: runs before this and runs after it are not comparable on the
settlement gate. The series report says which side of the line each run
falls on.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from world.life.models import SmallBusiness

GENESIS = pathlib.Path(__file__).resolve().parents[2] / "world"


def test_the_model_has_somewhere_to_put_it() -> None:
    """The JSON has carried `inventory` and `stock` keys the whole time
    and the model had no field for either, so pydantic dropped them
    silently. A declared key nothing reads is worse than no key."""
    assert "opening_stock" in SmallBusiness.model_fields


def test_a_business_with_no_declared_stock_gets_nothing() -> None:
    """No magic. A shop is given what genesis says it sells and not one
    item more."""
    shop = SmallBusiness(
        business_id="b1", name="A Shop", business_type="grocery",
        owner_agent_id="n01", starting_sim_balance=100,
    )
    assert shop.opening_stock == {}


def test_declared_stock_survives_the_parse() -> None:
    shop = SmallBusiness(
        business_id="b_bakery", name="Bakery", business_type="bakery",
        owner_agent_id="n07", starting_sim_balance=100,
        opening_stock={"bread": 12, "flour": 20},
    )
    assert shop.opening_stock == {"bread": 12, "flour": 20}


GENESIS_FILES = sorted(p.name for p in GENESIS.glob("genesis*.json"))

SELLS_A_SERVICE_NOT_GOODS: set[str] = set()


def test_there_are_genesis_files_to_check() -> None:
    """A glob that matched nothing would make every test below vacuous."""
    assert len(GENESIS_FILES) > 10, GENESIS_FILES


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_the_cast_files_open_their_shops(path: str) -> None:
    """The fix is only real if the files the runs actually use carry it.
    A field nothing populates is the defect this replaces, not a fix."""
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    shops = data.get("small_businesses") or []
    assert shops, f"{path} declares no small businesses"
    for shop in shops:
        if shop["business_type"] in SELLS_A_SERVICE_NOT_GOODS:
            continue
        stock = shop.get("opening_stock") or {}
        assert stock, f"{shop['business_id']} in {path} opens with nothing to sell"
        assert all(isinstance(n, int) and n > 0 for n in stock.values())


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_the_yard_can_break_ground(path: str) -> None:
    """A crew that has to wait for a supply chain never starts one.

    election30 felled timber 52 times, crafted planks, and raised 0
    buildings - all seven `work_site` calls failed, and the yard owned
    nothing to build with on the morning it opened.
    """
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    yard = data.get("construction_company")
    assert yard, f"{path} declares no construction company"
    stock = yard.get("opening_stock") or {}
    assert stock.get("timber", 0) > 0, f"{path}'s yard opens with no timber"


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_nobody_wakes_up_with_money_and_no_food(path: str) -> None:
    """Genesis has always handed out a purse and never a larder.

    Every resident in every run has woken with `starting_sim_balance` and
    bare shelves, in a town whose shops were also empty.
    """
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    for agent in data.get("agents") or []:
        larder = agent.get("opening_stock") or {}
        assert larder, f"{agent['agent_id']} in {path} wakes with nothing to eat"


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_a_larder_is_one_day_not_the_whole_run(path: str) -> None:
    """The seed must not quietly pass the meals bar on its own.

    ECON-4 asks for a meal per resident per day. A larder big enough to
    cover a two-day run would tick that box without the town ever feeding
    itself, which is the measurement this exists to make honest.
    """
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    for agent in data.get("agents") or []:
        units = sum((agent.get("opening_stock") or {}).values())
        assert units <= 4, (
            f"{agent['agent_id']} in {path} starts with {units} units of food - "
            "enough to skip the economy for the length of a short run"
        )


def test_every_seeded_good_is_a_thing_the_world_knows() -> None:
    """A credited item nobody can eat, sell or build with is a silent
    no-op: `inventory.credit` takes any string."""
    from world.tools.primitives.living import EDIBLE

    buildable = {"timber", "planks", "iron_ore", "iron_tool", "copper_ore", "stone"}
    known = EDIBLE | buildable
    for path in GENESIS_FILES:
        data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
        holders = [
            *(data.get("small_businesses") or []),
            *(data.get("agents") or []),
            data.get("construction_company") or {},
        ]
        for holder in holders:
            for item in (holder.get("opening_stock") or {}):
                assert item in known, f"{path} seeds unknown good {item!r}"


def test_a_bakery_opens_with_bread() -> None:
    """The exact failure the residents hit: `buy` answering "b_bakery
    needs 2 bread, has 0"."""
    data = json.loads((GENESIS / "genesis_10.json").read_text(encoding="utf-8"))
    bakery = next(
        shop for shop in data["small_businesses"] if shop["business_id"] == "b_bakery"
    )
    assert bakery["opening_stock"].get("bread", 0) > 0


def test_the_opening_stock_is_a_morning_not_a_warehouse() -> None:
    """A shop that can never run out is a shop with no economics in it.
    The point of stocking one is that the town can start trading, not
    that scarcity is abolished - and DESIGN.md's whole premise is that
    nobody is guaranteed a living."""
    data = json.loads((GENESIS / "genesis_10.json").read_text(encoding="utf-8"))
    for shop in data["small_businesses"]:
        for item, count in shop["opening_stock"].items():
            assert count <= 40, f"{shop['business_id']} holds {count} {item}"
