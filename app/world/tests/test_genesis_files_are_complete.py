"""Every shipped genesis file must satisfy the strict shape, not the loose one.

This exists because of a specific failure. `Genesis` parses a genesis
file with a twelve-key persona quite happily - `GenesisAgent.persona` is
an untyped dict, so nothing complains. `RichGenesis` is the shape the
run actually loads, and it wants twenty-two keys. So `genesis_20.json`
validated in a check, was written to disk, and then killed twenty
engines before the first tick with a wall of pydantic `Field required`.

A file that parses under the permissive model and explodes under the
real one is the exact "looks finished" failure this suite is for, so
these tests use the strict model and assert the social web on top of it:
a resident nobody has an opinion about is a stranger the town cannot
talk about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from world.life.genesis import RichGenesis

GENESIS_DIR = Path(__file__).resolve().parent.parent
SHIPPED = sorted(GENESIS_DIR.glob("genesis_*.json"))

# The hand-authored casts, which are the ones runs are launched against.
# Everything in this module applies to these.
CURATED = [p for p in SHIPPED if p.name in {"genesis_10.json", "genesis_20.json"}]

# The rest are older files, most of them sliced out of a 250-resident
# generation without pruning what the slice cut. Writing these tests
# turned up three real defects in them, recorded here rather than
# deleted, because a scoped-out failure that nobody wrote down is a
# failure nobody fixes:
#
#   * dangling opinion targets - genesis_30 holds 51 opinions about
#     residents who are not in the file, genesis_realsim_10 holds 13.
#     Those agents are briefed on neighbours the town does not contain.
#   * genesis_14's usd_ledger totals 52,800 cents, not the 100,000 the
#     $1,000 invariant requires.
#   * genesis_250_backup has a resident (a0027) with an empty opinion
#     list - nobody, in a town of 250.
#
# Fixing them means regenerating those files, which is its own change
# with its own runs behind it. What is asserted below for every file is
# the thing that actually stops a run booting: the strict shape.


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_there_are_genesis_files_to_check() -> None:
    """Guards the guard: a glob that silently matches nothing would make
    every parametrised test below vacuously green, and a curated list
    that stops matching its files would quietly check nothing at all."""
    assert SHIPPED, f"no genesis_*.json under {GENESIS_DIR}"
    assert len(CURATED) == 2, f"expected both curated casts, found {[p.name for p in CURATED]}"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_genesis_file_validates_against_the_shape_the_run_loads(path: Path) -> None:
    RichGenesis.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", CURATED, ids=lambda p: p.name)
def test_no_persona_field_is_blank(path: Path) -> None:
    """`RichGenesis` accepts an empty string for every str field, so
    validation alone cannot tell a written persona from a defaulted one."""
    blank: list[str] = []
    for agent in _load(path)["agents"]:
        for key, value in agent["persona"].items():
            if key == "secret_target_agent_id":
                continue  # legitimately None for a secret about nobody
            if isinstance(value, str) and not value.strip():
                blank.append(f"{agent['agent_id']}.{key}")
            if isinstance(value, list) and not value:
                blank.append(f"{agent['agent_id']}.{key}")
    assert not blank, f"{path.name} has empty persona fields: {blank}"


@pytest.mark.parametrize("path", CURATED, ids=lambda p: p.name)
def test_every_resident_is_known_to_the_town(path: Path) -> None:
    """Nine opinions apiece is what the founding cast carries. A resident
    below that is thinner than their neighbours; a resident nobody holds
    an opinion *about* cannot be gossiped about, which is most of what
    this simulation is for."""
    agents = _load(path)["agents"]
    holds = {a["agent_id"]: len(a["persona"]["opinions"]) for a in agents}
    held_about: dict[str, int] = {a["agent_id"]: 0 for a in agents}
    for agent in agents:
        for opinion in agent["persona"]["opinions"]:
            held_about[opinion["target_agent_id"]] += 1

    thin = {k: v for k, v in holds.items() if v < 9}
    assert not thin, f"{path.name}: residents holding fewer than 9 opinions: {thin}"
    unknown = {k: v for k, v in held_about.items() if v < 3}
    assert not unknown, f"{path.name}: residents almost nobody has an opinion of: {unknown}"


@pytest.mark.parametrize("path", CURATED, ids=lambda p: p.name)
def test_opinions_point_at_real_residents_under_their_real_names(path: Path) -> None:
    """A `target_name` that disagrees with the roster puts a name in an
    agent's prompt that no other agent answers to."""
    agents = _load(path)["agents"]
    names = {a["agent_id"]: a["name"] for a in agents}
    wrong = [
        f"{a['agent_id']}->{o['target_agent_id']}({o['target_name']})"
        for a in agents
        for o in a["persona"]["opinions"]
        if names.get(o["target_agent_id"]) != o["target_name"]
    ]
    assert not wrong, f"{path.name}: opinions naming someone who is not there: {wrong}"

    ids = {a["agent_id"] for a in agents}
    dangling = sorted(
        {o["target_agent_id"] for a in agents for o in a["persona"]["opinions"]} - ids
    )
    assert not dangling, f"{path.name}: opinions about non-residents: {dangling}"

    selves = [
        a["agent_id"]
        for a in agents
        for o in a["persona"]["opinions"]
        if o["target_agent_id"] == a["agent_id"]
    ]
    assert not selves, f"{path.name}: residents holding opinions of themselves: {selves}"


@pytest.mark.parametrize("path", CURATED, ids=lambda p: p.name)
def test_relationships_are_held_at_both_ends(path: Path) -> None:
    """If Rosalind knows Marta and Marta has never heard of Rosalind, the
    two agents are running on different maps of the same town."""
    agents = _load(path)["agents"]
    held = {
        (a["agent_id"], o["target_agent_id"])
        for a in agents
        for o in a["persona"]["opinions"]
    }
    one_way = sorted((x, y) for x, y in held if (y, x) not in held)
    assert not one_way, f"{path.name}: ties only one side knows about: {one_way}"


@pytest.mark.parametrize("path", CURATED, ids=lambda p: p.name)
def test_kin_and_money_survive_the_merge(path: Path) -> None:
    """Extending a cast is where kin graphs get cut: a spouse pointer to
    a deleted id, a household that forgot a member, a wallet split that
    stops summing to the thousand dollars the ledger promises."""
    data = _load(path)
    agents = data["agents"]
    ids = {a["agent_id"] for a in agents}

    for agent in agents:
        for field in ("parent_ids", "children_ids"):
            for other in agent.get(field) or []:
                assert other in ids, f"{agent['agent_id']}.{field} -> missing {other}"
        spouse = agent.get("spouse_id")
        if spouse is not None:
            assert spouse in ids, f"{agent['agent_id']} married to missing {spouse}"
            partner = next(a for a in agents if a["agent_id"] == spouse)
            assert partner["spouse_id"] == agent["agent_id"], (
                f"{agent['agent_id']} claims {spouse}, who claims "
                f"{partner['spouse_id']}"
            )

    members = [m for h in data["households"] for m in h["member_ids"]]
    assert len(members) == len(set(members)), f"{path.name}: someone lives in two houses"
    assert set(members) == ids, (
        f"{path.name}: residents with no household: {sorted(ids - set(members))}"
    )

    ledger = data["usd_ledger"]
    assert ledger["agent_wallets_cents"] + ledger["treasury_cents"] == ledger["total_cents"]
    assert ledger["total_cents"] == 100_000, "the $1,000 invariant"


@pytest.mark.parametrize("path", CURATED, ids=lambda p: p.name)
def test_every_occupation_has_somewhere_to_do_it(path: Path) -> None:
    """An occupation with no workplace gives its holder a standing brief
    that names no destination - which is how a miller spends a run
    looking for a mill the map never told her about."""
    from world.run_genesis import WORKPLACE_BY_OCCUPATION

    homeless = sorted(
        {
            a["occupation"]
            for a in _load(path)["agents"]
            if a["occupation"] not in WORKPLACE_BY_OCCUPATION and not a.get("is_minor")
        }
        - {"child", "student"}
    )
    assert not homeless, f"{path.name}: occupations with no workplace: {homeless}"
