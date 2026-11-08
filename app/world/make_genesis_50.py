"""genesis_20 grown to fifty, without throwing the cast away.

    uv run python -m world.make_genesis_50

WHY NOT JUST USE genesis_50.json. One already exists, carved out of the
big generated town by `make_genesis_subset`, and it is a perfectly good
fifty-person genesis - with a cast that shares NOT ONE resident with the
twenty this project has been running. Silas Vance's unsold plank, Bram
Holloway's copper seam running thin, Hester Vance being the only living
witness to where the boundary marker truly stood: every finding in
docs/runs/run-19-issues.md is about those people. Replacing them to reach
fifty would make the next run incomparable with every run before it.

So the twenty are kept exactly as they are - same ids, same personas, same
debts and secrets - and thirty more are carved out of the generated town
to join them.

HOW THE THIRTY ARE CHOSEN. Whole households, never fragments, for the
reason `make_genesis_subset` gives: a household is the unit the ledger
bills and the life cycle moves people through, and half a household is
worse than none. Within that, households are taken in the order that fills
the trades the original twenty do not have, largest household first when
the trade is already covered - so the town gains a pharmacy, a cafe and a
construction crew before it gains another forester.

WIRING. Every id, household id and business id from the carve is remapped
into this file's own namespace (n21-n50, h21-h50) so nothing collides with
the authored twenty - the carve has its own `b_grocery`, and so does
genesis_20. Every reference is then repaired against the surviving cast:
spouses, parents, children, sponsors, business owners and employees, and
the opinion web, which is where a dangling id does the most damage because
it is what an agent is told about its neighbours.

The result is validated as a RichGenesis - the shape run_genesis actually
loads, not the looser Genesis that let a thin persona through once before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from world.life.genesis import recount_ablation_summary

HERE = Path(__file__).resolve().parent
AUTHORED = HERE / "genesis_20.json"
CARVE = HERE / "genesis_50.json"
TARGET = HERE / "genesis_50_town.json"

WANTED_TOTAL = 50

TRADES_THE_TOWN_LACKS = (
    "pharmacist",
    "cafe_owner",
    "construction_company_owner",
    "construction_worker",
    "government_clerk",
    "college_instructor",
    "barista",
    "grocery_store_owner",
)


def _households(agents: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for agent in agents:
        out.setdefault(agent["household_id"], []).append(agent)
    return out


def choose_thirty(carve: dict[str, Any], want: int) -> list[dict]:
    """Whole households, best trades first, until `want` residents."""
    households = _households(carve["agents"])

    def value(members: list[dict]) -> tuple[int, int]:
        scarce = sum(1 for m in members if m["occupation"] in TRADES_THE_TOWN_LACKS)
        return (scarce, len(members))

    ordered = sorted(households.items(), key=lambda kv: value(kv[1]), reverse=True)

    kept: list[dict] = []
    for _household_id, members in ordered:
        if len(kept) + len(members) > want:
            continue
        kept.extend(members)
        if len(kept) == want:
            break
    return kept


def remap(kept: list[dict], carve: dict[str, Any]) -> dict[str, Any]:
    """Rename everything into this file's namespace and repair references."""
    old_ids = [a["agent_id"] for a in kept]
    id_of = {old: f"n{21 + i}" for i, old in enumerate(old_ids)}
    survivors = set(id_of)

    household_of: dict[str, str] = {}
    for agent in kept:
        household_of.setdefault(agent["household_id"], f"h{21 + len(household_of)}")

    def ref(old: str | None) -> str | None:
        return id_of.get(old) if old in survivors else None

    agents: list[dict] = []
    for agent in kept:
        new = json.loads(json.dumps(agent))
        new["agent_id"] = id_of[agent["agent_id"]]
        new["household_id"] = household_of[agent["household_id"]]
        new["spouse_id"] = ref(agent.get("spouse_id"))
        if not new["spouse_id"] and new.get("marital_status") == "married":
            new["marital_status"] = "single"
        new["parent_ids"] = [ref(p) for p in agent.get("parent_ids") or []]
        new["parent_ids"] = [p for p in new["parent_ids"] if p]
        new["children_ids"] = [ref(c) for c in agent.get("children_ids") or []]
        new["children_ids"] = [c for c in new["children_ids"] if c]
        new["sponsor_parent_id"] = ref(agent.get("sponsor_parent_id"))

        persona = new.get("persona") or {}
        persona["opinions"] = [
            {**op, "target_agent_id": ref(op.get("target_agent_id"))}
            for op in persona.get("opinions") or []
            if ref(op.get("target_agent_id"))
        ]
        persona["secret_target_agent_id"] = ref(persona.get("secret_target_agent_id"))
        new["persona"] = persona
        agents.append(new)

    households = [
        {
            "household_id": new_id,
            "member_ids": [id_of[a["agent_id"]] for a in kept
                           if household_of[a["household_id"]] == new_id],
            "formed_at_hours": 0.0,
        }
        for new_id in sorted(set(household_of.values()), key=lambda h: int(h[1:]))
    ]

    businesses = []
    for business in carve.get("small_businesses") or []:
        owner = ref(business.get("owner_agent_id"))
        if not owner:
            continue
        businesses.append({
            **business,
            "business_id": f"{business['business_id']}_2",
            "owner_agent_id": owner,
            "employee_agent_ids": [
                e for e in (ref(x) for x in business.get("employee_agent_ids") or []) if e
            ],
        })

    kept_business_ids = {b["business_id"] for b in businesses}
    old_to_new_business = {
        b["business_id"]: f"{b['business_id']}_2" for b in carve.get("small_businesses") or []
    }
    for agent in agents:
        mapped = old_to_new_business.get(agent.get("business_id") or "")
        agent["business_id"] = mapped if mapped in kept_business_ids else None

    return {"agents": agents, "households": households, "small_businesses": businesses}


TRADE_TIES: list[tuple[str, str, str, str]] = [
    ("farmer", "n11", "grain", "grain goes from the field to the Vance mill or it rots"),
    ("miner", "n15", "ore", "ore comes up out of the ground and Ezra Okonjo works it or nobody does"),
    ("forester", "n12", "timber", "felled timber is worth nothing until Silas Vance saws it"),
    ("baker", "n11", "flour", "the ovens run on Vance flour"),
    ("grocery_store_owner", "n07", "bread", "Marta Thorne's bread is what fills the shelves"),
    ("grocery_clerk", "n13", "the counter", "the two groceries in this town watch each other's prices"),
    ("cafe_owner", "n07", "bread", "the cafe buys day-olds from Thorne's bakery"),
    ("barista", "n07", "bread", "the cafe buys day-olds from Thorne's bakery"),
    ("pharmacist", "n16", "remedies", "two pharmacists in one town split the same custom"),
    ("trader", "n02", "everything", "Ainsley Holloway has been trading here longer than anyone"),
    ("artisan", "n05", "the workbench", "Nell Holloway has worked wood here for fifty years"),
    ("construction_worker", "n01", "stone", "the quarry is where a building starts"),
    ("construction_company_owner", "n01", "stone", "the quarry is where a building starts"),
    ("day_laborer", "n06", "a day's work", "Wren Thorne's fields need more hands than the family has"),
    ("government_clerk", "n19", "the records", "Father Anselm keeps the register the clerks work from"),
    ("office_administrator", "n19", "the records", "Father Anselm keeps the register the clerks work from"),
    ("college_instructor", "n10", "the college", "Liora Thorne is enrolled and someone teaches her"),
    ("student", "n10", "the college", "they sit the same lessons"),
    ("child", "n14", "the lane", "children of a small town run in the same lane"),
    ("sawyer", "n12", "the saw", "two sawyers in one town, one saw pit"),
    ("miller", "n11", "the stone", "two millers in one town, and one of the stones is chipped"),
]


def tie_the_town_together(genesis: dict[str, Any]) -> None:
    """Give every newcomer people it already knows, on both sides.

    The carve's own opinion web pointed at residents who stayed behind, and
    the repair pass correctly dropped those - leaving 21 of the 30 knowing
    nobody at all. An empty web is not a small gap: "People in Kasukabe you
    already know, before today" is a section of the persona prompt, and an
    agent with nothing in it is a stranger in the town it was born in.

    Three ties each, all of them true of the world as built:

      * the trade dependency above, reciprocated, so both sides remember
        the same fact and put their own spin on it;
      * their own household, which is who they actually live with;
      * the household next door, so the town has neighbours and not only
        colleagues.
    """
    by_id = {a["agent_id"]: a for a in genesis["agents"]}
    households: dict[str, list[str]] = {}
    for agent in genesis["agents"]:
        households.setdefault(agent["household_id"], []).append(agent["agent_id"])

    def opinions_of(agent_id: str) -> list[dict[str, Any]]:
        return by_id[agent_id]["persona"].setdefault("opinions", [])

    def add(holder: str, target: str, kind: str, role: str, fact: str, note: str) -> None:
        if holder == target or target not in by_id:
            return
        existing = opinions_of(holder)
        if any(o.get("target_agent_id") == target for o in existing):
            return
        existing.append({
            "target_agent_id": target,
            "target_name": by_id[target]["name"],
            "kind": kind,
            "role": role,
            "shared_fact": fact,
            "note": note,
            "sim_amount": None,
        })

    ties = {occupation: (other, goods, fact) for occupation, other, goods, fact in TRADE_TIES}

    for agent in genesis["agents"]:
        agent_id = agent["agent_id"]
        agent.setdefault("persona", {}).setdefault("opinions", [])

        tie = ties.get(agent["occupation"])
        if tie:
            other, goods, fact = tie
            add(agent_id, other, "trade", "buyer",
                fact, f"has not worked out yet what {goods} costs here")
            add(other, agent_id, "trade", "seller",
                fact, f"a new face wanting {goods}; time will tell if they pay")

        for housemate in households.get(agent["household_id"], []):
            add(agent_id, housemate, "kin", "household",
                f"they share the roof at {agent['household_id']}",
                "the person across the table, for better or worse")

    order = sorted(households)
    for index, household_id in enumerate(order):
        neighbour = order[(index + 1) % len(order)]
        if neighbour == household_id:
            continue
        for agent_id in households[household_id][:2]:
            for other_id in households[neighbour][:2]:
                add(agent_id, other_id, "neighbour", "across the street",
                    "their doors face each other across the lane",
                    "known by sight for years, and not much better than that")
                add(other_id, agent_id, "neighbour", "across the street",
                    "their doors face each other across the lane",
                    "known by sight for years, and not much better than that")


def name_the_shops_apart(genesis: dict[str, Any]) -> None:
    """Two businesses with one name is a place nobody can name.

    Both source files carry a "Kasukabe Grocery". `canonical_destination`
    declines to guess between two candidates - correctly - so a resident
    saying "the grocery" goes nowhere at all. Renamed after the family that
    runs it, which is how a town with two of anything actually tells them
    apart.
    """
    by_id = {a["agent_id"]: a for a in genesis["agents"]}
    seen: dict[str, int] = {}
    for business in genesis["small_businesses"]:
        name = business["name"]
        seen[name] = seen.get(name, 0) + 1
        if seen[name] == 1:
            continue
        owner = by_id.get(business["owner_agent_id"])
        surname = (owner["name"].split()[-1] if owner else business["business_id"])
        trade = name.replace("Kasukabe ", "")
        business["name"] = f"{surname}'s {trade}"


def build() -> dict[str, Any]:
    authored = json.loads(AUTHORED.read_text())
    carve = json.loads(CARVE.read_text())

    kept = choose_thirty(carve, WANTED_TOTAL - len(authored["agents"]))
    added = remap(kept, carve)

    genesis = json.loads(json.dumps(authored))
    genesis["agents"].extend(added["agents"])
    genesis["households"].extend(added["households"])
    genesis["small_businesses"].extend(added["small_businesses"])
    genesis["population"] = len(genesis["agents"])

    tie_the_town_together(genesis)
    name_the_shops_apart(genesis)

    wallets = 600 * genesis["population"]
    genesis["usd_ledger"] = {
        "agent_wallets_cents": wallets,
        "treasury_cents": 100_000 - wallets,
        "total_cents": 100_000,
    }
    recount_ablation_summary(genesis)
    return genesis


def main() -> None:
    genesis = build()
    TARGET.write_text(json.dumps(genesis, indent=2) + "\n")
    print(
        f"wrote {TARGET} - {genesis['population']} residents, "
        f"{len(genesis['households'])} households, "
        f"{len(genesis['small_businesses'])} small businesses"
    )


if __name__ == "__main__":
    main()
