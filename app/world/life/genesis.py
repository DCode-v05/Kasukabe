"""GENESIS GENERATOR: 250 personas, households, businesses, ablation tags.

Produces the Genesis pydantic model (models.py) and writes it to
genesis.json. Everything here is deterministic given a seed - same seed,
same population, byte-identical output - so genesis is reproducible
across runs without spending a cent of the $1000 inference budget
(DESIGN.md section 3) or calling broker/ at all (see namepool.py's
module docstring for why persona text is templated, not LLM-written).

Demographics (DESIGN.md section 4): ~15% minors, ~70% working-age
(18-69), ~15% elderly (70+), ~50/50 sex, pre-linked households/families.
Household model used here (an ASSUMPTION - DESIGN.md describes the
target shape but not a generation algorithm): elderly and working-age
agents are paired into couples at a fixed rate with the remainder
single, minors are distributed as children among working-age couples,
and every agent ends up in exactly one household. Business model
(DESIGN.md section 3): one construction company (owner + hands) plus
small owner-run shops for the genesis café/grocery/pharmacy buildings
(DESIGN.md section 1), assigned as an ASSUMPTION since DESIGN.md names
only the construction company explicitly. Ablation split (DESIGN.md
section 10 + this task's spec): 210 full / 20 no-reflection / 20
no-learning, summing to exactly 250.

RICH PERSONAS (DESIGN.md "Personas: a real starting point, not a stub"):
a one-line "name, age, job" seed is explicitly called out there as
producing interchangeable, worthless-for-research agents. Every persona
here carries formative history, values and a hard non-negotiable, fears,
ambitions and how they act under scarcity, how they treat strangers
versus family, trade competence AND a trade blind spot, a distinct
speech register and verbal tics, a daily rhythm, four attitude axes
(money/risk/authority/religion), one private secret, and a real
internal tension (a virtue paired with a flaw, DESIGN.md's own example:
"generous but proud") - not a list of virtues. See PersonaDepth below.

MUTUAL CONSISTENCY (this task's spec): personas name specific real
neighbours - debts, grudges, affection, rivalries, mentorships,
friendships, colleague ties - and every one of those opinions is built
from a single shared "fact" object that produces BOTH sides' entries in
the same pass (build_relationship_opinions below), so a debt Hana
resents Kenji for is, by construction, the exact same debt Kenji's own
persona knows about from his side (same reason, same sim amount,
opposite role). test_genesis.py's validator tests assert this
programmatically: every named target exists, names match, and every
opinion has a reciprocal entry on the other side with a matching
shared_fact.

These richer fields live on RichGenesisAgent/RichGenesis, defined in
this module rather than world/life/models.py: they extend (subclass)
GenesisAgent/Genesis purely additively, so every existing consumer
(world/wiring.py's life_record_from_genesis, world/run_genesis.py's
seed_genesis, both of which read only the original flat fields) keeps
working unmodified against plain Genesis.model_validate_json() - pydantic
v2's default `extra="ignore"` on GenesisAgent means the extra `persona`
key on each agent in genesis.json is simply not looked at by code that
doesn't know about it. This keeps the richer schema entirely inside this
task's owned files (genesis.py, genesis.json, and their tests) instead
of touching the shared models.py other tracks also import.
"""

from __future__ import annotations

import argparse
import random
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from world.life.constants import (
    GENESIS_AGENT_STARTING_SIM_BALANCE,
    GENESIS_AGENT_WALLET_USD_CENTS,
    GENESIS_BUSINESS_STARTING_SIM_BALANCE,
    GENESIS_POPULATION,
)
from world.life.models import (
    AblationArm,
    AblationSummary,
    ConstructionCompany,
    ControlBusiness,
    Genesis,
    GenesisAgent,
    Household,
    MaritalStatus,
    Sex,
    SmallBusiness,
    UsdLedgerSummary,
)
from world.life.namepool import (
    ADULT_TENSION,
    AFFECTION_BENEFACTOR_NOTE_POOL,
    AFFECTION_GRATEFUL_NOTE_POOL,
    AFFECTION_KINDNESS_POOL,
    AMBITIONS_POOL,
    APPRENTICE_NOTE_POOL,
    ATTITUDE_AUTHORITY_POOL,
    ATTITUDE_MONEY_POOL,
    ATTITUDE_RELIGION_POOL,
    ATTITUDE_RISK_POOL,
    COLLEAGUE_NOTE_POOL,
    COMPETENCE_BLINDSPOT_BY_CATEGORY,
    DAILY_RHYTHM_BY_CATEGORY,
    DEBT_OWED_NOTE_POOL,
    DEBT_OWER_NOTE_POOL,
    DEBT_REASON_POOL,
    ELDERLY_OCCUPATION,
    FAMILY_TREATMENT_POOL,
    FEARS_POOL,
    FEMALE_FIRST_NAMES,
    FORMATIVE_HISTORY_BY_CATEGORY,
    FRIEND_NOTE_POOL,
    FRIENDSHIP_BOND_POOL,
    GENERIC_OCCUPATIONS,
    GRUDGE_ACCUSED_NOTE_POOL,
    GRUDGE_AGGRIEVED_NOTE_POOL,
    GRUDGE_INCIDENT_POOL,
    HABITS,
    HARDSHIP_EVENTS,
    MALE_FIRST_NAMES,
    MENTOR_NOTE_POOL,
    MENTORSHIP_SKILL_POOL,
    MINOR_AMBITIONS_POOL,
    MINOR_BOND_POOL,
    MINOR_FAMILY_TREATMENT_POOL,
    MINOR_FEARS_POOL,
    MINOR_FRIEND_NOTE_POOL,
    MINOR_OCCUPATION_SCHOOL,
    MINOR_OCCUPATION_YOUNG,
    MINOR_RIVAL_CONTEST_POOL,
    MINOR_RIVAL_NOTE_POOL,
    MINOR_SCARCITY_BEHAVIOR,
    MINOR_SCHOOL_AGE_YEARS,
    MINOR_SECRET_POOL,
    MINOR_SPEECH_REGISTER_POOL,
    MINOR_STRANGER_TREATMENT_POOL,
    MINOR_TENSION,
    MINOR_VALUES_POOL,
    MINOR_VERBAL_HABIT_POOL,
    MINOR_WILL_NOT_COMPROMISE,
    ORIGIN_TOWNS,
    PERSONALITY_TRAITS,
    RIVALRY_CONTEST_POOL,
    RIVALRY_NOTE_POOL,
    SCARCITY_BEHAVIOR_POOL,
    SECRET_ABOUT_NEIGHBOUR_POOL,
    SECRET_POOL,
    SPEECH_REGISTER_POOL,
    STRANGER_TREATMENT_POOL,
    SURNAMES,
    VALUES_POOL,
    VERBAL_HABIT_POOL,
    WILL_NOT_COMPROMISE_TEMPLATES,
)

MINOR_FRACTION = 0.15
ELDERLY_FRACTION = 0.15
WORKING_AGE_MIN, WORKING_AGE_MAX = 18, 69
ELDERLY_AGE_MIN, ELDERLY_AGE_MAX = 70, 100
MINOR_AGE_MIN, MINOR_AGE_MAX = 0, 17
ELDERLY_COUPLE_FRACTION = 0.6
WORKING_COUPLE_FRACTION = 0.6
MAX_CHILDREN_PER_COUPLE = 3
CONSTRUCTION_HANDS_COUNT = 4

INSTITUTION_OCCUPATION_LABELS: dict[str, str] = {
    "doctor": "treats the sick at the town hospital",
    "nurse": "nurses patients on the hospital ward",
    "police_officer": "keeps the peace out of the police station",
    "firefighter": "crews the town fire station",
    "baker": "bakes the town's bread before dawn",
    "chef": "cooks at the town restaurant",
    "waiter": "waits tables at the town restaurant",
    "school_teacher": "teaches the town's children at the school",
    "librarian": "keeps the town library",
    "banker": "handles accounts at the town bank",
    "innkeeper": "keeps rooms and a bar at the inn",
    "street_sweeper": "sweeps the streets around the town square",
    "shopkeeper": "keeps a stall on Market Row",
}

OCCUPATION_LABELS: dict[str, str] = {
    "child": "is too young for school or work yet",
    "student": "attends the town school",
    "retired": "is retired and draws a pension",
    "farmer": "works the farmland at the town's edge",
    "miner": "works the ore outcrops",
    "forester": "fells timber in the forest zone",
    "government_clerk": "files paperwork at the government complex",
    "office_administrator": "keeps one of the office spaces running",
    "college_instructor": "teaches at the college",
    "college_student": "studies at the college",
    "artisan": "crafts goods to sell around town",
    "trader": "trades goods around the market",
    "day_laborer": "takes whatever work needs doing",
    "construction_company_owner": "owns and runs the town's one construction company",
    "construction_worker": "works the crews for the town's construction company",
    "grocery_store_owner": "owns the grocery store",
    "grocery_clerk": "clerks at the grocery store",
    "cafe_owner": "owns the cafe",
    "barista": "runs the counter at the cafe",
    "pharmacist": "keeps the pharmacy stocked",
}

INSTITUTION_STAFF: tuple[tuple[str, str, int], ...] = (
    ("hospital", "doctor", 3),
    ("hospital", "nurse", 7),
    ("police", "police_officer", 8),
    ("fire", "firefighter", 6),
    ("bakery", "baker", 4),
    ("restaurant", "chef", 3),
    ("restaurant", "waiter", 4),
    ("school", "school_teacher", 9),
    ("library", "librarian", 3),
    ("bank", "banker", 5),
    ("inn", "innkeeper", 4),
    ("townsquare", "street_sweeper", 3),
    ("marketrow", "shopkeeper", 5),
)

INSTITUTION_OCCUPATION_CATEGORY: dict[str, str] = {
    "doctor": "pharmacy",
    "nurse": "pharmacy",
    "police_officer": "civic",
    "firefighter": "civic",
    "baker": "cafe",
    "chef": "cafe",
    "waiter": "cafe",
    "school_teacher": "academic",
    "librarian": "academic",
    "banker": "civic",
    "innkeeper": "cafe",
    "street_sweeper": "outdoor",
    "shopkeeper": "grocery",
}

OCCUPATION_TO_CATEGORY: dict[str, str] = {
    "farmer": "outdoor",
    "miner": "outdoor",
    "forester": "outdoor",
    "government_clerk": "civic",
    "office_administrator": "civic",
    "college_instructor": "academic",
    "college_student": "academic",
    "artisan": "trade",
    "trader": "trade",
    "day_laborer": "trade",
    "construction_company_owner": "construction",
    "construction_worker": "construction",
    "grocery_store_owner": "grocery",
    "grocery_clerk": "grocery",
    "cafe_owner": "cafe",
    "barista": "cafe",
    "pharmacist": "pharmacy",
    "retired": "elderly",
    MINOR_OCCUPATION_YOUNG: "minor_young",
    MINOR_OCCUPATION_SCHOOL: "minor_school",
}

RELATIONSHIP_ROLE_PAIRS: dict[str, tuple[str, str]] = {
    "debt": ("ower", "owed"),
    "grudge": ("aggrieved", "accused"),
    "affection": ("grateful", "benefactor"),
    "rivalry": ("rival", "rival"),
    "mentorship": ("mentor", "apprentice"),
    "friendship": ("friend", "friend"),
    "colleague": ("colleague", "colleague"),
}
ADULT_RELATIONSHIP_KINDS: tuple[str, ...] = (
    "debt",
    "grudge",
    "affection",
    "rivalry",
    "mentorship",
    "friendship",
)
MINOR_RELATIONSHIP_KINDS: tuple[str, ...] = ("friendship", "rivalry")


class NeighbourOpinion(BaseModel):
    """One agent's opinion of one specific named other agent.

    ``shared_fact`` is the underlying event (a debt's reason and amount,
    a grudge's incident, a kindness, a rivalry's arena, a skill taught,
    a bond) rendered identically on both sides of the relationship -
    that identity is what "mutually consistent" means here and what
    test_genesis.py's symmetry test asserts directly, character for
    character. ``note`` is this agent's own, personal spin on the same
    fact, which is free to differ (and often does - see grudge role
    pairs, where the aggrieved and the accused read the same incident
    very differently).
    """

    target_agent_id: str
    target_name: str
    kind: str
    role: str
    shared_fact: str
    note: str
    sim_amount: int | None = None


class PersonaDepth(BaseModel):
    """Everything DESIGN.md's Personas section asks for beyond name, age,
    job, and a couple of habits (the exact thing that section says a
    one-line seed produces and calls "worthless for research")."""

    internal_tension: str
    tension_description: str
    formative_history: str
    values: list[str] = Field(default_factory=list)
    will_not_compromise: str
    fears: str
    ambitions: str
    scarcity_behavior: str
    stranger_treatment: str
    family_treatment: str
    competence: str
    blind_spot: str
    speech_register: str
    verbal_habits: list[str] = Field(default_factory=list)
    daily_rhythm: str
    attitude_money: str
    attitude_risk: str
    attitude_authority: str
    attitude_religion: str
    secret: str
    secret_target_agent_id: str | None = None
    opinions: list[NeighbourOpinion] = Field(default_factory=list)


class RichGenesisAgent(GenesisAgent):
    """GenesisAgent plus PersonaDepth. Every original field keeps its
    original meaning and type - this is a strict additive extension, see
    module docstring."""

    persona: PersonaDepth


class RichGenesis(Genesis):
    """Genesis with the richer agent shape. Same additive-extension
    reasoning as RichGenesisAgent - Genesis.model_validate_json() (used
    by world/wiring.py and world/run_genesis.py) parses this file's JSON
    just fine using the plain GenesisAgent shape; only code that imports
    RichGenesis/RichGenesisAgent from this module sees the persona
    field."""

    agents: list[RichGenesisAgent]


OCCUPATION_TO_CATEGORY.update(INSTITUTION_OCCUPATION_CATEGORY)
OCCUPATION_LABELS.update(INSTITUTION_OCCUPATION_LABELS)


def bracket_sizes(population: int) -> tuple[int, int, int]:
    """(minors, working, elderly) counts summing to ``population``, ~15/70/15."""
    minors = round(population * MINOR_FRACTION)
    elderly = round(population * ELDERLY_FRACTION)
    working = population - minors - elderly
    return minors, working, elderly


def pair_up(rng: random.Random, ids: list[str], pair_fraction: float) -> list[list[str]]:
    """Group ids into couples (len 2) and singles (len 1).

    ``pair_fraction`` of the group is paired off; rounded down to an
    even number since a couple needs two people.
    """
    shuffled = list(ids)
    rng.shuffle(shuffled)
    target_paired = round(len(shuffled) * pair_fraction)
    if target_paired % 2 == 1:
        target_paired -= 1
    groups: list[list[str]] = []
    i = 0
    while i < target_paired:
        groups.append([shuffled[i], shuffled[i + 1]])
        i += 2
    while i < len(shuffled):
        groups.append([shuffled[i]])
        i += 1
    return groups


def distribute_children(
    rng: random.Random,
    num_children: int,
    num_couples: int,
    max_per_couple: int = MAX_CHILDREN_PER_COUPLE,
) -> list[int]:
    """How many of ``num_children`` minors each of ``num_couples`` households gets."""
    if num_couples == 0:
        if num_children > 0:
            raise ValueError("no couples available to assign minors as children")
        return []
    counts = [0] * num_couples
    for _ in range(num_children):
        candidates = [i for i, c in enumerate(counts) if c < max_per_couple]
        if not candidates:
            raise ValueError(
                f"{num_couples} couples can't hold {num_children} minors at "
                f"max {max_per_couple} children each"
            )
        counts[rng.choice(candidates)] += 1
    return counts


def unique_name(rng: random.Random, sex: Sex, surname: str, used_names: set[str]) -> str:
    """A "first last" name not already in ``used_names``, mutating it on success."""
    pool = list(MALE_FIRST_NAMES if sex is Sex.MALE else FEMALE_FIRST_NAMES)
    rng.shuffle(pool)
    for first in pool:
        candidate = f"{first} {surname}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
    suffix = 2
    first = pool[0]
    while f"{first} {surname} {suffix}" in used_names:
        suffix += 1
    candidate = f"{first} {surname} {suffix}"
    used_names.add(candidate)
    return candidate


def pronoun(sex: Sex) -> str:
    """Capitalized subject pronoun, for starting a bio sentence."""
    return "He" if sex is Sex.MALE else "She"


def format_ctx(
    name: str,
    sex: Sex,
    age: int | None = None,
    target_name: str | None = None,
) -> dict[str, str]:
    """The str.format() context every namepool.py persona template is
    written against - see that module's own docstring for the exact
    placeholder list."""
    subj = "he" if sex is Sex.MALE else "she"
    poss = "his" if sex is Sex.MALE else "her"
    ctx = {
        "name": name,
        "Name": name,
        "subj": subj,
        "Subj": subj.capitalize(),
        "poss": poss,
        "Poss": poss.capitalize(),
        "obj": "him" if sex is Sex.MALE else "her",
        "reflexive": "himself" if sex is Sex.MALE else "herself",
    }
    if age is not None:
        ctx["age"] = str(age)
    if target_name is not None:
        ctx["target_name"] = target_name
    return ctx


def recount_ablation_summary(genesis: dict) -> dict:
    """Rewrite ``genesis["ablation_summary"]`` from the agents actually in it.

    EVAL-16 saw one file: `genesis_20.json` claiming ten residents' worth of
    arms for twenty. Swept, it was eleven of fifteen, and `genesis_3.json`
    claimed 314. The cause is mechanical - every generator here derives a
    cast from a bigger one and copies this dict forward, while recomputing
    `population`, `households` and `usd_ledger` right beside it.

    A DERIVED field, so it is computed rather than carried. Nothing in a run
    reads it - the seam honours each agent's own `ablation_arm`
    (`engine/src/sim/pinned_boot.py`), which is why this drifted silently for
    so long. What reads it is a person, and the evaluation spec's L4 arm
    comparison: a file claiming twenty no-learning residents it does not have
    is a claim about an experiment nobody ran.

    Mutates and returns ``genesis`` so it can be the last line of a builder.
    Absent arms are written as 0 rather than omitted, so the shape stays
    stable for anything reading the keys.
    """
    counts: dict[str, int] = {arm.value: 0 for arm in AblationArm}
    for agent in genesis.get("agents") or []:
        arm = str(agent.get("ablation_arm") or AblationArm.FULL.value)
        counts[arm] = counts.get(arm, 0) + 1
    genesis["ablation_summary"] = counts
    return genesis


def ablation_counts(population: int) -> AblationSummary:
    """Split ``population`` into ablation arms, 210/20/20 at the genesis
    population of 250 (DESIGN.md section 10, this task's exact spec),
    proportional otherwise. ``deepseek_locked`` is kept at 0 rather than
    dropped from AblationSummary/AblationArm (models.py, not owned by
    this task) since DESIGN.md's own ablation-arm list is exactly these
    three - full / no-reflection / no-learning - with no fourth arm.
    """
    if population == GENESIS_POPULATION:
        return AblationSummary(full=210, no_reflection=20, no_learning=20, deepseek_locked=0)
    no_reflection = round(population * 0.08)
    no_learning = round(population * 0.08)
    full = population - no_reflection - no_learning
    return AblationSummary(
        full=full, no_reflection=no_reflection, no_learning=no_learning, deepseek_locked=0
    )


def build_control_businesses() -> list[ControlBusiness]:
    """10 scripted control businesses (DESIGN.md section 10), rule-based
    pricing, no LLM and no owning agent - they exist purely as a
    baseline the "Business readiness" research track compares agent-run
    businesses against.
    """
    specs = [
        ("cb01", "Ration Bakery", "bakery", "cost_plus_10pct"),
        ("cb02", "Steady General Store", "general_store", "undercut_min_price_5pct"),
        ("cb03", "Farm Cooperative", "farm_coop", "fixed_price_seasonal"),
        ("cb04", "Old Anvil Smithy", "smithy", "cost_plus_20pct"),
        ("cb05", "Riverside Lumber Mill", "lumber_mill", "fixed_price_seasonal"),
        ("cb06", "Plainstitch Tailor", "tailor", "cost_plus_15pct"),
        ("cb07", "Wayfarer Inn", "inn", "dynamic_occupancy_pricing"),
        ("cb08", "Kasukabe Transport", "transport", "distance_based_flat_rate"),
        ("cb09", "Mutual Bank Stall", "bank_stall", "fixed_spread_lending"),
        ("cb10", "Open Market Stall", "market_stall", "undercut_min_price_10pct"),
    ]
    return [
        ControlBusiness(
            business_id=business_id,
            name=name,
            business_type=business_type,
            pricing_strategy=strategy,
            starting_sim_balance=GENESIS_BUSINESS_STARTING_SIM_BALANCE,
        )
        for business_id, name, business_type, strategy in specs
    ]


def write_genesis_json(genesis: Genesis, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(genesis.model_dump_json(indent=2) + "\n", encoding="utf-8")


def clamp_years_ago(age: int) -> int:
    high = max(1, min(40, age - 1)) if age > 1 else 1
    return high


def pick_tension(rng: random.Random, name: str, sex: Sex, is_minor: bool) -> tuple[str, str]:
    pool = MINOR_TENSION if is_minor else ADULT_TENSION
    virtue, flaw, template = rng.choice(pool)
    sentence = template.format(**format_ctx(name, sex))
    return f"{virtue} but {flaw}", sentence


def pick_formative_history(
    rng: random.Random, name: str, sex: Sex, age: int, category: str
) -> str:
    pool = FORMATIVE_HISTORY_BY_CATEGORY[category]
    template = rng.choice(pool)
    ctx = format_ctx(name, sex, age=age)
    ctx["years_ago"] = str(rng.randint(1, clamp_years_ago(age)))
    ctx["origin"] = rng.choice(ORIGIN_TOWNS)
    ctx["hardship"] = rng.choice(HARDSHIP_EVENTS)
    return template.format(**ctx)


def pick_values(rng: random.Random, sex: Sex, is_minor: bool) -> tuple[list[str], str]:
    values_pool = MINOR_VALUES_POOL if is_minor else VALUES_POOL
    values = rng.sample(values_pool, 2 if is_minor else 3)
    line_pool = MINOR_WILL_NOT_COMPROMISE if is_minor else WILL_NOT_COMPROMISE_TEMPLATES
    will_not_compromise = rng.choice(line_pool).format(**format_ctx("", sex))
    return values, will_not_compromise


def pick_fear(rng: random.Random, sex: Sex, is_minor: bool) -> str:
    pool = MINOR_FEARS_POOL if is_minor else FEARS_POOL
    return rng.choice(pool).format(**format_ctx("", sex))


def pick_ambition(rng: random.Random, sex: Sex, is_minor: bool) -> str:
    pool = MINOR_AMBITIONS_POOL if is_minor else AMBITIONS_POOL
    return rng.choice(pool).format(**format_ctx("", sex))


def pick_scarcity_behavior(rng: random.Random, sex: Sex, is_minor: bool) -> str:
    pool = MINOR_SCARCITY_BEHAVIOR if is_minor else SCARCITY_BEHAVIOR_POOL
    return rng.choice(pool).format(**format_ctx("", sex))


def pick_stranger_treatment(rng: random.Random, sex: Sex, is_minor: bool) -> str:
    pool = MINOR_STRANGER_TREATMENT_POOL if is_minor else STRANGER_TREATMENT_POOL
    return rng.choice(pool).format(**format_ctx("", sex))


def pick_family_treatment(rng: random.Random, sex: Sex, is_minor: bool) -> str:
    pool = MINOR_FAMILY_TREATMENT_POOL if is_minor else FAMILY_TREATMENT_POOL
    return rng.choice(pool).format(**format_ctx("", sex))


def pick_competence_blind_spot(rng: random.Random, sex: Sex, category: str) -> tuple[str, str]:
    competence, blind_spot = rng.choice(COMPETENCE_BLINDSPOT_BY_CATEGORY[category])
    ctx = format_ctx("", sex)
    return competence.format(**ctx), blind_spot.format(**ctx)


def pick_speech(rng: random.Random, sex: Sex, is_minor: bool) -> tuple[str, list[str]]:
    register_pool = MINOR_SPEECH_REGISTER_POOL if is_minor else SPEECH_REGISTER_POOL
    habit_pool = MINOR_VERBAL_HABIT_POOL if is_minor else VERBAL_HABIT_POOL
    ctx = format_ctx("", sex)
    register = rng.choice(register_pool).format(**ctx)
    habits = [h.format(**ctx) for h in rng.sample(habit_pool, 2)]
    return register, habits


def pick_daily_rhythm(rng: random.Random, sex: Sex, category: str) -> str:
    return rng.choice(DAILY_RHYTHM_BY_CATEGORY[category]).format(**format_ctx("", sex))


def pick_attitudes(rng: random.Random, sex: Sex) -> tuple[str, str, str, str]:
    ctx = format_ctx("", sex)
    money = rng.choice(ATTITUDE_MONEY_POOL).format(**ctx)
    risk = rng.choice(ATTITUDE_RISK_POOL).format(**ctx)
    authority = rng.choice(ATTITUDE_AUTHORITY_POOL).format(**ctx)
    religion = rng.choice(ATTITUDE_RELIGION_POOL).format(**ctx)
    return money, risk, authority, religion


def build_bio(
    name: str,
    age_years: int,
    sex: Sex,
    occupation: str,
    habit: str,
    spouse_name: str | None,
    num_children: int,
    is_minor: bool,
    parent_names: list[str],
    formative_history: str,
    tension_description: str,
    value: str,
    fear: str,
) -> str:
    label = OCCUPATION_LABELS.get(occupation, "works around town")
    subj_cap = pronoun(sex)
    subj = subj_cap.lower()
    sentence1 = f"{name} is a {age_years}-year-old resident of Kasukabe who {label}."

    if spouse_name is not None and num_children > 0:
        child_word = "child" if num_children == 1 else "children"
        household_sentence = (
            f"{subj_cap} shares a household with {spouse_name} and their {num_children} {child_word}."
        )
    elif spouse_name is not None:
        household_sentence = f"{subj_cap} shares a household with {spouse_name}."
    elif is_minor and parent_names:
        joined = " and ".join(parent_names)
        household_sentence = f"{subj_cap} lives at home with {joined}."
    else:
        household_sentence = f"{subj_cap} keeps a household alone."

    flavor_sentence = (
        f"{subj_cap} values {value} above almost anything, and fears, more than "
        f"{subj} lets on, {fear}."
    )
    habit_sentence = f"{subj_cap} {habit}."
    return " ".join(
        [
            sentence1,
            formative_history,
            household_sentence,
            tension_description,
            flavor_sentence,
            habit_sentence,
        ]
    )


def make_opinion_pair(
    rng: random.Random,
    agent_a: str,
    name_a: str,
    sex_a: Sex,
    agent_b: str,
    name_b: str,
    sex_b: Sex,
    kind: str,
) -> tuple[NeighbourOpinion, NeighbourOpinion]:
    """Builds both sides of one relationship edge from a single shared
    fact, so the two NeighbourOpinion objects returned are consistent by
    construction rather than by after-the-fact reconciliation - see
    module docstring's "MUTUAL CONSISTENCY" section.
    """
    role_a, role_b = RELATIONSHIP_ROLE_PAIRS[kind]
    sim_amount: int | None = None

    if kind == "debt":
        debt_ctx = format_ctx("", sex_a)
        debt_ctx["hardship"] = rng.choice(HARDSHIP_EVENTS)
        reason = rng.choice(DEBT_REASON_POOL).format(**debt_ctx)
        sim_amount = rng.randint(3, 60) * 100
        shared_fact = f"{name_a} owes {name_b} {sim_amount} sim {reason}."
        note_a = DEBT_OWER_NOTE_POOL[rng.randrange(len(DEBT_OWER_NOTE_POOL))].format(
            **format_ctx("", sex_a)
        )
        note_b = DEBT_OWED_NOTE_POOL[rng.randrange(len(DEBT_OWED_NOTE_POOL))].format(
            **format_ctx("", sex_b)
        )
    elif kind == "grudge":
        incident = rng.choice(GRUDGE_INCIDENT_POOL)
        shared_fact = f"{incident}, between {name_a} and {name_b}."
        note_a = rng.choice(GRUDGE_AGGRIEVED_NOTE_POOL).format(**format_ctx("", sex_a))
        note_b = rng.choice(GRUDGE_ACCUSED_NOTE_POOL).format(**format_ctx("", sex_b))
    elif kind == "affection":
        kindness = rng.choice(AFFECTION_KINDNESS_POOL)
        shared_fact = f"{name_b} once {kindness}, for {name_a}."
        note_a = rng.choice(AFFECTION_GRATEFUL_NOTE_POOL).format(
            **format_ctx("", sex_a, target_name=name_b)
        )
        note_b = rng.choice(AFFECTION_BENEFACTOR_NOTE_POOL).format(
            **format_ctx("", sex_b, target_name=name_a)
        )
    elif kind == "rivalry":
        contest = rng.choice(RIVALRY_CONTEST_POOL)
        shared_fact = f"{name_a} and {name_b} {contest}."
        note_a = rng.choice(RIVALRY_NOTE_POOL).format(**format_ctx("", sex_a, target_name=name_b))
        note_b = rng.choice(RIVALRY_NOTE_POOL).format(**format_ctx("", sex_b, target_name=name_a))
    elif kind == "mentorship":
        skill = rng.choice(MENTORSHIP_SKILL_POOL).format(**format_ctx("", sex_a))
        shared_fact = f"{name_a} taught {name_b} {skill}."
        note_a = rng.choice(MENTOR_NOTE_POOL).format(**format_ctx("", sex_a, target_name=name_b))
        note_b = rng.choice(APPRENTICE_NOTE_POOL).format(
            **format_ctx("", sex_b, target_name=name_a)
        )
    else:
        bond = rng.choice(FRIENDSHIP_BOND_POOL)
        shared_fact = f"{name_a} and {name_b} {bond}."
        note_a = rng.choice(FRIEND_NOTE_POOL).format(**format_ctx("", sex_a, target_name=name_b))
        note_b = rng.choice(FRIEND_NOTE_POOL).format(**format_ctx("", sex_b, target_name=name_a))

    opinion_a = NeighbourOpinion(
        target_agent_id=agent_b,
        target_name=name_b,
        kind=kind,
        role=role_a,
        shared_fact=shared_fact,
        note=note_a,
        sim_amount=sim_amount,
    )
    opinion_b = NeighbourOpinion(
        target_agent_id=agent_a,
        target_name=name_a,
        kind=kind,
        role=role_b,
        shared_fact=shared_fact,
        note=note_b,
        sim_amount=sim_amount,
    )
    return opinion_a, opinion_b


def make_colleague_pair(
    rng: random.Random,
    agent_a: str,
    name_a: str,
    sex_a: Sex,
    agent_b: str,
    name_b: str,
    sex_b: Sex,
    workplace: str,
) -> tuple[NeighbourOpinion, NeighbourOpinion]:
    """Coworker edge for genesis business ties (construction crew, the
    grocery counter, the cafe counter) - same shared-fact-first
    construction as make_opinion_pair, kept separate because the fact
    text (workplace-anchored, not reason/incident-anchored) doesn't fit
    that function's per-kind branches cleanly.
    """
    shared_fact = f"{name_a} and {name_b} work together at {workplace}."
    note_a = rng.choice(COLLEAGUE_NOTE_POOL).format(**format_ctx("", sex_a, target_name=name_b))
    note_b = rng.choice(COLLEAGUE_NOTE_POOL).format(**format_ctx("", sex_b, target_name=name_a))
    role = RELATIONSHIP_ROLE_PAIRS["colleague"][0]
    opinion_a = NeighbourOpinion(
        target_agent_id=agent_b,
        target_name=name_b,
        kind="colleague",
        role=role,
        shared_fact=shared_fact,
        note=note_a,
    )
    opinion_b = NeighbourOpinion(
        target_agent_id=agent_a,
        target_name=name_a,
        kind="colleague",
        role=role,
        shared_fact=shared_fact,
        note=note_b,
    )
    return opinion_a, opinion_b


def make_minor_opinion_pair(
    rng: random.Random,
    agent_a: str,
    name_a: str,
    sex_a: Sex,
    agent_b: str,
    name_b: str,
    sex_b: Sex,
    kind: str,
) -> tuple[NeighbourOpinion, NeighbourOpinion]:
    role_a, role_b = RELATIONSHIP_ROLE_PAIRS[kind]
    if kind == "friendship":
        bond = rng.choice(MINOR_BOND_POOL)
        shared_fact = f"{name_a} and {name_b} {bond}."
        note_a = rng.choice(MINOR_FRIEND_NOTE_POOL).format(
            **format_ctx("", sex_a, target_name=name_b)
        )
        note_b = rng.choice(MINOR_FRIEND_NOTE_POOL).format(
            **format_ctx("", sex_b, target_name=name_a)
        )
    else:
        contest = rng.choice(MINOR_RIVAL_CONTEST_POOL)
        shared_fact = f"{name_a} and {name_b} {contest}."
        note_a = rng.choice(MINOR_RIVAL_NOTE_POOL).format(
            **format_ctx("", sex_a, target_name=name_b)
        )
        note_b = rng.choice(MINOR_RIVAL_NOTE_POOL).format(
            **format_ctx("", sex_b, target_name=name_a)
        )
    opinion_a = NeighbourOpinion(
        target_agent_id=agent_b, target_name=name_b, kind=kind, role=role_a,
        shared_fact=shared_fact, note=note_a,
    )
    opinion_b = NeighbourOpinion(
        target_agent_id=agent_a, target_name=name_a, kind=kind, role=role_b,
        shared_fact=shared_fact, note=note_b,
    )
    return opinion_a, opinion_b


def build_relationship_opinions(
    rng: random.Random,
    working_ids: list[str],
    elderly_ids: list[str],
    minor_ids: list[str],
    name_by_agent: dict[str, str],
    sex_by_id: dict[str, Sex],
    household_id_by_agent: dict[str, str],
    business_by_agent: dict[str, str | None],
    construction_owner_id: str,
    construction_hand_ids: list[str],
    grocery_owner_id: str,
    grocery_clerk_id: str,
    cafe_owner_id: str,
    barista_id: str,
) -> dict[str, list[NeighbourOpinion]]:
    """Builds the whole town's opinion graph in one pass: business
    coworker ties first (guaranteed, DESIGN.md's named businesses), then
    a coverage layer so every eligible adult has at least one relationship,
    then extra texture edges on top so some agents end up with two or
    three. Minors get a light, separate, age-appropriate layer. Every
    edge is generated as a single shared fact via make_opinion_pair/
    make_colleague_pair/make_minor_opinion_pair, so symmetry holds by
    construction (see module docstring).
    """
    opinions: dict[str, list[NeighbourOpinion]] = {aid: [] for aid in (*working_ids, *elderly_ids, *minor_ids)}
    used_pairs: set[frozenset[str]] = set()

    def add_pair(agent_a: str, agent_b: str, opinion_a: NeighbourOpinion, opinion_b: NeighbourOpinion) -> None:
        opinions[agent_a].append(opinion_a)
        opinions[agent_b].append(opinion_b)
        used_pairs.add(frozenset((agent_a, agent_b)))

    def already_linked(agent_a: str, agent_b: str) -> bool:
        if agent_a == agent_b:
            return True
        if frozenset((agent_a, agent_b)) in used_pairs:
            return True
        return household_id_by_agent[agent_a] == household_id_by_agent[agent_b]

    def edge(agent_a: str, agent_b: str, kind: str) -> None:
        opinion_a, opinion_b = make_opinion_pair(
            rng, agent_a, name_by_agent[agent_a], sex_by_id[agent_a],
            agent_b, name_by_agent[agent_b], sex_by_id[agent_b], kind,
        )
        add_pair(agent_a, agent_b, opinion_a, opinion_b)

    def minor_edge(agent_a: str, agent_b: str, kind: str) -> None:
        opinion_a, opinion_b = make_minor_opinion_pair(
            rng, agent_a, name_by_agent[agent_a], sex_by_id[agent_a],
            agent_b, name_by_agent[agent_b], sex_by_id[agent_b], kind,
        )
        add_pair(agent_a, agent_b, opinion_a, opinion_b)

    for hand_id in construction_hand_ids:
        if not already_linked(construction_owner_id, hand_id):
            opinion_a, opinion_b = make_colleague_pair(
                rng, construction_owner_id, name_by_agent[construction_owner_id],
                sex_by_id[construction_owner_id], hand_id, name_by_agent[hand_id],
                sex_by_id[hand_id], "Kasukabe Construction Co.",
            )
            add_pair(construction_owner_id, hand_id, opinion_a, opinion_b)
    if len(construction_hand_ids) >= 2 and not already_linked(
        construction_hand_ids[0], construction_hand_ids[1]
    ):
        a, b = construction_hand_ids[0], construction_hand_ids[1]
        opinion_a, opinion_b = make_colleague_pair(
            rng, a, name_by_agent[a], sex_by_id[a], b, name_by_agent[b], sex_by_id[b],
            "Kasukabe Construction Co.",
        )
        add_pair(a, b, opinion_a, opinion_b)
    for owner, employee, workplace in (
        (grocery_owner_id, grocery_clerk_id, "Kasukabe Grocery"),
        (cafe_owner_id, barista_id, "Kasukabe Cafe"),
    ):
        if not already_linked(owner, employee):
            opinion_a, opinion_b = make_colleague_pair(
                rng, owner, name_by_agent[owner], sex_by_id[owner], employee,
                name_by_agent[employee], sex_by_id[employee], workplace,
            )
            add_pair(owner, employee, opinion_a, opinion_b)

    adults = [*working_ids, *elderly_ids]
    shuffled_adults = list(adults)
    rng.shuffle(shuffled_adults)
    uncovered = [aid for aid in shuffled_adults if not opinions[aid]]
    i = 0
    while i < len(uncovered):
        a = uncovered[i]
        partner = None
        for j in range(i + 1, len(uncovered)):
            if not already_linked(a, uncovered[j]):
                partner = uncovered[j]
                uncovered.pop(j)
                break
        if partner is None:
            for candidate in shuffled_adults:
                if not already_linked(a, candidate):
                    partner = candidate
                    break
        if partner is not None:
            kind = rng.choice(ADULT_RELATIONSHIP_KINDS)
            edge(a, partner, kind)
        i += 1

    extra_candidates = list(adults)
    rng.shuffle(extra_candidates)
    extra_target_count = len(adults) // 2
    added = 0
    attempts = 0
    while added < extra_target_count and attempts < len(adults) * 4:
        attempts += 1
        a = rng.choice(extra_candidates)
        b = rng.choice(extra_candidates)
        if already_linked(a, b):
            continue
        kind = rng.choice(ADULT_RELATIONSHIP_KINDS)
        edge(a, b, kind)
        added += 1

    shuffled_minors = list(minor_ids)
    rng.shuffle(shuffled_minors)
    for idx, a in enumerate(shuffled_minors):
        if opinions[a]:
            continue
        partner = None
        for b in shuffled_minors[idx + 1 :]:
            if not already_linked(a, b):
                partner = b
                break
        if partner is not None:
            kind = rng.choice(MINOR_RELATIONSHIP_KINDS)
            minor_edge(a, partner, kind)

    return opinions


def pick_secret(
    rng: random.Random,
    sex: Sex,
    is_minor: bool,
    eligible_target_ids: list[str],
    name_by_agent: dict[str, str],
    self_id: str,
    household_id_by_agent: dict[str, str],
) -> tuple[str, str | None]:
    """A private hope or secret told to nobody (DESIGN.md's Personas
    section, last bullet). About a third of adult secrets name a real
    other agent - deliberately NOT written back onto that agent's own
    persona (that is what makes it a secret rather than an opinion) -
    test_genesis.py only checks the named target actually exists, never
    that it's reciprocated.
    """
    if is_minor:
        return rng.choice(MINOR_SECRET_POOL).format(**format_ctx("", sex)), None

    if eligible_target_ids and rng.random() < 0.35:
        candidates = [
            aid
            for aid in eligible_target_ids
            if aid != self_id and household_id_by_agent[aid] != household_id_by_agent[self_id]
        ]
        if candidates:
            target_id = rng.choice(candidates)
            target_name = name_by_agent[target_id]
            text = rng.choice(SECRET_ABOUT_NEIGHBOUR_POOL).format(
                **format_ctx("", sex, target_name=target_name)
            )
            return text, target_id

    return rng.choice(SECRET_POOL).format(**format_ctx("", sex)), None


GENESIS_TOWN_TOTAL_USD_CENTS = 100_000


def generate_genesis(seed: int, population: int = GENESIS_POPULATION) -> RichGenesis:
    rng = random.Random(seed)
    used_names: set[str] = set()

    minors_n, working_n, elderly_n = bracket_sizes(population)

    id_counter = 0

    def next_id() -> str:
        nonlocal id_counter
        id_counter += 1
        return f"a{id_counter:04d}"

    minor_ids = [next_id() for _ in range(minors_n)]
    working_ids = [next_id() for _ in range(working_n)]
    elderly_ids = [next_id() for _ in range(elderly_n)]
    all_ids = [*minor_ids, *working_ids, *elderly_ids]

    half = population // 2
    sex_deck = [Sex.MALE] * half + [Sex.FEMALE] * (population - half)
    rng.shuffle(sex_deck)
    sex_by_id = dict(zip(all_ids, sex_deck))

    elderly_groups = pair_up(rng, elderly_ids, ELDERLY_COUPLE_FRACTION)
    working_groups = pair_up(rng, working_ids, WORKING_COUPLE_FRACTION)
    working_couples = [g for g in working_groups if len(g) == 2]

    child_counts = distribute_children(rng, len(minor_ids), len(working_couples))
    shuffled_minor_ids = list(minor_ids)
    rng.shuffle(shuffled_minor_ids)
    children_by_couple: dict[int, list[str]] = {}
    cursor = 0
    for i, count in enumerate(child_counts):
        children_by_couple[i] = shuffled_minor_ids[cursor : cursor + count]
        cursor += count

    household_id_by_agent: dict[str, str] = {}
    spouse_by_agent: dict[str, str] = {}
    parents_by_agent: dict[str, list[str]] = {aid: [] for aid in all_ids}
    children_by_agent: dict[str, list[str]] = {aid: [] for aid in all_ids}
    surname_by_agent: dict[str, str] = {}
    household_records: list[Household] = []

    def new_household(member_ids: list[str]) -> str:
        household_id = f"h{len(household_records) + 1:04d}"
        household_records.append(Household(household_id=household_id, member_ids=list(member_ids)))
        for member in member_ids:
            household_id_by_agent[member] = household_id
        return household_id

    for group in elderly_groups:
        surname = rng.choice(SURNAMES)
        for member in group:
            surname_by_agent[member] = surname
        new_household(group)
        if len(group) == 2:
            spouse_by_agent[group[0]] = group[1]
            spouse_by_agent[group[1]] = group[0]

    working_couple_index = 0
    for group in working_groups:
        surname = rng.choice(SURNAMES)
        for member in group:
            surname_by_agent[member] = surname
        if len(group) == 2:
            kids = children_by_couple.get(working_couple_index, [])
            for kid in kids:
                surname_by_agent[kid] = surname
                parents_by_agent[kid] = list(group)
            household_members = [*group, *kids]
            new_household(household_members)
            spouse_by_agent[group[0]] = group[1]
            spouse_by_agent[group[1]] = group[0]
            children_by_agent[group[0]] = list(kids)
            children_by_agent[group[1]] = list(kids)
            working_couple_index += 1
        else:
            new_household(group)

    unassigned_minors = [m for m in minor_ids if m not in household_id_by_agent]
    if unassigned_minors:
        raise RuntimeError(
            f"genesis generation left {len(unassigned_minors)} minors without a household"
        )

    available_working = list(working_ids)
    rng.shuffle(available_working)

    def take(n: int) -> list[str]:
        taken, remaining = available_working[:n], available_working[n:]
        available_working[:] = remaining
        return taken

    construction_owner_id, *construction_hand_ids = take(1 + CONSTRUCTION_HANDS_COUNT)
    grocery_owner_id, grocery_clerk_id = take(2)
    cafe_owner_id, barista_id = take(2)
    (pharmacist_id,) = take(1)

    occupation_by_agent: dict[str, str] = {}
    business_by_agent: dict[str, str | None] = {}

    construction_company = ConstructionCompany(
        business_id="b_construction",
        name="Kasukabe Construction Co.",
        owner_agent_id=construction_owner_id,
        hand_agent_ids=list(construction_hand_ids),
        starting_sim_balance=GENESIS_BUSINESS_STARTING_SIM_BALANCE,
    )
    occupation_by_agent[construction_owner_id] = "construction_company_owner"
    business_by_agent[construction_owner_id] = construction_company.business_id
    for hand_id in construction_hand_ids:
        occupation_by_agent[hand_id] = "construction_worker"
        business_by_agent[hand_id] = construction_company.business_id

    grocery_store = SmallBusiness(
        business_id="b_grocery",
        name="Kasukabe Grocery",
        business_type="grocery_store",
        owner_agent_id=grocery_owner_id,
        employee_agent_ids=[grocery_clerk_id],
        starting_sim_balance=GENESIS_BUSINESS_STARTING_SIM_BALANCE,
    )
    occupation_by_agent[grocery_owner_id] = "grocery_store_owner"
    business_by_agent[grocery_owner_id] = grocery_store.business_id
    occupation_by_agent[grocery_clerk_id] = "grocery_clerk"
    business_by_agent[grocery_clerk_id] = grocery_store.business_id

    cafe = SmallBusiness(
        business_id="b_cafe",
        name="Kasukabe Cafe",
        business_type="cafe",
        owner_agent_id=cafe_owner_id,
        employee_agent_ids=[barista_id],
        starting_sim_balance=GENESIS_BUSINESS_STARTING_SIM_BALANCE,
    )
    occupation_by_agent[cafe_owner_id] = "cafe_owner"
    business_by_agent[cafe_owner_id] = cafe.business_id
    occupation_by_agent[barista_id] = "barista"
    business_by_agent[barista_id] = cafe.business_id

    pharmacy = SmallBusiness(
        business_id="b_pharmacy",
        name="Kasukabe Pharmacy",
        business_type="pharmacy",
        owner_agent_id=pharmacist_id,
        employee_agent_ids=[],
        starting_sim_balance=GENESIS_BUSINESS_STARTING_SIM_BALANCE,
    )
    occupation_by_agent[pharmacist_id] = "pharmacist"
    business_by_agent[pharmacist_id] = pharmacy.business_id

    for agent_id in available_working:
        occupation_by_agent[agent_id] = rng.choice(GENERIC_OCCUPATIONS)
        business_by_agent[agent_id] = None

    control_businesses = build_control_businesses()

    ablation_summary = ablation_counts(population)
    shuffled_all_ids = list(all_ids)
    rng.shuffle(shuffled_all_ids)
    ablation_by_agent: dict[str, AblationArm] = {}
    cursor = 0
    for arm, count in (
        (AblationArm.FULL, ablation_summary.full),
        (AblationArm.NO_REFLECTION, ablation_summary.no_reflection),
        (AblationArm.NO_LEARNING, ablation_summary.no_learning),
    ):
        for agent_id in shuffled_all_ids[cursor : cursor + count]:
            ablation_by_agent[agent_id] = arm
        cursor += count

    age_by_agent: dict[str, int] = {}
    name_by_agent: dict[str, str] = {}
    traits_by_agent: dict[str, list[str]] = {}
    habits_by_agent: dict[str, list[str]] = {}
    persona_core_by_agent: dict[str, dict] = {}

    def build_persona_core(agent_id: str, sex: Sex, age: int, occupation: str, is_minor: bool) -> dict:
        name = name_by_agent[agent_id]
        category = OCCUPATION_TO_CATEGORY[occupation]
        internal_tension, tension_description = pick_tension(rng, name, sex, is_minor)
        formative_history = pick_formative_history(rng, name, sex, age, category)
        values, will_not_compromise = pick_values(rng, sex, is_minor)
        fear = pick_fear(rng, sex, is_minor)
        ambition = pick_ambition(rng, sex, is_minor)
        scarcity_behavior = pick_scarcity_behavior(rng, sex, is_minor)
        stranger_treatment = pick_stranger_treatment(rng, sex, is_minor)
        family_treatment = pick_family_treatment(rng, sex, is_minor)
        competence, blind_spot = pick_competence_blind_spot(rng, sex, category)
        speech_register, verbal_habits = pick_speech(rng, sex, is_minor)
        daily_rhythm = pick_daily_rhythm(rng, sex, category)
        attitude_money, attitude_risk, attitude_authority, attitude_religion = pick_attitudes(
            rng, sex
        )
        return {
            "internal_tension": internal_tension,
            "tension_description": tension_description,
            "formative_history": formative_history,
            "values": values,
            "will_not_compromise": will_not_compromise,
            "fears": fear,
            "ambitions": ambition,
            "scarcity_behavior": scarcity_behavior,
            "stranger_treatment": stranger_treatment,
            "family_treatment": family_treatment,
            "competence": competence,
            "blind_spot": blind_spot,
            "speech_register": speech_register,
            "verbal_habits": verbal_habits,
            "daily_rhythm": daily_rhythm,
            "attitude_money": attitude_money,
            "attitude_risk": attitude_risk,
            "attitude_authority": attitude_authority,
            "attitude_religion": attitude_religion,
        }

    for agent_id in working_ids:
        age = rng.randint(WORKING_AGE_MIN, WORKING_AGE_MAX)
        sex = sex_by_id[agent_id]
        surname = surname_by_agent.setdefault(agent_id, rng.choice(SURNAMES))
        name = unique_name(rng, sex, surname, used_names)
        occupation = occupation_by_agent[agent_id]
        if occupation == "college_student" and age > 24:
            occupation = "day_laborer"
            occupation_by_agent[agent_id] = occupation
        age_by_agent[agent_id] = age
        name_by_agent[agent_id] = name
        traits_by_agent[agent_id] = rng.sample(PERSONALITY_TRAITS, 2)
        habits_by_agent[agent_id] = rng.sample(HABITS, 2)
        persona_core_by_agent[agent_id] = build_persona_core(
            agent_id, sex, age, occupation, is_minor=False
        )

    for agent_id in elderly_ids:
        age = rng.randint(ELDERLY_AGE_MIN, ELDERLY_AGE_MAX)
        sex = sex_by_id[agent_id]
        surname = surname_by_agent.setdefault(agent_id, rng.choice(SURNAMES))
        name = unique_name(rng, sex, surname, used_names)
        occupation_by_agent[agent_id] = ELDERLY_OCCUPATION
        business_by_agent[agent_id] = None
        age_by_agent[agent_id] = age
        name_by_agent[agent_id] = name
        traits_by_agent[agent_id] = rng.sample(PERSONALITY_TRAITS, 2)
        habits_by_agent[agent_id] = rng.sample(HABITS, 2)
        persona_core_by_agent[agent_id] = build_persona_core(
            agent_id, sex, age, ELDERLY_OCCUPATION, is_minor=False
        )

    for agent_id in minor_ids:
        age = rng.randint(MINOR_AGE_MIN, MINOR_AGE_MAX)
        sex = sex_by_id[agent_id]
        surname = surname_by_agent.setdefault(agent_id, rng.choice(SURNAMES))
        name = unique_name(rng, sex, surname, used_names)
        occupation = (
            MINOR_OCCUPATION_YOUNG if age < MINOR_SCHOOL_AGE_YEARS else MINOR_OCCUPATION_SCHOOL
        )
        occupation_by_agent[agent_id] = occupation
        business_by_agent[agent_id] = None
        age_by_agent[agent_id] = age
        name_by_agent[agent_id] = name
        traits_by_agent[agent_id] = rng.sample(PERSONALITY_TRAITS, 2)
        habits_by_agent[agent_id] = rng.sample(HABITS, 2)
        persona_core_by_agent[agent_id] = build_persona_core(
            agent_id, sex, age, occupation, is_minor=True
        )

    staff_ids: list[str] = []
    staff_household_seq = 1
    for _zone, occupation, count in INSTITUTION_STAFF:
        for _ in range(count):
            agent_id = f"s{len(staff_ids) + 1:04d}"
            staff_ids.append(agent_id)
            sex = Sex.MALE if rng.random() < 0.5 else Sex.FEMALE
            age = rng.randint(WORKING_AGE_MIN, WORKING_AGE_MAX)
            household_id = f"h9{staff_household_seq:03d}"
            staff_household_seq += 1

            sex_by_id[agent_id] = sex
            age_by_agent[agent_id] = age
            name_by_agent[agent_id] = unique_name(
                rng, sex, rng.choice(SURNAMES), used_names
            )
            occupation_by_agent[agent_id] = occupation
            parents_by_agent[agent_id] = []
            children_by_agent[agent_id] = []
            household_id_by_agent[agent_id] = household_id
            household_records.append(
                Household(household_id=household_id, member_ids=[agent_id])
            )
            traits_by_agent[agent_id] = rng.sample(PERSONALITY_TRAITS, 2)
            habits_by_agent[agent_id] = rng.sample(HABITS, 2)
            ablation_by_agent[agent_id] = AblationArm.FULL
            persona_core_by_agent[agent_id] = build_persona_core(
                agent_id, sex, age, occupation, is_minor=False
            )

    opinions_by_agent = build_relationship_opinions(
        rng,
        [*working_ids, *staff_ids],
        elderly_ids,
        minor_ids,
        name_by_agent,
        sex_by_id,
        household_id_by_agent,
        business_by_agent,
        construction_owner_id,
        construction_hand_ids,
        grocery_owner_id,
        grocery_clerk_id,
        cafe_owner_id,
        barista_id,
    )

    adult_ids = [*working_ids, *elderly_ids, *staff_ids]
    secret_by_agent: dict[str, str] = {}
    secret_target_by_agent: dict[str, str | None] = {}
    for agent_id in (*working_ids, *elderly_ids, *staff_ids):
        text, target_id = pick_secret(
            rng, sex_by_id[agent_id], False, adult_ids, name_by_agent, agent_id, household_id_by_agent
        )
        secret_by_agent[agent_id] = text
        secret_target_by_agent[agent_id] = target_id
    for agent_id in minor_ids:
        text, target_id = pick_secret(
            rng, sex_by_id[agent_id], True, adult_ids, name_by_agent, agent_id, household_id_by_agent
        )
        secret_by_agent[agent_id] = text
        secret_target_by_agent[agent_id] = target_id

    genesis_agents: list[RichGenesisAgent] = []

    def assemble(agent_id: str, is_minor: bool) -> RichGenesisAgent:
        sex = sex_by_id[agent_id]
        age = age_by_agent[agent_id]
        name = name_by_agent[agent_id]
        occupation = occupation_by_agent[agent_id]
        spouse_id = spouse_by_agent.get(agent_id)
        spouse_name = name_by_agent[spouse_id] if spouse_id else None
        kids = children_by_agent.get(agent_id, [])
        parent_ids = parents_by_agent[agent_id] if is_minor else []
        parent_names = [name_by_agent[pid] for pid in parent_ids]
        core = persona_core_by_agent[agent_id]

        bio = build_bio(
            name,
            age,
            sex,
            occupation,
            habits_by_agent[agent_id][0],
            spouse_name,
            len(kids),
            is_minor,
            parent_names,
            core["formative_history"],
            core["tension_description"],
            core["values"][0],
            core["fears"],
        )

        persona = PersonaDepth(
            internal_tension=core["internal_tension"],
            tension_description=core["tension_description"],
            formative_history=core["formative_history"],
            values=core["values"],
            will_not_compromise=core["will_not_compromise"],
            fears=core["fears"],
            ambitions=core["ambitions"],
            scarcity_behavior=core["scarcity_behavior"],
            stranger_treatment=core["stranger_treatment"],
            family_treatment=core["family_treatment"],
            competence=core["competence"],
            blind_spot=core["blind_spot"],
            speech_register=core["speech_register"],
            verbal_habits=core["verbal_habits"],
            daily_rhythm=core["daily_rhythm"],
            attitude_money=core["attitude_money"],
            attitude_risk=core["attitude_risk"],
            attitude_authority=core["attitude_authority"],
            attitude_religion=core["attitude_religion"],
            secret=secret_by_agent[agent_id],
            secret_target_agent_id=secret_target_by_agent[agent_id],
            opinions=opinions_by_agent[agent_id],
        )

        return RichGenesisAgent(
            agent_id=agent_id,
            name=name,
            age_years=float(age),
            sex=sex,
            household_id=household_id_by_agent[agent_id],
            marital_status=MaritalStatus.MARRIED if spouse_id else MaritalStatus.SINGLE,
            spouse_id=spouse_id,
            parent_ids=parent_ids,
            children_ids=list(kids),
            sponsor_parent_id=(parent_ids[0] if is_minor and parent_ids else None),
            occupation=occupation,
            business_id=business_by_agent.get(agent_id),
            personality_traits=traits_by_agent[agent_id],
            habits=habits_by_agent[agent_id],
            bio=bio,
            ablation_arm=ablation_by_agent[agent_id],
            usd_wallet_cents=GENESIS_AGENT_WALLET_USD_CENTS,
            starting_sim_balance=GENESIS_AGENT_STARTING_SIM_BALANCE,
            persona=persona,
        )

    for agent_id in working_ids:
        genesis_agents.append(assemble(agent_id, is_minor=False))
    for agent_id in elderly_ids:
        genesis_agents.append(assemble(agent_id, is_minor=False))
    for agent_id in minor_ids:
        genesis_agents.append(assemble(agent_id, is_minor=True))
    for agent_id in staff_ids:
        genesis_agents.append(assemble(agent_id, is_minor=False))

    wallets_cents = len(genesis_agents) * GENESIS_AGENT_WALLET_USD_CENTS
    treasury_cents = GENESIS_TOWN_TOTAL_USD_CENTS - wallets_cents
    if treasury_cents < 0:
        raise ValueError(
            f"population {population} x {GENESIS_AGENT_WALLET_USD_CENTS} cents "
            f"exceeds the town's {GENESIS_TOWN_TOTAL_USD_CENTS}-cent total - "
            "there is no treasury left to fund anything"
        )
    usd_ledger = UsdLedgerSummary(
        total_cents=GENESIS_TOWN_TOTAL_USD_CENTS,
        agent_wallets_cents=wallets_cents,
        treasury_cents=treasury_cents,
    )

    return RichGenesis(
        seed=seed,
        generated_at=datetime.now(UTC).isoformat(),
        population=len(genesis_agents),
        usd_ledger=usd_ledger,
        agents=genesis_agents,
        households=household_records,
        construction_company=construction_company,
        small_businesses=[grocery_store, cafe, pharmacy],
        control_businesses=control_businesses,
        ablation_summary=AblationSummary(
            full=sum(1 for a in genesis_agents if a.ablation_arm is AblationArm.FULL),
            no_reflection=sum(
                1 for a in genesis_agents if a.ablation_arm is AblationArm.NO_REFLECTION
            ),
            no_learning=sum(
                1 for a in genesis_agents if a.ablation_arm is AblationArm.NO_LEARNING
            ),
            deepseek_locked=sum(
                1 for a in genesis_agents if a.ablation_arm is AblationArm.DEEPSEEK_LOCKED
            ),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate genesis.json for Kasukabe.")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--population", type=int, default=GENESIS_POPULATION)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent.parent / "genesis.json"
    )
    args = parser.parse_args()

    genesis = generate_genesis(seed=args.seed, population=args.population)
    write_genesis_json(genesis, args.output)
    print(f"wrote {len(genesis.agents)} agents to {args.output}")


if __name__ == "__main__":
    main()
