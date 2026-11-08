"""Carve a smaller, still-coherent town out of genesis.json.

    uv run python -m world.make_genesis_subset 50          # default purses
    uv run python -m world.make_genesis_subset 50 500      # $5.00 each

A live run against real Bedrock costs money per agent per tick, so the first
real runs are small. Taking "the first N agents" would produce a town that
cannot function: a cafe with no owner, a household whose other half is missing,
a spouse_id pointing at somebody who does not exist. This picks a cast that
still works as a town, and then repairs every reference so the subset is a
valid genesis file in its own right rather than a truncated one.

THE SELECTION POLICY, in priority order. Each step takes WHOLE HOUSEHOLDS,
because the household is the unit the ledger bills, the life-cycle moves people
between, and the viewer ranks wealth by - half a household is worse than none:

  1. Every business owner, and the employees of their business. A business
     whose owner was left behind is a shop that can never open, and an
     employee without an employer is a wage that is never paid.
  2. The services a town cannot do without, one household each while budget
     allows: the pharmacist, a farmer, a government clerk, a trader, an
     artisan, and so on down ESSENTIAL_ROLES. These are the trades the food
     chain and the government seats depend on.

     Note the vocabulary: genesis.json has no doctor, nurse, baker, police
     officer or school teacher - the township viewer assigns those itself
     when it derives its roster. A list of roles that match nobody is a phase
     that silently does nothing, which is exactly what happened first time.
  3. Whoever is left, largest household first, so the remaining budget buys
     the most complete families rather than the most scattered individuals.

Then the repair pass, which is what makes the result loadable:
  - households are rewritten to only their surviving members, and dropped
    when empty;
  - spouse_id / parent_ids / children_ids / sponsor_parent_id are cleared
    when they point outside the cast;
  - marital_status follows spouse_id, so nobody is "married" to nobody;
  - businesses keep only surviving employees, and a small business whose owner
    did not survive is dropped along with its employees' business_id. The
    CONSTRUCTION COMPANY is the exception: RichGenesis requires it and requires
    it to name an owner, so its owner's household is taken first instead;
  - usd_ledger is rebuilt from the survivors and its total re-derived, because
    the $1000 invariant is checked on every ledger move and a stale total
    fails at boot rather than at hour 300. It is three integers
    (total/agent_wallets/treasury), matching UsdLedgerSummary - NOT a
    per-agent map, which is the shape this got wrong first time. total_cents
    is CARRIED OVER, not re-derived: the $1000 is a fixed quantity and the
    departed agents' money returns to the treasury.

`population` is set to the real count. `seed` and `generated_at` are carried
over unchanged so the subset is traceable to the run that produced it.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from world.life.genesis import recount_ablation_summary

GENESIS = Path(__file__).with_name("genesis.json")

ESSENTIAL_ROLES = [
    "pharmacist",
    "farmer",
    "government_clerk",
    "trader",
    "artisan",
    "forester",
    "miner",
    "college_instructor",
    "office_administrator",
]


def household_of(agents: list[dict], hid: str) -> list[dict]:
    return [a for a in agents if a.get("household_id") == hid]


def choose(data: dict[str, Any], want: int) -> list[str]:
    """The agent_ids that make up the subset, by the policy above."""
    agents = data["agents"]
    by_id = {a["agent_id"]: a for a in agents}
    chosen: list[str] = []
    seen_hh: set[str] = set()

    def take_household(hid: str | None, reason: str, ceiling: int | None = None) -> int:
        """Whole household, or nothing. Returns how many were added.

        `ceiling` caps how far THIS phase may fill the cast. Without it the
        first phase spent the whole budget: thirty agents came back with four
        construction workers and no doctor, no nurse and no baker, so the
        emergency rules had nobody to call and the bakery had nobody to bake.
        """
        if not hid or hid in seen_hh:
            return 0
        members = household_of(agents, hid)
        limit = want if ceiling is None else min(want, ceiling)
        if not members or len(chosen) + len(members) > limit:
            return 0
        seen_hh.add(hid)
        for m in members:
            chosen.append(m["agent_id"])
            m.setdefault("_why", reason)
        return len(members)

    BUSINESS_CEILING = max(2, round(want * 0.45))
    ESSENTIAL_CEILING = max(4, round(want * 0.85))

    cc0 = data.get("construction_company") or {}
    if cc0.get("owner_agent_id"):
        cc_owner = by_id.get(cc0["owner_agent_id"])
        if cc_owner:
            take_household(cc_owner.get("household_id"), "construction company owner")

    businesses = list(data.get("small_businesses") or [])
    cc = data.get("construction_company")
    if cc:
        businesses.append({
            "owner_agent_id": cc.get("owner_agent_id"),
            "employee_agent_ids": cc.get("hand_agent_ids") or [],
        })
    for b in businesses:
        owner = by_id.get(b.get("owner_agent_id") or "")
        if owner:
            take_household(owner.get("household_id"), "business owner", BUSINESS_CEILING)
        for eid in (b.get("employee_agent_ids") or []):
            emp = by_id.get(eid)
            if emp:
                take_household(emp.get("household_id"), "employee", BUSINESS_CEILING)

    for role in ESSENTIAL_ROLES:
        if len(chosen) >= want:
            break
        for a in agents:
            if a["agent_id"] in chosen or a.get("occupation") != role:
                continue
            if take_household(a.get("household_id"), f"essential: {role}", ESSENTIAL_CEILING):
                break

    sizes = Counter(a.get("household_id") for a in agents if a.get("household_id"))
    for hid, _n in sizes.most_common():
        if len(chosen) >= want:
            break
        take_household(hid, "household")

    if len(chosen) < want:
        for a in sorted(agents, key=lambda x: -float(x.get("age_years") or 0)):
            if len(chosen) >= want:
                break
            if a["agent_id"] not in chosen:
                chosen.append(a["agent_id"])
                a.setdefault("_why", "filler")
    return chosen[:want]


def repair(data: dict[str, Any], keep: set[str],
           wallet_cents: int | None = None) -> dict[str, Any]:
    """Make the subset a valid genesis file, not a truncated one."""
    agents = [a for a in data["agents"] if a["agent_id"] in keep]
    for a in agents:
        a.pop("_why", None)
        if a.get("spouse_id") not in keep:
            a["spouse_id"] = None
            if a.get("marital_status") == "married":
                a["marital_status"] = "single"
        a["parent_ids"] = [p for p in (a.get("parent_ids") or []) if p in keep]
        a["children_ids"] = [c for c in (a.get("children_ids") or []) if c in keep]
        if a.get("sponsor_parent_id") not in keep:
            a["sponsor_parent_id"] = None

    households = []
    for h in data.get("households") or []:
        members = [m for m in (h.get("member_ids") or []) if m in keep]
        if members:
            households.append({**h, "member_ids": members})
    live_hh = {h["household_id"] for h in households}
    for a in agents:
        if a.get("household_id") not in live_hh:
            a["household_id"] = None

    def keep_business(b: dict) -> dict | None:
        if b.get("owner_agent_id") and b["owner_agent_id"] not in keep:
            return None
        out = dict(b)
        if "employee_agent_ids" in out:
            out["employee_agent_ids"] = [e for e in out["employee_agent_ids"] if e in keep]
        if "hand_agent_ids" in out:
            out["hand_agent_ids"] = [e for e in out["hand_agent_ids"] if e in keep]
        return out

    small = [x for x in (keep_business(b) for b in (data.get("small_businesses") or [])) if x]
    live_biz = {b["business_id"] for b in small}
    cc = dict(data.get("construction_company") or {})
    if cc:
        cc["hand_agent_ids"] = [e for e in (cc.get("hand_agent_ids") or []) if e in keep]
    if cc and cc.get("business_id"):
        live_biz.add(cc["business_id"])
    controls = list(data.get("control_businesses") or [])
    live_biz |= {b["business_id"] for b in controls}
    for a in agents:
        if a.get("business_id") and a["business_id"] not in live_biz:
            a["business_id"] = None

    if wallet_cents is not None:
        if wallet_cents < 0:
            raise ValueError("wallet_cents must not be negative")
        for a in agents:
            a["usd_wallet_cents"] = int(wallet_cents)

    wallets_total = sum(int(a.get("usd_wallet_cents") or 0) for a in agents)
    old = data.get("usd_ledger") or {}
    total = int(old.get("total_cents") or 0)
    if wallets_total > total:
        raise ValueError(
            f"starting purses total {wallets_total} cents across {len(agents)} agents, "
            f"but the town only holds {total} cents (the fixed $1000). "
            f"Maximum per agent here is {total // max(1, len(agents))} cents."
        )
    ledger = {
        "agent_wallets_cents": wallets_total,
        "treasury_cents": total - wallets_total,
        "total_cents": total,
    }

    out = dict(data)
    out.update({
        "agents": agents,
        "households": households,
        "small_businesses": small,
        "control_businesses": controls,
        "usd_ledger": ledger,
        "population": len(agents),
    })
    if cc:
        out["construction_company"] = cc
    recount_ablation_summary(out)
    return out


def build_subset(want: int, wallet_cents: int | None = None) -> dict[str, Any]:
    """The whole pipeline, for callers that want the dict rather than a file."""
    data = json.loads(GENESIS.read_text(encoding="utf-8"))
    chosen = choose(data, want)
    return repair(json.loads(GENESIS.read_text(encoding="utf-8")), set(chosen), wallet_cents)


def main(argv: list[str]) -> int:
    """`python -m world.make_genesis_subset <agents> [wallet_cents] [dest]`.

    UI-14 added the third argument. `game/realsim.py` used to
    `from world.make_genesis_subset import build_subset` and write the
    result into `app/world/` - two crossings of the boundary
    app/CONVENTIONS.md draws, from a module whose whole job is to run the
    world as an EXTERNAL process. It shells out for the runner already;
    now it shells out for the genesis too, and says where the file goes
    rather than reaching into another package's directory to put it there.
    """
    want = int(argv[1]) if len(argv) > 1 else 30
    wallet_cents = int(argv[2]) if len(argv) > 2 and argv[2].strip() else None
    out = build_subset(want, wallet_cents)

    dest = Path(argv[3]) if len(argv) > 3 else GENESIS.with_name(f"genesis_{want}.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")

    roles = Counter(a.get("occupation") for a in out["agents"])
    print(f"wrote {dest.name}: {out['population']} agents, "
          f"{len(out['households'])} households, "
          f"{len(out['small_businesses'])} small businesses")
    print("  roles:", ", ".join(f"{k} x{v}" for k, v in roles.most_common()))
    minors = sum(1 for a in out["agents"] if a.get("is_minor"))
    print(f"  minors: {minors}  married: "
          f"{sum(1 for a in out['agents'] if a.get('spouse_id'))}")
    print(f"  ledger total: {out['usd_ledger']['total_cents']} cents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
