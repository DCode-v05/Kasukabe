"""Turning numbers into plain questions with a confidence on them (§19.7).

The 250 section 19.7, on what to borrow from Simile's own product:

  "every survival-status and household-solvency view above should consider
  adopting the same confidence-tagged, plain-question framing - not just
  'Duval household: 0 days runway' as a bare fact, but a scenario framed
  the way Simile frames one: *'If no relative or the town's charity path
  intervenes in the next 2 days, this household enters hunger - high
  confidence, based on the last 10 days of income data.'* This turns the
  dashboard from a spreadsheet-with-better-fonts into the same kind of
  readable, trustworthy, plain-language product."

So this module exists, rather than the sentences being built in JavaScript,
for one reason: **the confidence label is a claim, and a claim needs a
test.** A dashboard that says "high confidence" on two days of data is
worse than one that shows a bare number, because it invites a decision the
data cannot support. Every label below is derived from how much evidence
there actually is, and that derivation is asserted in
game/console/tests/test_framing.py.

Nothing here fetches anything. It takes the JSON the read-only world APIs
already return (family_api, agents_api, governance_api, skills_api) and
returns the sentence a person should read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# How much evidence each confidence label needs, in days of observed
# history. Chosen so the labels mean what section 19.7's own example means
# by them - its "high confidence" is quoted as resting on "the last 10 days
# of income data", so that is what HIGH costs here.
HIGH_CONFIDENCE_DAYS = 10
MODERATE_CONFIDENCE_DAYS = 5
MEDIUM_CONFIDENCE_DAYS = 2

CONFIDENCE_HIGH = "high confidence"
CONFIDENCE_MODERATE = "moderately high confidence"
CONFIDENCE_MEDIUM = "medium confidence"
CONFIDENCE_LOW = "low confidence"
CONFIDENCE_NONE = "not enough data yet"


def confidence_for(days_observed: int) -> str:
    """The label this much evidence actually earns.

    Deliberately monotonic and boring: an operator has to be able to learn
    what these words mean once and trust them everywhere. There is no
    weighting by how dramatic the finding is - a confident claim about a
    catastrophe and a confident claim about nothing happening need the same
    amount of evidence behind them.
    """
    if days_observed <= 0:
        return CONFIDENCE_NONE
    if days_observed >= HIGH_CONFIDENCE_DAYS:
        return CONFIDENCE_HIGH
    if days_observed >= MODERATE_CONFIDENCE_DAYS:
        return CONFIDENCE_MODERATE
    if days_observed >= MEDIUM_CONFIDENCE_DAYS:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


@dataclass
class Framed:
    """One plain question, answered, with how much to trust the answer."""

    question: str
    answer: str
    confidence: str
    basis: str
    severity: str = "info"          # info | watch | critical
    breakdown: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "confidence": self.confidence,
            "basis": self.basis,
            "severity": self.severity,
            "breakdown": self.breakdown,
        }


def frame_household(household: dict[str, Any], days_observed: int) -> Framed:
    """Section 19.7's own worked example, generalised.

    Not "Duval household: 0 days runway" - "if nobody intervenes in the
    next N days, this household enters hunger."
    """
    runway = int(household.get("days_of_runway", 0))
    hid = household.get("household_id", "this household")
    dependents = int(household.get("dependents", 0))
    earners = int(household.get("earners", 0))
    shape = household.get("shape", "household")

    if runway <= 0:
        answer = (
            f"{hid} cannot cover today's bill. Unless a relative or the town's "
            f"charity path intervenes now, it is already in the escalation path."
        )
        severity = "critical"
    elif runway <= 2:
        answer = (
            f"If no relative or the town's charity path intervenes in the next "
            f"{runway} day{'s' if runway != 1 else ''}, {hid} enters hunger."
        )
        severity = "critical"
    elif runway <= 7:
        answer = (
            f"{hid} has about {runway} days of runway. One lost week of income "
            f"and it is in the escalation path."
        )
        severity = "watch"
    else:
        answer = f"{hid} is covering its own bill, with about {runway} days of runway."
        severity = "info"

    if earners == 0 and dependents > 0:
        answer += f" It has {dependents} dependent{'s' if dependents != 1 else ''} and nobody earning."
        severity = "critical"

    return Framed(
        question=f"Will {hid} make it?",
        answer=answer,
        confidence=confidence_for(days_observed),
        basis=f"based on the last {days_observed} day{'s' if days_observed != 1 else ''} of income data",
        severity=severity,
        breakdown=[
            {"label": "shape", "value": shape},
            {"label": "earners", "value": earners},
            {"label": "dependents", "value": dependents},
            {"label": "combined balance", "value": household.get("combined_sim", 0)},
            {"label": "daily bill", "value": household.get("daily_bill_sim", 0)},
            {"label": "days of runway", "value": runway},
        ],
    )


def frame_business(business: dict[str, Any]) -> Framed:
    """"A failing business is something the town can see coming" (§23)."""
    name = business.get("name", business.get("business_id", "this business"))
    trend = business.get("trend", "unknown")
    profit = int(business.get("profit_sim", 0))
    covered = int(business.get("days_of_wages_covered", 0))
    days = len(business.get("by_day", []) or [])

    if trend == "insolvent":
        answer = f"{name} has nothing left in the till. It is finished unless someone puts money in."
        severity = "critical"
    elif trend == "cannot make payroll":
        answer = f"{name} cannot cover its next wage bill. Its employees are about to be unpaid."
        severity = "critical"
    elif trend == "declining":
        answer = f"{name} is losing ground - its last day was worse than the one before."
        severity = "watch"
    elif trend == "improving":
        answer = f"{name} is recovering - its last day was better than the one before."
        severity = "info"
    else:
        answer = f"{name} has not traded long enough to show a direction yet."
        severity = "info"

    return Framed(
        question=f"Is {name} going to survive?",
        answer=answer,
        confidence=confidence_for(days),
        basis=f"based on {days} day{'s' if days != 1 else ''} of its books",
        severity=severity,
        breakdown=[
            {"label": "profit so far", "value": profit},
            {"label": "balance", "value": business.get("balance_sim", 0)},
            {"label": "wages paid", "value": business.get("wages_paid_sim", 0)},
            {"label": "days of wages covered", "value": covered},
            {"label": "employees", "value": business.get("employees", 0)},
        ],
    )


def frame_treasury(treasury: dict[str, Any], policy: dict[str, Any], days_observed: int) -> Framed:
    """"So a policy choice has a traceable financial consequence" (§23)."""
    balance = int(treasury.get("balance_sim", 0))
    tax_in = int(treasury.get("tax_collected_sim", 0))
    out = int(treasury.get("wages_paid_sim", 0)) + int(treasury.get("pensions_paid_sim", 0))
    net = tax_in - out
    rate = policy.get("tax_rate_bps", 0)

    if balance <= 0:
        answer = "The town is broke. It cannot pay a wage or a pension until money comes in."
        severity = "critical"
    elif net < 0 and out > 0:
        # Only meaningful once there is spending to compare against.
        runway = balance // max(1, abs(net) // max(1, days_observed or 1))
        answer = (
            f"The town is spending more than it collects. At this rate the treasury "
            f"runs dry in about {runway} day{'s' if runway != 1 else ''}."
        )
        severity = "watch"
    else:
        answer = f"The town is collecting more than it spends, at a tax rate of {rate / 100:.1f}%."
        severity = "info"

    return Framed(
        question="Can the town pay for itself?",
        answer=answer,
        confidence=confidence_for(days_observed),
        basis=f"based on {days_observed} day{'s' if days_observed != 1 else ''} of treasury movement",
        severity=severity,
        breakdown=[
            {"label": "balance", "value": balance},
            {"label": "tax collected", "value": tax_in},
            {"label": "wages paid", "value": treasury.get("wages_paid_sim", 0)},
            {"label": "pensions paid", "value": treasury.get("pensions_paid_sim", 0)},
            {"label": "net", "value": net},
        ],
    )


def frame_skill_spread(spreads: list[dict[str, Any]]) -> Framed:
    """"Why did half the town become farmers and the other half traders"
    becomes an answerable, watchable question (§22)."""
    if not spreads:
        return Framed(
            question="What is this town good at?",
            answer="Nobody has picked up a trade yet.",
            confidence=CONFIDENCE_NONE,
            basis="no skills recorded",
        )
    top = spreads[0]
    named = ", ".join(
        f"{s['skill']} ({s['practitioners']})" for s in spreads[:3]
    )
    best = top.get("best") or {}
    answer = (
        f"{top['skill']} is the town's commonest trade, with {top['practitioners']} "
        f"practising it; {best.get('name', 'nobody')} is the best at it."
    )
    return Framed(
        question="What is this town good at?",
        answer=answer,
        confidence=confidence_for(len(spreads) * 2),
        basis=f"based on {sum(s['practitioners'] for s in spreads)} recorded proficiencies",
        breakdown=[{"label": "top trades", "value": named}],
    )


def frame_at_risk(households: list[dict[str, Any]], days_observed: int) -> Framed:
    """The watchlist, as one sentence an operator can act on."""
    critical = [h for h in households if int(h.get("days_of_runway", 0)) <= 2]
    if not households:
        return Framed(
            question="Is anyone about to go hungry?",
            answer="No households are being tracked yet.",
            confidence=CONFIDENCE_NONE,
            basis="no household data",
        )
    if not critical:
        answer = "No household is within two days of hunger right now."
        severity = "info"
    else:
        answer = (
            f"{len(critical)} household{'s are' if len(critical) != 1 else ' is'} "
            f"within two days of hunger."
        )
        severity = "critical"
    return Framed(
        question="Is anyone about to go hungry?",
        answer=answer,
        confidence=confidence_for(days_observed),
        basis=f"based on {len(households)} households over {days_observed} day"
              f"{'s' if days_observed != 1 else ''}",
        severity=severity,
        breakdown=[
            {"label": h.get("household_id", "?"), "value": f"{h.get('days_of_runway', 0)} days"}
            for h in households[:8]
        ],
    )
