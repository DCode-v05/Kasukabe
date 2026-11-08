"""The fifty-resident cast has to be a town, not fifty rows.

`world/genesis_50_town.json` keeps the twenty authored residents exactly as
they were - same ids, same debts, same secrets, so the next run is
comparable with runs 16 to 19 - and adds thirty carved out of the
generated town in whole households.

Every assertion here is a way the file could be valid JSON and still
produce a broken town. Most of them have happened at least once:

  * a persona thin enough to pass `Genesis` and fail `RichGenesis`, which
    is how run 20's first attempt died at boot;
  * a spouse, parent or opinion pointing at somebody who was left behind -
    a neighbour an agent is told about and can never find;
  * a shop whose owner did not come, which can never open;
  * a resident seeded before their own parent, which cost run 16 one
    resident's entire existence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map

GENESIS = Path(__file__).resolve().parent.parent / "genesis_50_town.json"


@pytest.fixture(scope="module")
def town() -> dict:
    return json.loads(GENESIS.read_text())


@pytest.fixture(scope="module")
def ids(town: dict) -> set[str]:
    return {a["agent_id"] for a in town["agents"]}


def test_there_are_fifty_of_them_and_each_is_distinct(town: dict, ids: set[str]) -> None:
    assert town["population"] == 50
    assert len(town["agents"]) == 50
    assert len(ids) == 50


def test_the_authored_twenty_are_untouched(town: dict) -> None:
    """The point of building this file rather than using the existing
    genesis_50.json: continuity with every run so far."""
    authored = json.loads((GENESIS.parent / "genesis_20.json").read_text())
    by_id = {a["agent_id"]: a for a in town["agents"]}
    for original in authored["agents"]:
        kept = by_id.get(original["agent_id"])
        assert kept is not None, f"{original['name']} was dropped from the cast"
        assert kept["name"] == original["name"]
        # Every authored field is untouched, and every relationship they
        # arrived with survives. Opinions may be ADDED - the newcomers have
        # to be knowable, and a town where the original twenty have never
        # heard of the thirty who joined is two towns on one map - but
        # nothing authored may be edited or dropped.
        for field, value in original["persona"].items():
            if field == "opinions":
                continue
            assert kept["persona"][field] == value, (
                f"{original['name']}'s {field} changed - the debts and secrets "
                "the runs are about live in here"
            )
        arrived_with = {
            (op["target_agent_id"], op["kind"]) for op in original["persona"]["opinions"]
        }
        still_here = {
            (op["target_agent_id"], op["kind"]) for op in kept["persona"]["opinions"]
        }
        assert arrived_with <= still_here, (
            f"{original['name']} lost relationships they were authored with: "
            f"{sorted(arrived_with - still_here)}"
        )


def test_every_resident_parses_as_the_shape_the_runner_loads(town: dict) -> None:
    """`RichGenesis`, not `Genesis`. The loose one accepts a persona with
    twelve of its twenty-two fields and the runner then dies at boot."""
    from world.life.genesis import RichGenesis

    parsed = RichGenesis.model_validate(town)
    assert len(parsed.agents) == 50
    for agent in parsed.agents:
        assert len(agent.persona.model_dump()) == 22


def test_no_reference_points_at_somebody_who_is_not_here(town: dict, ids: set[str]) -> None:
    dangling: list[str] = []
    for agent in town["agents"]:
        for field in ("spouse_id", "sponsor_parent_id"):
            value = agent.get(field)
            if value and value not in ids:
                dangling.append(f"{agent['agent_id']}.{field} -> {value}")
        for field in ("parent_ids", "children_ids"):
            for value in agent.get(field) or []:
                if value not in ids:
                    dangling.append(f"{agent['agent_id']}.{field} -> {value}")
    assert not dangling, dangling


def test_nobody_is_married_to_nobody(town: dict) -> None:
    spouses = {a["agent_id"]: a.get("spouse_id") for a in town["agents"]}
    for agent in town["agents"]:
        if agent.get("marital_status") == "married":
            assert agent.get("spouse_id"), f"{agent['agent_id']} is married to nobody"
        if agent.get("spouse_id"):
            assert spouses.get(agent["spouse_id"]) == agent["agent_id"], (
                f"{agent['agent_id']}'s spouse does not name them back"
            )


def test_every_neighbour_an_agent_is_told_about_exists(town: dict, ids: set[str]) -> None:
    """The opinion web is the text a resident is TOLD. A stale id here is a
    person they believe in and can never find."""
    dangling = [
        f"{a['agent_id']} -> {op.get('target_agent_id')}"
        for a in town["agents"]
        for op in (a.get("persona") or {}).get("opinions") or []
        if op.get("target_agent_id") not in ids
    ]
    assert not dangling, dangling
    secrets = [
        f"{a['agent_id']} -> {(a.get('persona') or {}).get('secret_target_agent_id')}"
        for a in town["agents"]
        if (a.get("persona") or {}).get("secret_target_agent_id")
        and (a.get("persona") or {}).get("secret_target_agent_id") not in ids
    ]
    assert not secrets, secrets


def test_every_household_holds_exactly_the_people_who_say_they_live_there(
    town: dict, ids: set[str]
) -> None:
    listed = [m for h in town["households"] for m in h["member_ids"]]
    assert len(listed) == len(set(listed)), "somebody is in two households"
    assert set(listed) == ids, "the households do not account for every resident"
    for household in town["households"]:
        for member in household["member_ids"]:
            agent = next(a for a in town["agents"] if a["agent_id"] == member)
            assert agent["household_id"] == household["household_id"]


def test_every_shop_has_an_owner_who_lives_here(town: dict, ids: set[str]) -> None:
    for business in town["small_businesses"] + [town["construction_company"]]:
        assert business["owner_agent_id"] in ids, (
            f"{business['business_id']} is owned by somebody who is not in the town"
        )
        for employee in business.get("employee_agent_ids") or []:
            assert employee in ids


def test_no_two_businesses_share_an_id(town: dict) -> None:
    """Both source files name a shop `b_grocery`."""
    ids_seen = [b["business_id"] for b in town["small_businesses"]]
    assert len(ids_seen) == len(set(ids_seen)), ids_seen


def test_an_agents_business_is_one_the_town_has(town: dict) -> None:
    known = {b["business_id"] for b in town["small_businesses"]}
    known.add(town["construction_company"]["business_id"])
    for agent in town["agents"]:
        if agent.get("business_id"):
            assert agent["business_id"] in known, (
                f"{agent['agent_id']} works at {agent['business_id']}, which does not exist"
            )


def test_the_thousand_dollars_is_still_a_thousand_dollars(town: dict) -> None:
    ledger = town["usd_ledger"]
    assert ledger["total_cents"] == 100_000
    assert ledger["agent_wallets_cents"] + ledger["treasury_cents"] == 100_000
    assert ledger["agent_wallets_cents"] == 600 * 50


def test_nobody_is_seeded_before_their_own_parent(town: dict) -> None:
    """Run 16 lost Ainsley Holloway to exactly this: `agents.parent_agent_id`
    is a self-referencing foreign key and she came eighteen places before
    her mother. `seed_agent` defers the link now, so this is belt and
    braces - but a cast that does not need the deferral is better than one
    that does."""
    from world.wiring import TraceStore  # noqa: F401  (import proves the fix ships)

    order = {a["agent_id"]: i for i, a in enumerate(town["agents"])}
    late = [
        f"{a['agent_id']} before parent {p}"
        for a in town["agents"]
        for p in a.get("parent_ids") or []
        if order[p] > order[a["agent_id"]]
    ]
    # Reported, not failed: the deferral in seed_agent handles it, and the
    # authored cast deliberately has Hester last.
    assert len(late) <= 2, f"more forward kin references than the fix was built for: {late}"


def test_every_trade_has_somewhere_to_do_it(town: dict) -> None:
    """A brief that names a workplace the map does not draw sends a
    resident somewhere `move` cannot route to - run 19 left the grocery
    clerk in a room no customer could enter.

    EVAL-27. This was red on the review's checkout: the 50-agent cast
    named workplaces the place list did not contain. GROW-29 closed it -
    `resolve_workplace` now resolves the three occupations whose
    workplaces were workshop constants `known_places` does not offer,
    rather than looking them up and finding nothing. Named here so the
    connection survives; a passing test with no history reads as one that
    never mattered.
    """
    import asyncio

    from world.run_genesis import WORKPLACE_BY_OCCUPATION
    from world.tools.bootstrap import build_world
    from world.tools.primitives.places import known_places

    world, _registry = asyncio.run(build_world(genesis_tick=0, seed=1))
    walkable = {p["place_id"] for p in known_places(world)}

    unreachable = sorted({
        WORKPLACE_BY_OCCUPATION[a["occupation"]]
        for a in town["agents"]
        if a["occupation"] in WORKPLACE_BY_OCCUPATION
        and WORKPLACE_BY_OCCUPATION[a["occupation"]] not in walkable
    })
    assert not unreachable, unreachable


def test_the_town_still_has_the_trades_it_depends_on(town: dict) -> None:
    """Fifty residents who are all foresters is not a town."""
    import collections

    occupations = collections.Counter(a["occupation"] for a in town["agents"])
    for trade in ("farmer", "baker", "miller", "trader", "pharmacist", "miner"):
        assert occupations[trade] >= 1, f"nobody in this town is a {trade}"
    assert len(occupations) >= 12, f"only {len(occupations)} trades in fifty people"


# ── the parts a valid file can still get wrong ────────────────────────
#
# Everything above passed on the first build, and the town was still
# broken in two ways that only reading it showed.


def test_nobody_arrives_knowing_nobody(town: dict) -> None:
    """The opinion web is not decoration - it is the entire content of
    "People in Kasukabe you already know, before today" in the persona
    prompt. A resident with an empty web is told nothing about anyone, and
    behaves like a stranger in a town it was born in.

    The first build of this file left 21 of the 30 newcomers with zero
    opinions: their opinions pointed at people who stayed behind in the
    carve, and the repair pass correctly dropped them and put nothing in
    their place.
    """
    thin = [
        f"{a['agent_id']} {a['name']}"
        for a in town["agents"]
        if len((a.get("persona") or {}).get("opinions") or []) < 3
    ]
    assert not thin, f"{len(thin)} residents know fewer than three people: {thin[:8]}"


def test_the_newcomers_know_the_town_they_moved_into(town: dict) -> None:
    """Thirty people who only know each other are a second town sharing a
    map, not fifty residents of one."""
    authored = {f"n{i:02d}" for i in range(1, 10)} | {f"n{i}" for i in range(10, 21)}
    strangers = [
        a["agent_id"]
        for a in town["agents"]
        if a["agent_id"] not in authored
        and not any(
            op.get("target_agent_id") in authored
            for op in (a.get("persona") or {}).get("opinions") or []
        )
    ]
    assert not strangers, (
        f"{len(strangers)} newcomers know nobody from the original cast: {strangers[:8]}"
    )


def test_the_original_cast_knows_the_newcomers_too(town: dict) -> None:
    """A one-way tie is not a relationship. If Marta is the baker the new
    grocer buys from, the grocer knows Marta and Marta knows the grocer."""
    authored = {f"n{i:02d}" for i in range(1, 10)} | {f"n{i}" for i in range(10, 21)}
    newcomers = {a["agent_id"] for a in town["agents"]} - authored
    linked = [
        a["agent_id"]
        for a in town["agents"]
        if a["agent_id"] in authored
        and any(
            op.get("target_agent_id") in newcomers
            for op in (a.get("persona") or {}).get("opinions") or []
        )
    ]
    assert len(linked) >= 6, (
        f"only {len(linked)} of the original twenty have any tie to the thirty who joined"
    )


def test_no_two_shops_answer_to_the_same_name(town: dict) -> None:
    """Both source files have a "Kasukabe Grocery". Two shops with one name
    is a place-folding ambiguity by construction: a resident saying "the
    grocery" names two rooms, and `canonical_destination` declines to guess
    between two candidates - correctly, and the resident goes nowhere."""
    names = [b["name"] for b in town["small_businesses"]]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"two businesses share a name: {duplicates}"


def test_a_reciprocated_tie_tells_the_same_story_from_both_sides(town: dict) -> None:
    """`shared_fact` is what both parties agree happened; `note` is each
    one's own spin. A pair that disagrees about the FACT is two different
    events wearing one relationship.

    Scoped to the ties THIS generator creates. The authored twenty already
    fail it and were not changed to pass: Bram Holloway's opinion of his
    wife reads "Twenty years married. She runs the money and he brings it
    in, and neither has ever said so plainly", while Ainsley's of him is
    the generic "has lived beside Bram Holloway long enough to know their
    habits". Both are hand-written prose about a real marriage, and
    rewriting either to satisfy a symmetry rule would be editing the cast
    to please a test. Recorded here instead, which is what the finding is
    worth.
    """
    generated = {
        "they share the roof",
        "their doors face each other across the lane",
    }
    by_id = {a["agent_id"]: a for a in town["agents"]}
    for agent in town["agents"]:
        for op in (agent.get("persona") or {}).get("opinions") or []:
            other = by_id.get(op.get("target_agent_id"))
            if other is None:
                continue
            back = [
                o for o in (other.get("persona") or {}).get("opinions") or []
                if o.get("target_agent_id") == agent["agent_id"]
            ]
            for mirror in back:
                fact = str(op.get("shared_fact") or "")
                if not any(fact.startswith(g) for g in generated):
                    continue
                if mirror.get("kind") == op.get("kind"):
                    assert mirror.get("shared_fact") == op.get("shared_fact"), (
                        f"{agent['agent_id']} and {other['agent_id']} remember the "
                        f"same {op.get('kind')} differently"
                    )
