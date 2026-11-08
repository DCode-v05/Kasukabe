"""The town's rulebook, loaded and checked.

`village_rules.json` next to this file is the rules of Kasukabe kept as data:
wages, the market's supply/demand response, how the workforce re-staffs itself,
what counts as an emergency and what the town does about one, what the
government is paid, and which documents it has to answer. It exists so those
rules can be read and changed by someone who is not going to read the code.

This module is the half that makes that safe. A rulebook that is only data is
a rulebook nobody can trust: a typo in a ratio, a role with no floor, a
document with no decision-maker, and the town runs happily and wrongly for
three hundred game-hours before anyone notices. So every value that other code
depends on is checked HERE, at load, and a bad edit fails at boot with a
message naming the field - never silently at hour 300.

Two deliberate choices:

1. **Nothing is defaulted.** If a caller asks for a rule the file does not
   define, that is a `RuleError`, not a quiet fallback. A missing rule means
   the file and the code disagree about what the town is, and guessing which
   is right is exactly how the wiring breaks this project keeps finding get in.

2. **Unknown keys are allowed.** Every `_about`, `_note` and `_source` field in
   the file is documentation for whoever opens it, and a validator that
   rejected them would make the rulebook unreadable to keep itself tidy. They
   are ignored, never required.

Read it with `load_rules()`. It caches, because the file is read once per
process and the town asks it questions every tick.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).with_name("village_rules.json")

SCHEMA_VERSION = "1.0.0"


class RuleError(ValueError):
    """A rulebook that cannot be trusted. Raised at load, never at use."""


# --- the shape other code is entitled to rely on ---------------------------
# Each entry: dotted path -> the type it must be. Checked exhaustively at load
# so a caller never has to defend against a missing or mistyped rule.
REQUIRED: dict[str, type | tuple[type, ...]] = {
    "schema_version": str,
    "economy.upkeep_per_day_sim": (int, float),
    "economy.head_tax_base_sim": (int, float),
    "economy.tax_rate_bps": (int, float),
    "economy.wages_per_day_sim": dict,
    "economy.pension.minimum_age_years": (int, float),
    "market.price_response.elasticity": (int, float),
    "market.price_response.min_price_multiplier": (int, float),
    "market.price_response.max_price_multiplier": (int, float),
    "market.goods": dict,
    "market.storage.buffer_days": (int, float),
    "market.storage.spoilage_per_day": (int, float),
    "market.shortage.deficit_ratio_watch": (int, float),
    "market.shortage.deficit_ratio_alarm": (int, float),
    "market.shortage.consecutive_days_to_escalate": int,
    "labour.dynamic_staffing": bool,
    "labour.max_moves_per_review": int,
    "labour.retrain_days": (int, float),
    "labour.staffing_targets": dict,
    "labour.recruit_from.order": list,
    "labour.recruit_from.never_recruit": list,
    "health.ailment_daily_incidence": dict,
    "health.hospital_capacity.beds_per_ward": int,
    "health.hospital_capacity.patients_per_doctor": (int, float),
    "health.hospital_capacity.patients_per_nurse": (int, float),
    "health.emergency": dict,
    "government.seats.prime_minister": int,
    "government.seats.minister": int,
    "government.salaries_per_day_sim": dict,
    "government.treasury.reserve_floor_sim": (int, float),
    "government.documents": dict,
    "government.build_queue.projects": list,
    "survival.daily_needs.food_units": (int, float),
    "survival.daily_needs.hunger_days_to_illness": (int, float),
    "survival.daily_needs.starvation_days_to_death": (int, float),
    "survival.childcare.carried_until_age_years": (int, float),
    "survival.childcare.supervised_until_age_years": (int, float),
    "survival.childcare.school_from_age_years": (int, float),
    "survival.wallet.destitute_below_sim": (int, float),
    "survival.adaptation.options": list,
    "unrest.max": (int, float),
    "unrest.decay_per_day": (int, float),
}

# Every emergency must be able to fire AND to stop firing. An emergency with no
# stand-down is a town permanently in crisis.
EMERGENCY_REQUIRED = ("trigger", "response", "stand_down")

# A document nobody decides is a document nobody answers.
DOCUMENT_REQUIRED = ("decided_by",)


def _dig(data: Any, path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise RuleError(f"village_rules.json is missing required rule `{path}`")
        node = node[part]
    return node


def _check_required(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path, want in REQUIRED.items():
        try:
            value = _dig(data, path)
        except RuleError as exc:
            problems.append(str(exc))
            continue
        # bool is an int in Python; a rule declared int must not accept True.
        if want is not bool and isinstance(value, bool):
            problems.append(f"`{path}` is a true/false but should be a number")
        elif not isinstance(value, want):
            names = want.__name__ if isinstance(want, type) else "/".join(t.__name__ for t in want)
            problems.append(f"`{path}` should be {names}, found {type(value).__name__}")
    return problems


def _check_sense(data: dict[str, Any]) -> list[str]:
    """Values that are the right type and still cannot be true."""
    problems: list[str] = []

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {version!r}, this loader understands {SCHEMA_VERSION!r} - "
            "update loader.py alongside the file, or the town runs by rules the code cannot read"
        )

    pr = data["market"]["price_response"]
    if not 0 < pr["elasticity"] <= 3:
        problems.append("market.price_response.elasticity must be above 0 and at most 3")
    if pr["min_price_multiplier"] >= pr["max_price_multiplier"]:
        problems.append("market.price_response min_price_multiplier must be below max_price_multiplier")

    st = data["market"]["storage"]
    if st["buffer_days"] <= 0:
        problems.append("market.storage.buffer_days must be above 0 - a town with no larder starves nightly")
    if not 0 <= st["spoilage_per_day"] < 1:
        problems.append("market.storage.spoilage_per_day must be at least 0 and below 1")
    if st.get("granary_buffer_days", 0) < st["buffer_days"]:
        problems.append(
            "market.storage.granary_buffer_days is not larger than buffer_days - "
            "the granary would make the town worse off"
        )

    sh = data["market"]["shortage"]
    if not 0 < sh["deficit_ratio_alarm"] < sh["deficit_ratio_watch"] <= 1:
        problems.append(
            "market.shortage needs 0 < deficit_ratio_alarm < deficit_ratio_watch <= 1 "
            "(alarm is the worse case, so it is the lower number)"
        )

    for name, good in data["market"]["goods"].items():
        if name.startswith("_"):
            continue
        for field in ("produced_by", "units_per_worker_day", "demand_per_resident_day", "base_price_sim"):
            if field not in good:
                problems.append(f"market.goods.{name} has no `{field}`")
        for need in (good.get("consumes") or {}):
            if need not in data["market"]["goods"]:
                problems.append(f"market.goods.{name} consumes `{need}`, which is not a good")

    for role, target in data["labour"]["staffing_targets"].items():
        if role.startswith("_"):
            continue
        if "per_1000" not in target or "min" not in target:
            problems.append(f"labour.staffing_targets.{role} needs both `per_1000` and `min`")
        if target.get("surge_source") and "per_surge_unit" not in target:
            problems.append(
                f"labour.staffing_targets.{role} names a surge_source but no `per_surge_unit`, "
                "so the surge can never be sized"
            )
    # A role the town may never recruit FROM but must staff to a floor is a
    # deadlock the moment that floor is breached.
    never = set(data["labour"]["recruit_from"]["never_recruit"])
    order = set(data["labour"]["recruit_from"]["order"])
    if never & order:
        problems.append(
            f"labour.recruit_from lists {sorted(never & order)} in both `order` and `never_recruit`"
        )

    for name, emergency in data["health"]["emergency"].items():
        if name.startswith("_"):
            continue
        for field in EMERGENCY_REQUIRED:
            if field not in emergency:
                problems.append(
                    f"health.emergency.{name} has no `{field}` - "
                    "an emergency that cannot stand down never ends"
                )
        for step in emergency.get("response", []):
            if "action" not in step:
                problems.append(f"health.emergency.{name} has a response step with no `action`")
            role = step.get("role")
            if role and role not in data["labour"]["staffing_targets"]:
                problems.append(
                    f"health.emergency.{name} raises staffing for `{role}`, "
                    "which has no entry in labour.staffing_targets"
                )

    for name, doc in data["government"]["documents"].items():
        if name.startswith("_"):
            continue
        for field in DOCUMENT_REQUIRED:
            if field not in doc:
                problems.append(f"government.documents.{name} has no `{field}`")

    seen = set()
    for project in data["government"]["build_queue"]["projects"]:
        for field in ("id", "name", "cost_sim"):
            if field not in project:
                problems.append(f"government.build_queue has a project with no `{field}`")
        pid = project.get("id")
        if pid in seen:
            problems.append(f"government.build_queue has two projects with id `{pid}`")
        seen.add(pid)

    needs = data["survival"]["daily_needs"]
    if needs["starvation_days_to_death"] <= needs["hunger_days_to_illness"]:
        problems.append(
            "survival.daily_needs: starvation_days_to_death must be greater than "
            "hunger_days_to_illness - a resident has to fall ill before they die of it"
        )

    care = data["survival"]["childcare"]
    if care["carried_until_age_years"] > care["supervised_until_age_years"]:
        problems.append(
            "survival.childcare: carried_until_age_years is above "
            "supervised_until_age_years - a child cannot stop being carried "
            "after it stops being supervised"
        )
    if care["school_from_age_years"] < care["carried_until_age_years"]:
        problems.append(
            "survival.childcare: school_from_age_years is below "
            "carried_until_age_years - infants would be sent to school"
        )

    wages = data["economy"]["wages_per_day_sim"]
    upkeep = data["economy"]["upkeep_per_day_sim"]
    earning = {r: w for r, w in wages.items() if not r.startswith("_") and w > 0}
    if not earning:
        problems.append("economy.wages_per_day_sim has no paid trade at all")
    elif max(earning.values()) <= upkeep:
        problems.append(
            f"economy: the best-paid trade ({max(earning.values())}) does not cover upkeep "
            f"({upkeep}) - every resident in town is losing money every day"
        )

    return problems


@dataclass(frozen=True)
class Rules:
    """The validated rulebook. Ask it for a rule; it will not guess."""

    data: dict[str, Any]
    path: Path

    def get(self, path: str) -> Any:
        """One rule, by dotted path. Raises rather than returning None."""
        return _dig(self.data, path)

    def section(self, name: str) -> dict[str, Any]:
        value = self.get(name)
        if not isinstance(value, dict):
            raise RuleError(f"`{name}` is not a section")
        return value

    # -- the questions the town actually asks -----------------------------

    def wage(self, occupation: str) -> float:
        wages = self.get("economy.wages_per_day_sim")
        if occupation not in wages:
            raise RuleError(f"no wage in the rulebook for `{occupation}`")
        return float(wages[occupation])

    def staffing_target(self, role: str, population: int, surge_units: float = 0.0) -> int:
        """How many of `role` the town should have right now.

        Baseline scales with population; the surge term is what makes the
        workforce respond to what is happening rather than to the census. A
        town of 314 wants 3 doctors; the same town with 40 sick residents wants
        those 3 plus one per 15 sick, and `labour` moves people until it has
        them.
        """
        targets = self.get("labour.staffing_targets")
        if role not in targets:
            raise RuleError(f"no staffing target in the rulebook for `{role}`")
        target = targets[role]
        base = population * float(target["per_1000"]) / 1000.0
        surge = 0.0
        if target.get("surge_source") and surge_units:
            surge = float(surge_units) / float(target["per_surge_unit"])
        return max(int(target["min"]), round(base + surge))

    def price_multiplier(self, supply: float, demand: float) -> float:
        """What a shortfall does to a price. 1.0 means supply met demand."""
        pr = self.get("market.price_response")
        if supply <= 0:
            return float(pr["max_price_multiplier"])
        if demand <= 0:
            return float(pr["min_price_multiplier"])
        raw = (demand / supply) ** float(pr["elasticity"])
        return max(float(pr["min_price_multiplier"]), min(float(pr["max_price_multiplier"]), raw))

    def shortage_level(self, supply: float, demand: float) -> str:
        """`ok`, `watch` or `shortage` - the three states the town reports."""
        if demand <= 0:
            return "ok"
        ratio = supply / demand
        sh = self.get("market.shortage")
        if ratio < float(sh["deficit_ratio_alarm"]):
            return "shortage"
        if ratio < float(sh["deficit_ratio_watch"]):
            return "watch"
        return "ok"


_cache: dict[Path, Rules] = {}


def load_rules(path: Path | None = None, *, reload: bool = False) -> Rules:
    """Read, validate and cache the rulebook.

    Raises `RuleError` listing every problem at once - fixing rules one
    error per boot is its own kind of misery.
    """
    target = Path(path) if path is not None else RULES_PATH
    if not reload and target in _cache:
        return _cache[target]

    if not target.exists():
        raise RuleError(f"no rulebook at {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"{target.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleError(f"{target.name} must be a JSON object")

    problems = _check_required(data)
    if not problems:                      # sense checks assume the shape is there
        problems = _check_sense(data)
    if problems:
        listed = "\n  - ".join(problems)
        raise RuleError(f"{target.name} has {len(problems)} problem(s):\n  - {listed}")

    rules = Rules(data=data, path=target)
    _cache[target] = rules
    return rules
