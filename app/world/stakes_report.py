"""The evaluation spec's section 6 report additions, computed from events.

Prices, stakes, wages, time, and the task label. Every figure here reads
the WorldEvent stream a completed run produced, and nothing here reads
live world state - evaluation spec section 2: "All read from a completed
trace store ... never from a live world."

That rule is not pedantry. `deni-50-agent-report.md` was regenerated after
the run exited and its conversation section came back empty, because the
generator read a world that had already been torn down. A figure computed
from live state is a figure that can only be taken while the thing being
measured is still running, which is exactly when it is least safe to
stop and take it.

Every function takes a list of event dicts - `{"type": ..., "tick": ...,
"payload": {...}}` - so a caller can hand it the trace store's `events`
table, the runner's own in-memory bus recording, or a fixture. Nothing
here knows what a database is.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from world.core.clock import TICKS_PER_DAY
from world.task_label import label_for_turn


def task_label(
    tools_called: Sequence[str],
    *,
    continuing_tool: str | None = None,
    intention_tool: str | None = None,
) -> str:
    """The evaluation spec's section 4 rules - delegated, not restated.

    EVAL-04. This was a second implementation of the same six rules, with
    its own hard-coded `LABOUR_TOOLS` / `TRADE_TOOLS` / `LOOK_TOOLS`
    literals, and it disagreed with `world/task_label.py` on real turns:
    `["move", "look_around"]` was `look` to one and `idle` to the other,
    as were `["say", "look_around"]` and `["rest"]`.

    `task_label` is the column C1 - "does a task get cheaper as a resident
    gets better at it" - groups by. Two answers to that question is the
    same as none, and the divergence was invisible because both sides had
    tests and neither had a caller that compared them.

    `label_for_turn` wins because it derives its work set from
    `DEFAULT_SCHEDULE` rather than restating it, so a tool added to the
    town is classified without anybody remembering to update a literal.
    Kept as a thin wrapper rather than deleted outright: this name is what
    `label_calls` below and this module's own tests import.
    """
    return label_for_turn(
        tools_called=list(tools_called),
        continuing_tool=continuing_tool,
        intention_tool=intention_tool,
    )


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _of_type(events: Iterable[Mapping[str, Any]], *types: str) -> list[Mapping[str, Any]]:
    wanted = set(types)
    return [event for event in events if event.get("type") in wanted]


def _game_day(event: Mapping[str, Any]) -> int:
    return int(event.get("tick", 0)) // TICKS_PER_DAY


def label_calls(
    calls: Sequence[Mapping[str, Any]],
    invocations: Sequence[Mapping[str, Any]],
    *,
    continuing_by_turn: Mapping[tuple[str, int], str] | None = None,
    intention_by_turn: Mapping[tuple[str, int], str] | None = None,
) -> list[dict[str, Any]]:
    """Attach a `task_label` to every llm_calls row, from the trace.

    NOT A COLUMN THE BROKER WRITES, and the evaluation spec's section 4
    asks for one ("Stored on the `llm_calls` row as `task_label`"). The
    reason it is computed here instead:

    A label is a fact about a TURN, and it is decided by which tools the
    turn ended up calling. At the moment a model call is made - which is
    when the broker writes its row - nobody knows that yet, including the
    resident. A column the broker filled would have to be either a guess
    or a header the engine cannot honestly populate, and a learning-curve
    x-axis that is a guess is not a measurement.

    So it is joined afterwards, by (agent_id, tick), against the
    `tool_invocations` the same turn produced. Deterministic, replayable
    from a finished trace years later, and no new write path. Adding the
    column and leaving nothing to write it would be the same
    built-and-never-invoked failure this round of work exists to end.
    """
    by_turn: dict[tuple[str, int], list[str]] = defaultdict(list)
    for row in invocations:
        key = (str(row.get("agent_id")), int(row.get("tick", 0)))
        by_turn[key].append(str(row.get("tool_name")))

    continuing = continuing_by_turn or {}
    intentions = intention_by_turn or {}

    labelled: list[dict[str, Any]] = []
    for call in calls:
        key = (str(call.get("agent_id")), int(call.get("tick", 0) or 0))
        labelled.append({
            **call,
            "task_label": task_label(
                by_turn.get(key, []),
                continuing_tool=continuing.get(key),
                intention_tool=intentions.get(key),
            ),
        })
    return labelled


@dataclass
class PriceReport:
    """E1 to E3: when the town discovered prices, and whether they converged."""

    first_post_tick: int | None
    posts: int
    withdrawals: int
    asks_by_item_by_day: dict[str, dict[int, list[int]]] = field(default_factory=dict)
    sales_at_a_posted_price: int = 0
    sales_at_a_spoken_price: int = 0

    def spread_by_day(self, item: str) -> dict[int, float]:
        """E2: coefficient of variation of the asks for one item, per day.

        Smith 1962 and Plott & Smith 1978: posted-offer markets converge
        toward a competitive price over repeated periods, more slowly than
        double auctions. So the BAR is direction, not a level, and this
        returns the series rather than a verdict.
        """
        out: dict[int, float] = {}
        for day, asks in sorted((self.asks_by_item_by_day.get(item) or {}).items()):
            if len(asks) < 2:
                continue
            mean = sum(asks) / len(asks)
            if mean == 0:
                continue
            variance = sum((a - mean) ** 2 for a in asks) / len(asks)
            out[day] = (variance ** 0.5) / mean
        return out

    @property
    def posted_share(self) -> float | None:
        """E3. None when nothing was traded at all, which is not zero."""
        total = self.sales_at_a_posted_price + self.sales_at_a_spoken_price
        return (self.sales_at_a_posted_price / total) if total else None


def prices(events: Sequence[Mapping[str, Any]]) -> PriceReport:
    posted = _of_type(events, "economy.price.posted")
    first = min((int(e.get("tick", 0)) for e in posted), default=None)

    by_item: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for event in posted:
        payload = _payload(event)
        ask = payload.get("ask_sim")
        if ask is not None:
            by_item[str(payload.get("item"))][_game_day(event)].append(int(ask))

    at_posted = 0
    spoken = 0
    for event in _of_type(events, "business.sale.completed", "business.purchase.completed"):
        if _payload(event).get("posted_price_sim") is not None:
            at_posted += 1
    spoken = len(_of_type(events, "economy.trade.settled"))

    return PriceReport(
        first_post_tick=first,
        posts=len(posted),
        withdrawals=len(_of_type(events, "economy.price.withdrawn")),
        asks_by_item_by_day={k: dict(v) for k, v in by_item.items()},
        sales_at_a_posted_price=at_posted,
        sales_at_a_spoken_price=spoken,
    )


@dataclass
class StakesReport:
    """S1 to S5: what existing cost, and what anybody did about it."""

    energy_purchases: int
    energy_buyers: set[str]
    sim_converted: int
    dormancies: int
    dormant_agents: set[str]
    revivals: int
    revivals_by_household: int
    revivals_by_stranger: int
    revival_payers: set[str]


def stakes(events: Sequence[Mapping[str, Any]]) -> StakesReport:
    bought = _of_type(events, "economy.energy.bought")
    dormant = _of_type(events, "health.agent.dormant")
    revived = _of_type(events, "health.agent.revived")

    household = sum(1 for e in revived if _payload(e).get("same_household") is True)
    return StakesReport(
        energy_purchases=len(bought),
        energy_buyers={str(_payload(e).get("agent_id")) for e in bought},
        sim_converted=sum(int(_payload(e).get("sim_amount", 0) or 0) for e in bought),
        dormancies=len(dormant),
        dormant_agents={str(_payload(e).get("agent_id")) for e in dormant},
        revivals=len(revived),
        revivals_by_household=household,
        revivals_by_stranger=len(revived) - household,
        revival_payers={str(_payload(e).get("payer_id")) for e in revived},
    )


@dataclass
class WageReport:
    """S6 and S7: promises made, promises kept, and who complained."""

    promises: int
    payments: int
    sim_promised: int
    sim_paid: int
    overdue_days_by_employee: dict[str, int]
    complaints: int

    @property
    def paid_share(self) -> float | None:
        """S6, gated on DIRECTION only in the first full-day run.

        Charness & Dufwenberg 2006 found a bare promise raises
        follow-through to a majority. None when nothing was ever promised,
        which is a different fact from nothing being paid.
        """
        return (self.sim_paid / self.sim_promised) if self.sim_promised else None


def wages(events: Sequence[Mapping[str, Any]]) -> WageReport:
    promised = _of_type(events, "business.wage.promised")
    paid = _of_type(events, "business.wage.paid")
    overdue = _of_type(events, "business.wage.overdue")

    worst: dict[str, int] = {}
    for event in overdue:
        payload = _payload(event)
        employee = str(payload.get("employee_id"))
        days = int(payload.get("days_overdue", 0) or 0)
        worst[employee] = max(worst.get(employee, 0), days)

    return WageReport(
        promises=len(promised),
        payments=len(paid),
        sim_promised=sum(int(_payload(e).get("wage_sim", 0) or 0) for e in promised),
        sim_paid=sum(int(_payload(e).get("amount_sim", 0) or 0) for e in paid),
        overdue_days_by_employee=worst,
        complaints=sum(
            1 for e in _of_type(events, "politics.complaint.filed")
            if _payload(e).get("kind") == "unpaid_wages"
        ),
    )


@dataclass
class TimeReport:
    """T2 to T4: what was done without deciding, and what was decided and
    then not done."""

    continuing_started: int
    continuing_repetitions: int
    wake_reasons: dict[str, int]
    intentions_set: int
    intentions_fulfilled: int
    intentions_expired: int

    @property
    def fulfilled_share(self) -> float | None:
        """T4, and business-readiness figure R4. None when nobody set one."""
        return (self.intentions_fulfilled / self.intentions_set) if self.intentions_set else None


def time_spent(events: Sequence[Mapping[str, Any]]) -> TimeReport:
    started = _of_type(events, "agent.continuing.started")
    repetitions = sum(
        1 for event in events if _payload(event).get("continuing") is True
    )
    ended = _of_type(events, "agent.continuing.ended")
    reasons: dict[str, int] = defaultdict(int)
    for event in ended:
        reasons[str(_payload(event).get("reason", "unknown"))] += 1

    dropped = _of_type(events, "agent.intention.dropped")
    return TimeReport(
        continuing_started=len(started),
        continuing_repetitions=repetitions,
        wake_reasons=dict(reasons),
        intentions_set=len(_of_type(events, "agent.intention.set")),
        intentions_fulfilled=sum(
            1 for e in dropped if _payload(e).get("reason") == "fulfilled"
        ),
        intentions_expired=sum(
            1 for e in dropped if _payload(e).get("reason") == "the day ended"
        ),
    )


@dataclass
class DiffusionReport:
    """C4: whether anything anybody invented reached anybody else."""

    licences: int
    revocations: int
    sales: int
    teachings: int
    inheritances: int
    holders_by_tool: dict[str, set[str]]
    skill_teachings: int = 0
    holders_by_skill: dict[str, set[str]] = field(default_factory=dict)

    @property
    def widest_reach(self) -> int:
        """The largest number of hands one invented thing reached.

        The roadmap's T5 gate is "a tool held by three residents", and
        before conveyance existed this was 1 by construction rather than
        as a finding about the town. Skills count: a procedure a resident
        wrote and taught is something invented here that reached somebody
        else, which is the whole question C4 asks.
        """
        reach = list(self.holders_by_tool.values()) + list(self.holders_by_skill.values())
        return max((len(who) for who in reach), default=0)

    @property
    def anything_moved(self) -> bool:
        """Whether one single thing invented here reached anybody else."""
        return bool(self.licences or self.sales or self.teachings
                    or self.inheritances or self.skill_teachings)


def diffusion(events: Sequence[Mapping[str, Any]]) -> DiffusionReport:
    holders: dict[str, set[str]] = defaultdict(set)
    for event in _of_type(events, "business.tool.licensed"):
        payload = _payload(event)
        holders[str(payload.get("tool_name"))].add(str(payload.get("licensee_id")))
        holders[str(payload.get("tool_name"))].add(str(payload.get("owner_id")))
    for event in _of_type(events, "business.tool.taught"):
        payload = _payload(event)
        holders[str(payload.get("tool_name"))].add(str(payload.get("student_id")))
        holders[str(payload.get("tool_name"))].add(str(payload.get("teacher_id")))
    for event in _of_type(events, "business.tool.sold"):
        payload = _payload(event)
        holders[str(payload.get("tool_name"))].add(str(payload.get("buyer_id")))

    skill_holders: dict[str, set[str]] = defaultdict(set)
    for event in _of_type(events, "agent.skill.taught"):
        payload = _payload(event)
        skill = str(payload.get("skill") or "")
        if not skill:
            continue
        for key in ("teacher_agent_id", "student_agent_id"):
            who = payload.get(key)
            if who:
                skill_holders[skill].add(str(who))

    return DiffusionReport(
        skill_teachings=len(_of_type(events, "agent.skill.taught")),
        holders_by_skill={k: v for k, v in skill_holders.items()},
        licences=len(_of_type(events, "business.tool.licensed")),
        revocations=len(_of_type(events, "business.tool.license_revoked")),
        sales=len(_of_type(events, "business.tool.sold")),
        teachings=len(_of_type(events, "business.tool.taught")),
        inheritances=len(_of_type(events, "business.tools_inherited")),
        holders_by_tool={k: v for k, v in holders.items()},
    )


@dataclass
class LearningReport:
    """C1 and C5: what the town spent per task, and what it was told."""

    tokens: dict[tuple[str, str], dict[int, list[int]]]
    corrections_emitted: int

    def falling_pairs(self) -> tuple[int, int]:
        """C1: how many (resident, label) pairs got cheaper, of those with
        enough turns on both the first and last day to compare.

        Direction only. Wright 1936 says unit cost falls a constant
        fraction per doubling of cumulative output; no rate is claimed
        here, and the evaluation spec gates this ONLY against the
        no-learning arm - a bare fall is a correlation.
        """
        fell = 0
        compared = 0
        for series in self.tokens.values():
            days = sorted(series)
            if len(days) < 2:
                continue
            first, last = series[days[0]], series[days[-1]]
            if len(first) < 5 or len(last) < 5:
                continue
            compared += 1
            if median(last) < median(first):
                fell += 1
        return fell, compared


def learning(
    calls: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]] = (),
) -> LearningReport:
    """`calls` are llm_calls rows carrying agent_id, task_label, tick and
    input tokens - see evaluation spec section 4."""
    tokens: dict[tuple[str, str], dict[int, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for call in calls:
        key = (str(call.get("agent_id")), str(call.get("task_label", "idle")))
        day = int(call.get("tick", 0)) // TICKS_PER_DAY
        tokens[key][day].append(int(call.get("input_tokens", 0) or 0))

    return LearningReport(
        tokens={k: dict(v) for k, v in tokens.items()},
        corrections_emitted=len(_of_type(events, "world.correction.emitted")),
    )
