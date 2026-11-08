"""Read-only Family / survival viewer API (The 250 sections 19.1-19.6).

"This is the direct answer to 'understand it like a real world to see who
is surviving.' Everything below reads from data the trace store already
has - this is a plan for *views*, not new data collection."

Six views, one per subsection of section 19:

  19.1 `/family/agents`             per-agent survival status
  19.2 `/family/households`         per-household solvency
  19.3 `/family/at-risk`            the watchlist, sorted by runway ascending
  19.4 `/family/wealth`             the wealth ticker / leaderboard
  19.5 `/family/tree/{agent_id}`    a family tree with live balances on every node
  19.6 `/family/money-flow`         where sim-currency actually moved

Every route is a plain GET with no token, for the reason governance_api.py
states and DESIGN.md section 8 draws itself: "they are windows, never
authorities. Nothing an operator views may mutate the world." A window
does not need a key. Nothing in this module writes - not to the ledger,
not to WorldState, not to the trace store.

Two deliberate choices worth stating up front:

**Runway is computed, not stored.** Section 19.3 sorts households "by days
of runway, ascending - the households closest to the escalation path in
section 5.3 float to the top automatically." Runway is balance divided by
the household's own daily bill, and that bill already has one correct
implementation in `world.life.upkeep.compute_household_bill`. This module
calls it rather than re-deriving the arithmetic, so a change to the upkeep
constants moves the watchlist too instead of silently disagreeing with it.

**Money flow reads transactions, not events.** Section 19.6 wants "the
same $1000-ledger invariant DESIGN.md already enforces, just watchable" -
so it aggregates the ledger's own `Transaction` log, which is the record
that invariant is checked against, rather than the event bus, which
carries plenty of things that never moved a coin.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from world.ledger.models import AccountKind, Currency, TransactionReason
from world.life.models import AgentLifeRecord, Genesis
from world.life.upkeep import (
    UPKEEP_TOTAL_SIM_PER_DAY,
    compute_household_bill,
    is_dependent,
)
from world.tools.state import WorldState

RUNWAY_CAP_DAYS = 999


@dataclass
class FamilyDeps:
    """Everything a handler below needs. Same shape as GovernanceDeps in
    governance_api.py - `genesis` is read only for display names, since
    life/'s AgentLifeRecord deliberately carries none."""

    world: WorldState
    genesis: Genesis


def name_map(genesis: Genesis) -> dict[str, str]:
    return {agent.agent_id: agent.name for agent in genesis.agents}


class AgentSurvival(BaseModel):
    """19.1 - per-agent survival status."""

    agent_id: str
    name: str
    alive: bool
    age_years: float
    is_dependent: bool
    household_id: str | None
    sim_balance: int
    usd_cents: int
    ailment: str | None
    days_of_runway: int
    status: str


class HouseholdSolvency(BaseModel):
    """19.2 - the unit that matters more than any one individual."""

    household_id: str
    member_ids: list[str]
    shape: str
    earners: int
    dependents: int
    combined_sim: int
    daily_bill_sim: int
    days_of_runway: int
    status: str


class WealthEntry(BaseModel):
    subject_id: str
    name: str
    amount_sim: int


class WealthBoard(BaseModel):
    """19.4 - richest, poorest, biggest giver, biggest saver."""

    richest: list[WealthEntry]
    poorest: list[WealthEntry]
    biggest_givers: list[WealthEntry]
    biggest_earners: list[WealthEntry]


class TreeNode(BaseModel):
    """19.5 - a family tree node, with the live balance on it."""

    agent_id: str
    name: str
    relation: str
    alive: bool
    sim_balance: int
    days_of_runway: int


class FamilyTree(BaseModel):
    root: TreeNode
    parents: list[TreeNode]
    spouse: TreeNode | None
    children: list[TreeNode]
    siblings: list[TreeNode]


class FlowEdge(BaseModel):
    reason: str
    total_sim: int
    transfers: int


class MoneyFlow(BaseModel):
    """19.6 - where sim-currency is actually moving."""

    total_moved_sim: int
    transactions: int
    by_reason: list[FlowEdge]
    into_treasury_sim: int
    out_of_treasury_sim: int
    between_agents_sim: int


def runway_days(balance_sim: int, daily_bill_sim: int) -> int:
    """How many days this balance covers this bill.

    A zero bill means nothing is being consumed - that is the cap, not
    infinity (see RUNWAY_CAP_DAYS). A balance at or below zero is zero
    days: section 5.3's escalation has already started.
    """
    if balance_sim <= 0:
        return 0
    if daily_bill_sim <= 0:
        return RUNWAY_CAP_DAYS
    return min(RUNWAY_CAP_DAYS, balance_sim // daily_bill_sim)


def status_for(runway: int, alive: bool = True) -> str:
    """The plain-language label section 19.7 asks for instead of a bare
    number. Thresholds are in days of runway, which is the unit the
    escalation ladder in section 5.3 actually runs on."""
    if not alive:
        return "deceased"
    if runway <= 0:
        return "destitute"
    if runway <= 2:
        return "critical"
    if runway <= 7:
        return "at risk"
    if runway <= 30:
        return "stable"
    return "comfortable"


def household_shape(earners: int, dependents: int) -> str:
    """Section 4's archetypes, named from the composition rather than
    stored - the household record carries members, not a label."""
    total = earners + dependents
    if total == 0:
        return "empty"
    if total == 1:
        return "single, no dependents" if earners else "lone dependent"
    if dependents == 0:
        return "dual-earner, no dependents" if earners == 2 else "adults sharing"
    if earners == 1:
        return "single-earner with dependents"
    return "dual-earner with dependents"


def sim_balance_of(world: WorldState, account_id: str) -> int:
    """The live sim balance, or zero for an id the ledger never opened.

    A household member with no ledger account is not an error worth a 500 -
    genesis opens accounts for agents, and a view must not fall over on a
    resident who arrived some other way (a birth, mid-run).
    """
    account = world.ledger.accounts.get(account_id)
    return account.sim_units if account is not None else 0


def usd_cents_of(world: WorldState, account_id: str) -> int:
    account = world.ledger.accounts.get(account_id)
    return account.usd_cents if account is not None else 0


def agent_survival(world: WorldState, names: dict[str, str], agent: AgentLifeRecord) -> AgentSurvival:
    balance = sim_balance_of(world, agent.agent_id)
    runway = runway_days(balance, UPKEEP_TOTAL_SIM_PER_DAY)
    ailment = agent.health.current_ailment
    return AgentSurvival(
        agent_id=agent.agent_id,
        name=names.get(agent.agent_id, agent.agent_id),
        alive=agent.alive,
        age_years=agent.age_years,
        is_dependent=is_dependent(agent),
        household_id=agent.household_id,
        sim_balance=balance,
        usd_cents=usd_cents_of(world, agent.agent_id),
        ailment=ailment.kind.value if ailment is not None else None,
        days_of_runway=runway,
        status=status_for(runway, agent.alive),
    )


def household_solvency(world: WorldState, household_id: str, day: int) -> HouseholdSolvency:
    household = world.households.get(household_id)
    member_ids = list(household.member_ids) if household is not None else []
    members = [world.agents[m] for m in member_ids if m in world.agents]
    bill = compute_household_bill(household_id, day, members)
    combined = sum(sim_balance_of(world, m) for m in bill.earner_ids + bill.dependent_ids)
    runway = runway_days(combined, bill.total_sim)
    return HouseholdSolvency(
        household_id=household_id,
        member_ids=member_ids,
        shape=household_shape(len(bill.earner_ids), len(bill.dependent_ids)),
        earners=len(bill.earner_ids),
        dependents=len(bill.dependent_ids),
        combined_sim=combined,
        daily_bill_sim=bill.total_sim,
        days_of_runway=runway,
        status=status_for(runway),
    )


def tree_node(world: WorldState, names: dict[str, str], agent_id: str, relation: str) -> TreeNode | None:
    agent = world.agents.get(agent_id)
    if agent is None:
        return None
    balance = sim_balance_of(world, agent_id)
    return TreeNode(
        agent_id=agent_id,
        name=names.get(agent_id, agent_id),
        relation=relation,
        alive=agent.alive,
        sim_balance=balance,
        days_of_runway=runway_days(balance, UPKEEP_TOTAL_SIM_PER_DAY),
    )


def money_flow(world: WorldState, since_tick: int = 0) -> MoneyFlow:
    """Aggregate the ledger's own transaction log from `since_tick` on."""
    from world.ledger.money import TREASURY_ACCOUNT_ID

    rows = [t for t in world.ledger.transactions if t.tick >= since_tick and t.currency is Currency.SIM]
    by_reason: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    into_treasury = out_of_treasury = between_agents = 0
    for t in rows:
        bucket = by_reason[t.reason.value if isinstance(t.reason, TransactionReason) else str(t.reason)]
        bucket[0] += t.amount
        bucket[1] += 1
        if t.to_account == TREASURY_ACCOUNT_ID:
            into_treasury += t.amount
        elif t.from_account == TREASURY_ACCOUNT_ID:
            out_of_treasury += t.amount
        else:
            between_agents += t.amount
    return MoneyFlow(
        total_moved_sim=sum(t.amount for t in rows),
        transactions=len(rows),
        by_reason=sorted(
            (FlowEdge(reason=r, total_sim=v[0], transfers=v[1]) for r, v in by_reason.items()),
            key=lambda e: e.total_sim,
            reverse=True,
        ),
        into_treasury_sim=into_treasury,
        out_of_treasury_sim=out_of_treasury,
        between_agents_sim=between_agents,
    )


async def add_cors_headers(response: Response) -> None:
    """Same permissive read-only CORS as governance_api.py - the static
    viewer pages are served from a different origin (DESIGN.md section 9)."""
    response.headers["Access-Control-Allow-Origin"] = "*"


def build_family_router(get_deps: Callable[[], FamilyDeps]) -> APIRouter:
    router = APIRouter(tags=["family"], dependencies=[Depends(add_cors_headers)])

    def current_day(world: WorldState) -> int:
        return int(world.game_clock().total_hours // 24)


    @router.get("/family/agents", response_model=list[AgentSurvival])
    async def agents() -> list[AgentSurvival]:
        deps = get_deps()
        names = name_map(deps.genesis)
        return [agent_survival(deps.world, names, a) for a in deps.world.agents.values()]


    @router.get("/family/households", response_model=list[HouseholdSolvency])
    async def households() -> list[HouseholdSolvency]:
        deps = get_deps()
        day = current_day(deps.world)
        return [household_solvency(deps.world, hid, day) for hid in deps.world.households]


    @router.get("/family/at-risk", response_model=list[HouseholdSolvency])
    async def at_risk(limit: int = 20) -> list[HouseholdSolvency]:
        """"Sorted by days of runway, ascending - the households closest to
        the escalation path float to the top automatically"."""
        deps = get_deps()
        day = current_day(deps.world)
        rows = [household_solvency(deps.world, hid, day) for hid in deps.world.households]
        rows.sort(key=lambda h: (h.days_of_runway, -h.dependents))
        return rows[: max(1, limit)]


    @router.get("/family/wealth", response_model=WealthBoard)
    async def wealth(top: int = 5) -> WealthBoard:
        from world.ledger.money import TREASURY_ACCOUNT_ID

        deps = get_deps()
        names = name_map(deps.genesis)
        holders = [
            WealthEntry(subject_id=aid, name=names.get(aid, aid), amount_sim=acct.sim_units)
            for aid, acct in deps.world.ledger.accounts.items()
            if acct.kind is AccountKind.AGENT and not acct.closed
        ]
        holders.sort(key=lambda e: e.amount_sim, reverse=True)

        given: dict[str, int] = defaultdict(int)
        earned: dict[str, int] = defaultdict(int)
        for t in deps.world.ledger.transactions:
            if t.currency is not Currency.SIM:
                continue
            if t.from_account != TREASURY_ACCOUNT_ID:
                given[t.from_account] += t.amount
            if t.to_account != TREASURY_ACCOUNT_ID:
                earned[t.to_account] += t.amount

        def board(totals: dict[str, int]) -> list[WealthEntry]:
            rows = [
                WealthEntry(subject_id=k, name=names.get(k, k), amount_sim=v)
                for k, v in totals.items()
                if k in deps.world.agents
            ]
            rows.sort(key=lambda e: e.amount_sim, reverse=True)
            return rows[: max(1, top)]

        return WealthBoard(
            richest=holders[: max(1, top)],
            poorest=list(reversed(holders[-max(1, top) :])) if holders else [],
            biggest_givers=board(given),
            biggest_earners=board(earned),
        )


    @router.get("/family/tree/{agent_id}", response_model=FamilyTree)
    async def tree(agent_id: str) -> FamilyTree:
        deps = get_deps()
        names = name_map(deps.genesis)
        agent = deps.world.agents.get(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")
        root = tree_node(deps.world, names, agent_id, "self")
        assert root is not None
        parents = [n for p in agent.parent_ids if (n := tree_node(deps.world, names, p, "parent"))]
        children = [n for c in agent.children_ids if (n := tree_node(deps.world, names, c, "child"))]
        spouse = tree_node(deps.world, names, agent.spouse_id, "spouse") if agent.spouse_id else None
        sibling_ids = {
            other.agent_id
            for other in deps.world.agents.values()
            if other.agent_id != agent_id and set(other.parent_ids) & set(agent.parent_ids)
        }
        siblings = [n for s in sorted(sibling_ids) if (n := tree_node(deps.world, names, s, "sibling"))]
        return FamilyTree(root=root, parents=parents, spouse=spouse, children=children, siblings=siblings)


    @router.get("/family/money-flow", response_model=MoneyFlow)
    async def flow(since_tick: int = 0) -> MoneyFlow:
        return money_flow(get_deps().world, since_tick=since_tick)

    return router
