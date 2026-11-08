"""Measuring the town as a population, not one agent at a time (§24).

The 250 section 24, on the two Park et al. papers:

  "The finding that matters for Kasukabe: **rich, individual personas (not
  demographic labels) are what make a population of agents behave
  realistically as a group, not just convincingly as individuals.**...
  What this adds to the plan: measure the 250 not only one at a time ('did
  Priya act like Priya') but **as a population, and as families**."

Three things, matching that section's three bullets:

  1. `slice_population()` - break the town down by age band, sex,
     occupation, wealth bracket.
  2. `bias_report()` - "check the town's *aggregate* patterns are plausible
     and not skewed toward one group - the same bias check these papers
     run."
  3. `household_outcomes()` - "break down survival outcomes by household
     shape... and check whether the pattern that emerges is a plausible
     economic story, not an artifact of how the genesis generator happened
     to assign occupations."

Read-only, like everything else in `research/` (see this package's own
docstring). Pure functions over records the caller has already fetched -
no I/O here at all, so the same code measures a live `WorldState`, a
Parquet export, or a fixture in a test without three implementations.

**On what "bias" means here, precisely.** It is not a moral judgement and
this module makes none. It is the measurable thing the papers measure: does
an outcome depend on a demographic slice more than the town's own
mechanics can account for? A town where the elderly are poorer is not
biased - DESIGN.md pensions them at a fraction of a wage, so that is the
design working. A town where women are poorer, with no mechanic that
touches sex anywhere in the codebase, is a finding: something in genesis or
in the personas is doing it. `bias_report` therefore reports the disparity
and names which slices are EXPECTED to differ, rather than pretending a
number alone can tell you which is which.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

AGE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("minor", 0.0, 18.0),
    ("young adult", 18.0, 30.0),
    ("adult", 30.0, 45.0),
    ("older adult", 45.0, 70.0),
    ("elderly", 70.0, 999.0),
)

MECHANICALLY_EXPECTED = frozenset({"age_band", "is_dependent", "household_shape"})

DISPARITY_THRESHOLD = 1.5
MIN_SLICE_SIZE = 5


def age_band(age_years: float) -> str:
    for name, low, high in AGE_BANDS:
        if low <= age_years < high:
            return name
    return "elderly"


def wealth_bracket(balance: int, all_balances: Sequence[int]) -> str:
    """Which quarter of the town this balance sits in.

    Quartiles rather than fixed thresholds: the sim's currency has no
    absolute meaning, and a fixed "rich = 10,000" would stop being true the
    moment the economy inflates or the genesis balance changes.
    """
    if not all_balances:
        return "unknown"
    ordered = sorted(all_balances)
    q1 = ordered[len(ordered) // 4]
    q2 = ordered[len(ordered) // 2]
    q3 = ordered[(3 * len(ordered)) // 4]
    if balance <= q1:
        return "poorest quarter"
    if balance <= q2:
        return "lower middle"
    if balance <= q3:
        return "upper middle"
    return "richest quarter"


@dataclass
class Person:
    """The slice of one resident this module measures.

    A flat record on purpose: the caller assembles it from whatever it has
    (a live WorldState, a Parquet row, a fixture), so this module never
    needs to know which.
    """

    agent_id: str
    age_years: float
    sex: str
    occupation: str
    household_id: str | None
    balance_sim: int
    alive: bool = True
    is_dependent: bool = False
    household_shape: str = "unknown"
    extra: dict[str, Any] = field(default_factory=dict)

    def slice_value(self, dimension: str, all_balances: Sequence[int]) -> str:
        if dimension == "age_band":
            return age_band(self.age_years)
        if dimension == "sex":
            return self.sex
        if dimension == "occupation":
            return self.occupation
        if dimension == "wealth_bracket":
            return wealth_bracket(self.balance_sim, all_balances)
        if dimension == "household_shape":
            return self.household_shape
        if dimension == "is_dependent":
            return "dependent" if self.is_dependent else "earner"
        return str(self.extra.get(dimension, "unknown"))


@dataclass
class SliceStat:
    """One demographic slice, and how it is doing."""

    dimension: str
    value: str
    count: int
    share_of_town: float
    mean_balance: float
    median_balance: float
    alive_rate: float


@dataclass
class Disparity:
    """Two slices of one dimension, and how far apart their outcomes are."""

    dimension: str
    best_value: str
    worst_value: str
    best_mean: float
    worst_mean: float
    ratio: float
    mechanically_expected: bool
    note: str


@dataclass
class BiasReport:
    population: int
    dimensions: list[str]
    slices: list[SliceStat]
    disparities: list[Disparity]
    flagged: list[Disparity]

    def to_dict(self) -> dict[str, Any]:
        return {
            "population": self.population,
            "dimensions": self.dimensions,
            "slices": [vars(s) for s in self.slices],
            "disparities": [vars(d) for d in self.disparities],
            "flagged": [vars(d) for d in self.flagged],
        }


DEFAULT_DIMENSIONS = ("age_band", "sex", "occupation", "wealth_bracket", "is_dependent")


def slice_population(
    people: Iterable[Person], dimensions: Sequence[str] = DEFAULT_DIMENSIONS
) -> list[SliceStat]:
    """Break the town down along each dimension. Section 24's first bullet."""
    people = list(people)
    if not people:
        return []
    balances = [p.balance_sim for p in people]
    total = len(people)
    stats: list[SliceStat] = []
    for dimension in dimensions:
        buckets: dict[str, list[Person]] = {}
        for person in people:
            buckets.setdefault(person.slice_value(dimension, balances), []).append(person)
        for value, members in sorted(buckets.items()):
            member_balances = [m.balance_sim for m in members]
            stats.append(
                SliceStat(
                    dimension=dimension,
                    value=value,
                    count=len(members),
                    share_of_town=round(len(members) / total, 4),
                    mean_balance=round(statistics.fmean(member_balances), 2),
                    median_balance=float(statistics.median(member_balances)),
                    alive_rate=round(sum(1 for m in members if m.alive) / len(members), 4),
                )
            )
    return stats


def bias_report(
    people: Iterable[Person],
    dimensions: Sequence[str] = DEFAULT_DIMENSIONS,
    *,
    threshold: float = DISPARITY_THRESHOLD,
    min_slice: int = MIN_SLICE_SIZE,
) -> BiasReport:
    """"Check the town's aggregate patterns are... not skewed toward one
    group - the same bias check these papers run."

    Reports every dimension's best- and worst-off slice and the ratio
    between them. A dimension the town's own rules are expected to separate
    is marked as such rather than silently dropped: the reader needs to see
    that the elderly are poorer AND that this is the pension design, not be
    shown only one of those two facts.
    """
    people = [p for p in people]
    stats = slice_population(people, dimensions)
    by_dimension: dict[str, list[SliceStat]] = {}
    for stat in stats:
        by_dimension.setdefault(stat.dimension, []).append(stat)

    disparities: list[Disparity] = []
    for dimension, slices in by_dimension.items():
        usable = [s for s in slices if s.count >= min_slice]
        if len(usable) < 2:
            continue
        best = max(usable, key=lambda s: s.mean_balance)
        worst = min(usable, key=lambda s: s.mean_balance)
        ratio = (
            float("inf") if worst.mean_balance <= 0
            else round(best.mean_balance / worst.mean_balance, 3)
        )
        expected = dimension in MECHANICALLY_EXPECTED
        disparities.append(
            Disparity(
                dimension=dimension,
                best_value=best.value,
                worst_value=worst.value,
                best_mean=best.mean_balance,
                worst_mean=worst.mean_balance,
                ratio=ratio,
                mechanically_expected=expected,
                note=(
                    "the town's own rules treat these groups differently, so a gap here "
                    "is the design working, not a finding"
                    if expected
                    else "no mechanic in the town reads this dimension, so a gap here comes "
                         "from genesis or from the personas"
                ),
            )
        )

    flagged = [
        d for d in disparities
        if not d.mechanically_expected and (d.ratio == float("inf") or d.ratio >= threshold)
    ]
    disparities.sort(key=lambda d: (d.ratio if d.ratio != float("inf") else 1e9), reverse=True)
    flagged.sort(key=lambda d: (d.ratio if d.ratio != float("inf") else 1e9), reverse=True)
    return BiasReport(
        population=len(people),
        dimensions=list(dimensions),
        slices=stats,
        disparities=disparities,
        flagged=flagged,
    )


@dataclass
class HouseholdOutcome:
    shape: str
    households: int
    mean_runway_days: float
    median_runway_days: float
    destitute_rate: float
    mean_combined_sim: float


def household_outcomes(households: Iterable[dict[str, Any]]) -> list[HouseholdOutcome]:
    """Survival outcomes by household shape. Section 24's second bullet.

    "Check whether the pattern that emerges - which family shapes thrive,
    and which struggle - is a plausible economic story, not an artifact of
    how the genesis generator happened to assign occupations."

    Takes the rows `world/family_api.py` already returns, so the two views
    cannot disagree about what a household's shape or runway is.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for household in households:
        buckets.setdefault(str(household.get("shape", "unknown")), []).append(household)

    out: list[HouseholdOutcome] = []
    for shape, rows in sorted(buckets.items()):
        runways = [float(r.get("days_of_runway", 0)) for r in rows]
        out.append(
            HouseholdOutcome(
                shape=shape,
                households=len(rows),
                mean_runway_days=round(statistics.fmean(runways), 2),
                median_runway_days=float(statistics.median(runways)),
                destitute_rate=round(sum(1 for r in runways if r <= 0) / len(rows), 4),
                mean_combined_sim=round(
                    statistics.fmean([float(r.get("combined_sim", 0)) for r in rows]), 2
                ),
            )
        )
    out.sort(key=lambda o: o.mean_runway_days)
    return out


def emergence_check(
    people: Iterable[Person],
    behaviour: Callable[[Person], bool],
    dimensions: Sequence[str] = ("age_band", "sex", "occupation"),
    *,
    min_slice: int = MIN_SLICE_SIZE,
) -> list[dict[str, Any]]:
    """Did a behaviour emerge evenly, or concentrate in one slice?

    Section 24's third bullet: "track whether patterns *emerge* that
    weren't scripted - who votes, who prays, who marries whom, who ends up
    wealthy, who helps a struggling relative and who doesn't."

    `behaviour` is any predicate over a Person - the caller supplies "did
    this one vote", and this returns the rate per slice. Deliberately not a
    fixed list of behaviours: the interesting ones are the unscripted, and
    a hardcoded list could only ever contain the ones already thought of.
    """
    people = list(people)
    if not people:
        return []
    balances = [p.balance_sim for p in people]
    town_rate = sum(1 for p in people if behaviour(p)) / len(people)
    rows: list[dict[str, Any]] = []
    for dimension in dimensions:
        buckets: dict[str, list[Person]] = {}
        for person in people:
            buckets.setdefault(person.slice_value(dimension, balances), []).append(person)
        for value, members in sorted(buckets.items()):
            if len(members) < min_slice:
                continue
            rate = sum(1 for m in members if behaviour(m)) / len(members)
            rows.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "count": len(members),
                    "rate": round(rate, 4),
                    "town_rate": round(town_rate, 4),
                    "lift": round(rate / town_rate, 3) if town_rate > 0 else None,
                }
            )
    rows.sort(key=lambda r: r["rate"], reverse=True)
    return rows


def people_from_world(world: Any, household_shapes: dict[str, str] | None = None) -> list[Person]:
    """Assemble `Person` records from a live WorldState.

    A convenience for the common caller, kept at the bottom and deliberately
    duck-typed: `research/` is read-only and must not drag world's own
    imports into every consumer of this module (a Parquet-driven analysis
    has no WorldState at all).
    """
    shapes = household_shapes or {}
    people: list[Person] = []
    for agent_id, agent in world.agents.items():
        account = world.ledger.accounts.get(agent_id)
        occupation = str(getattr(agent, "occupation", "") or "unknown")
        people.append(
            Person(
                agent_id=agent_id,
                age_years=float(agent.age_years),
                sex=getattr(agent.sex, "value", str(agent.sex)),
                occupation=occupation,
                household_id=agent.household_id,
                balance_sim=account.sim_units if account is not None else 0,
                alive=bool(agent.alive),
                is_dependent=bool(getattr(agent, "is_minor", False)),
                household_shape=shapes.get(agent.household_id or "", "unknown"),
            )
        )
    return people
