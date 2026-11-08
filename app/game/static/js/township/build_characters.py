"""Build the township viewer's character data from world/genesis.json.

Writes three things:

  1. data/characters/<agent_id>.json   - one complete file per resident (250)
  2. data/characters/index.json        - a light roster index over all of them
  3. js/township/agents.js             - the same records bundled as a JS
                                         constant, because township.html is
                                         openable straight off disk and
                                         fetch() cannot read file:// JSON

Three things are computed here rather than copied:

**Short display names.** Genesis names ("Jovana Ravensworth") are hard to scan
on a map holding 250 of them. Each resident gets one short unique name (`n`),
households sharing a surname so families read as families. The genesis name is
kept verbatim as `rn` and shown on every card, so nothing is lost.

**The kinship graph.** genesis.json records only three edges - `spouse_id`,
`parent_ids`, `children_ids` - so father/mother/son/daughter/husband/wife and
sibling all fall out directly, and those are marked `"genesis"`. Everything
further out (grandparents, uncles, aunts, cousins, in-laws) has no basis in
that file: only 38 residents have a recorded parent, and no adult has a
recorded parent, so there is literally no second generation to read an uncle
from. Rather than leave those blank, this script builds a deterministic
lineage layer joining households into extended families, and marks every edge
it invents `"inferred"`. Both kinds are kept in separate lists and the page
labels them differently - an inferred uncle must never be mistaken for a fact
that came out of genesis.

**The memory stream.** Formatted per Park et al., "Generative Agents:
Interactive Simulacra of Human Behavior" (UIST '23) - a flat stream of
timestamped natural-language entries, each with a type (observation /
reflection / plan), an importance score, and pointers to the entries it was
derived from. The paper seeds each agent with a set of day-0 memories before
the simulation starts; these are exactly that: seeded from the resident's real
persona, occupation, and relationships. Importance is assigned by rule here
rather than LLM-rated as in the paper, and the file says so in its own
`memory.scoring` field.

Run from app/:  python3 game/static/js/township/build_characters.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parents[1]
APP_ROOT = HERE.parents[3]
GENESIS = APP_ROOT / "world" / "genesis.json"
CHAR_DIR = STATIC / "data" / "characters"
AGENTS_JS = HERE / "agents.js"

# First names are picked from the pool matching the resident's real `sex`
# field. They have to agree: the kinship labels below ("mother", "uncle") are
# generated from that same field, so a female agent handed a male-reading name
# produces cards that read "mother Arlo Vance" and "aunt Leo Jost".
FIRST_MALE = [
    "Ben", "Dan", "Finn", "Hugo", "Jack", "Leo", "Noah", "Sam", "Wes", "Yuri",
    "Amos", "Cole", "Eli", "Gus", "Ivan", "Jem", "Kai", "Milo", "Omar", "Silas",
    "Toby", "Vince", "Zeke", "Arlo", "Cyrus", "Enzo", "Gale", "Jonah", "Kit", "Lars",
    "Otto", "Reid", "Tom", "Bram", "Dev", "Fritz", "Hal", "Jude", "Klaus", "Marek",
    "Odin", "Pim", "Rex", "Theo", "Zane", "Bo", "Emil", "Gil", "Ike", "Kurt",
    "Mac", "Abel", "Cato", "Dorian", "Ezra", "Felix", "Gideon", "Hector", "Ira", "Joel",
]
FIRST_FEMALE = [
    "Ana", "Cara", "Ella", "Gia", "Iris", "Kira", "Mia", "Olive", "Pia", "Rosa",
    "Tara", "Umi", "Vera", "Xena", "Zara", "Bree", "Dara", "Faye", "Hana", "Lena",
    "Nina", "Pearl", "Rhea", "Uma", "Willa", "Yara", "Bess", "Dot", "Freya", "Hettie",
    "Ida", "Maud", "Nell", "Poppy", "Sela", "Ruth", "Wren", "Ada", "Cleo", "Edda",
    "Greta", "Ines", "Lila", "Neve", "Suri", "Vida", "Wilma", "Dora", "Fern", "Hilda",
    "Jana", "Lore", "Nora", "Beatrix", "Clara", "Delia", "Esme", "Flora", "Hazel", "Junia",
]
LAST = [
    "Ash", "Barlow", "Crane", "Dunn", "East", "Fowler", "Grave", "Hale", "Ingle",
    "Jost", "Kemp", "Lark", "Mead", "North", "Oak", "Pike", "Quill", "Rook", "Stone",
    "Thorn", "Underhill", "Vale", "Wick", "Yates", "Ziegler", "Bell", "Cobb", "Dell",
    "Frost", "Gates", "Hart", "Ives", "Kite", "Lamb", "Moss", "Nash", "Owens", "Penn",
    "Reed", "Snow", "Tate", "Vance", "Ward", "Yule", "Brook", "Coop", "Drew", "Ford",
    "Gray", "Holt",
]

# Zone ids must match ZONES in village.js exactly - an occupation pointing at a
# building that does not exist would strand that resident off the map.
WORK_ZONE = {
    "artisan": "tradeoffices", "office_administrator": "tradeoffices",
    "trader": "marketrow", "day_laborer": "marketrow",
    "government_clerk": "government", "pharmacist": "pharmacy",
    "grocery_store_owner": "grocery", "grocery_clerk": "grocery",
    "cafe_owner": "cafe", "barista": "cafe",
    "construction_company_owner": "construction", "construction_worker": "construction",
    "farmer": "farmland", "miner": "oreoutcrop", "forester": "forest",
    "college_instructor": "college", "college_student": "college",
    # Institution staff (see INSTITUTION_STAFF below).
    "doctor": "hospital", "nurse": "hospital",
    "police_officer": "police", "firefighter": "fire",
    "baker": "bakery", "chef": "restaurant", "waiter": "restaurant",
    "school_teacher": "school", "librarian": "library",
    "banker": "bank", "innkeeper": "inn",
    "street_sweeper": "townsquare", "shopkeeper": "marketrow",
}

WORKPLACE_NAME = {
    "tradeoffices": "Trade Offices", "marketrow": "Market Row",
    "government": "Government Complex", "pharmacy": "Kasukabe Pharmacy",
    "grocery": "Kasukabe Grocery", "cafe": "Kasukabe Cafe",
    "construction": "Construction Yard", "farmland": "Farmland",
    "oreoutcrop": "Ore Outcrop", "forest": "Forest",
    "college": "Oak Hill College", "park": "Town Park",
    "hospital": "Hospital", "police": "Police Station", "fire": "Fire Station",
    "bakery": "Bakery", "restaurant": "Restaurant", "school": "Town School",
    "library": "Library", "bank": "Town Bank", "inn": "The Inn",
    "townsquare": "Town Square",
}

SKILL_BASE = {
    "artisan": "Crafting", "barista": "Brewing", "cafe_owner": "Hospitality",
    "child": "Play", "college_instructor": "Teaching", "college_student": "Research",
    "construction_company_owner": "Site Management", "construction_worker": "Construction",
    "day_laborer": "General Labor", "farmer": "Farming", "forester": "Foresting",
    "government_clerk": "Civic Administration", "grocery_clerk": "Retail",
    "grocery_store_owner": "Retail Management", "miner": "Mining",
    "office_administrator": "Administration", "pharmacist": "Pharmacy",
    "retired": "Mentorship", "student": "Studying", "trader": "Negotiation",
    "doctor": "Medicine", "nurse": "Nursing", "police_officer": "Policing",
    "firefighter": "Firefighting", "baker": "Baking", "chef": "Cooking",
    "waiter": "Table Service", "school_teacher": "Teaching",
    "librarian": "Records", "banker": "Banking", "innkeeper": "Innkeeping",
    "street_sweeper": "Street Cleaning", "shopkeeper": "Shopkeeping",
}
SKILL_POOL = {
    "artisan": ["Fine Detailing", "Tool Repair", "Pattern Design"],
    "trader": ["Price Forecasting", "Bulk Bargaining", "Route Trading"],
    "day_laborer": ["Load Hauling", "Quick Hire"],
    "farmer": ["Crop Rotation", "Irrigation"],
    "miner": ["Vein Reading", "Deep Digging"],
    "forester": ["Timber Grading", "Trail Clearing"],
    "college_instructor": ["Curriculum Design", "Mentoring"],
    "college_student": ["Speed Reading", "Thesis Writing"],
    "office_administrator": ["Records Auditing", "Scheduling"],
    "government_clerk": ["Tax Auditing", "Petition Review"],
    "construction_worker": ["Scaffold Rigging", "Masonry"],
    "construction_company_owner": ["Site Bidding", "Crew Leadership"],
    "grocery_store_owner": ["Inventory Forecasting"], "grocery_clerk": ["Fast Checkout"],
    "pharmacist": ["Compounding"], "cafe_owner": ["Menu Design"], "barista": ["Latte Art"],
    "retired": ["Storytelling", "Genealogy"], "student": ["Note-Taking"],
    "child": ["Imaginative Play"],
    "doctor": ["Diagnosis", "Surgery"], "nurse": ["Triage", "Ward Rounds"],
    "police_officer": ["Interviewing", "Case Filing"], "firefighter": ["Ladder Work", "Rescue"],
    "baker": ["Sourdough", "Pastry"], "chef": ["Menu Design", "Butchery"],
    "waiter": ["Upselling", "Silver Service"], "school_teacher": ["Lesson Planning", "Reading Groups"],
    "librarian": ["Cataloguing", "Archive Search"], "banker": ["Credit Assessment", "Bookkeeping"],
    "innkeeper": ["Housekeeping", "Brewing"], "street_sweeper": ["Route Planning"],
    "shopkeeper": ["Stock Rotation", "Haggling"],
}

# Real fiscal constants, world/ledger/head_tax.py + world/life/constants.py.
HEAD_TAX_BASE_SIM = 1_000
TAX_RATE_BPS = 300  # illustrative rate; genesis default is 0, government amends it


def stable(text: str, salt: str) -> int:
    """A stable hash. Python's hash() is randomised per process for str, so
    every seeded choice here would otherwise change between runs and the same
    genesis file would produce different names, memories, and families."""
    h = 2166136261
    for ch in f"{text}|{salt}":
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def pick(seq, text: str, salt: str):
    return seq[stable(text, salt) % len(seq)]


def bracket(occupation: str) -> str:
    if occupation in ("child", "student"):
        return "minor"
    if occupation == "retired":
        return "elderly"
    return "working"


def day_zone(occupation: str) -> str | None:
    if occupation == "child":
        return "park"
    if occupation == "student":
        return "college"
    if occupation == "retired":
        return None
    return WORK_ZONE.get(occupation, "tradeoffices")


def first_clause(bio: str, limit: int = 140) -> str:
    if not bio:
        return ""
    opening = bio.split(". ")[0]
    if len(opening) > limit:
        return opening[:limit].rsplit(" ", 1)[0] + "…"
    return opening + "."


def assign_names(agents: list[dict]) -> dict[str, str]:
    """One short unique name per resident, first name matching their sex and
    surname shared across the household, so families read as families."""
    household_ids = sorted({a["household_id"] for a in agents})
    surname = {hh: LAST[i % len(LAST)] for i, hh in enumerate(household_ids)}
    used: set[str] = set()
    names: dict[str, str] = {}
    for agent in sorted(agents, key=lambda a: a["agent_id"]):
        last = surname[agent["household_id"]]
        pool = FIRST_MALE if agent["sex"] == "male" else FIRST_FEMALE
        for step in range(len(pool) * 4):
            first = pool[(stable(agent["agent_id"], "name") + step) % len(pool)]
            candidate = f"{first} {last}"
            if candidate not in used:
                used.add(candidate)
                names[agent["agent_id"]] = candidate
                break
        else:  # pragma: no cover
            names[agent["agent_id"]] = f"{agent['agent_id']} {last}"
    return names


# --------------------------------------------------------------------------
# Kinship
# --------------------------------------------------------------------------

def gendered(male: str, female: str, sex: str) -> str:
    return male if sex == "male" else female


def build_genesis_relations(agents: list[dict], by_id: dict) -> dict[str, list[dict]]:
    """Every kinship edge that genesis.json actually states, plus siblings,
    which follow necessarily from two agents sharing a parent."""
    rel: dict[str, list[dict]] = {a["agent_id"]: [] for a in agents}

    def add(subject: str, other: str, label: str) -> None:
        if other in by_id and not any(r["agent_id"] == other and r["relation"] == label for r in rel[subject]):
            rel[subject].append({"relation": label, "agent_id": other, "source": "genesis"})

    for a in agents:
        aid, sex = a["agent_id"], a["sex"]
        spouse = a.get("spouse_id")
        if spouse:
            add(aid, spouse, gendered("husband", "wife", by_id[spouse]["sex"]))
        for pid in a.get("parent_ids") or []:
            add(aid, pid, gendered("father", "mother", by_id[pid]["sex"]))
            add(pid, aid, gendered("son", "daughter", sex))
        for cid in a.get("children_ids") or []:
            add(aid, cid, gendered("son", "daughter", by_id[cid]["sex"]))
            add(cid, aid, gendered("father", "mother", sex))

    # Siblings: anyone sharing at least one parent.
    kids_of: dict[str, list[str]] = {}
    for a in agents:
        for pid in a.get("parent_ids") or []:
            kids_of.setdefault(pid, []).append(a["agent_id"])
    for sibs in kids_of.values():
        for x in sibs:
            for y in sibs:
                if x != y:
                    add(x, y, gendered("brother", "sister", by_id[y]["sex"]))
    return rel


def build_extended_relations(agents, by_id, genesis_rel) -> dict[str, list[dict]]:
    """A deterministic lineage layer, so grandparents/uncles/cousins exist.

    genesis.json has exactly one generation of parent links, so nobody has a
    recorded grandparent and no adult has a recorded sibling - which means no
    uncle, aunt, or cousin can be derived from it at all. This builds that
    missing upper generation: elderly residents are seated as the town's
    grandparents and adopted as parents of working-age residents, which then
    yields adult siblings, and from those, uncles/aunts/nephews/nieces/cousins.

    Every edge produced here is marked "inferred" and kept in its own list.
    It is a plausible, consistent family tree for the town - not a fact read
    out of genesis.
    """
    ext: dict[str, list[dict]] = {a["agent_id"]: [] for a in agents}

    def add(subject: str, other: str, label: str) -> None:
        if subject == other:
            return
        if any(r["agent_id"] == other for r in genesis_rel[subject]):
            return  # never shadow a real genesis edge
        if not any(r["agent_id"] == other and r["relation"] == label for r in ext[subject]):
            ext[subject].append({"relation": label, "agent_id": other, "source": "inferred"})

    elders = sorted([a["agent_id"] for a in agents if bracket(a["occupation"]) == "elderly"])
    adults = sorted([a["agent_id"] for a in agents if bracket(a["occupation"]) == "working"])
    if not elders:
        return ext

    # Seat each elder as the head of a lineage, then distribute adults across
    # those lineages as their children. 38 elders over 174 adults gives
    # families of about four or five siblings.
    lineage: dict[str, list[str]] = {e: [] for e in elders}
    for i, adult in enumerate(adults):
        lineage[elders[i % len(elders)]].append(adult)

    inferred_parents: dict[str, list[str]] = {}
    for elder, children in lineage.items():
        esex = by_id[elder]["sex"]
        for child in children:
            add(child, elder, gendered("father", "mother", esex))
            add(elder, child, gendered("son", "daughter", by_id[child]["sex"]))
            inferred_parents.setdefault(child, []).append(elder)
        # Adult siblings within one lineage.
        for x in children:
            for y in children:
                if x != y:
                    add(x, y, gendered("brother", "sister", by_id[y]["sex"]))

    # An elder's grandchildren: the genesis children of their inferred children.
    for elder, children in lineage.items():
        esex = by_id[elder]["sex"]
        for child in children:
            for grandchild in by_id[child].get("children_ids") or []:
                if grandchild in by_id:
                    add(grandchild, elder, gendered("grandfather", "grandmother", esex))
                    add(elder, grandchild, gendered("grandson", "granddaughter", by_id[grandchild]["sex"]))

    # Uncles/aunts and nephews/nieces: a parent's sibling, by blood or marriage.
    for a in agents:
        aid = a["agent_id"]
        for pid in a.get("parent_ids") or []:
            if pid not in by_id:
                continue
            for sib in [r["agent_id"] for r in ext[pid] if r["relation"] in ("brother", "sister")]:
                add(aid, sib, gendered("uncle", "aunt", by_id[sib]["sex"]))
                add(sib, aid, gendered("nephew", "niece", a["sex"]))
                # The sibling's spouse is an uncle/aunt by marriage.
                sib_spouse = by_id[sib].get("spouse_id")
                if sib_spouse in by_id:
                    add(aid, sib_spouse, gendered("uncle", "aunt", by_id[sib_spouse]["sex"]))
                    add(sib_spouse, aid, gendered("nephew", "niece", a["sex"]))

    # Cousins: children of a parent's sibling.
    for a in agents:
        aid = a["agent_id"]
        for pid in a.get("parent_ids") or []:
            if pid not in by_id:
                continue
            for sib in [r["agent_id"] for r in ext[pid] if r["relation"] in ("brother", "sister")]:
                for cousin in by_id[sib].get("children_ids") or []:
                    if cousin in by_id and cousin != aid:
                        add(aid, cousin, "cousin")
                        add(cousin, aid, "cousin")

    # In-laws through marriage.
    for a in agents:
        aid = a["agent_id"]
        spouse = a.get("spouse_id")
        if spouse not in by_id:
            continue
        for pid in inferred_parents.get(spouse, []):
            add(aid, pid, gendered("father-in-law", "mother-in-law", by_id[pid]["sex"]))
            add(pid, aid, gendered("son-in-law", "daughter-in-law", a["sex"]))
        for r in ext[spouse]:
            if r["relation"] in ("brother", "sister"):
                add(aid, r["agent_id"], gendered("brother-in-law", "sister-in-law", by_id[r["agent_id"]]["sex"]))
                add(r["agent_id"], aid, gendered("brother-in-law", "sister-in-law", a["sex"]))
    return ext


# --------------------------------------------------------------------------
# Memory stream (Park et al., UIST '23 format)
# --------------------------------------------------------------------------

def build_memory_stream(agent, name, occupation, persona, relations, names) -> list[dict]:
    """Seed memories in the paper's observation / reflection / plan shape.

    Importance is 1-10 as in the paper; the paper asks the model to rate
    poignancy, this assigns by kind (who I am and who I love score high, a
    routine errand scores low) and records that choice in the file.
    """
    aid = agent["agent_id"]
    stream: list[dict] = []

    def entry(kind: str, description: str, importance: int, evidence=None) -> None:
        stream.append({
            "id": f"{aid}-m{len(stream) + 1:02d}",
            "type": kind,
            "description": description,
            "created_day": 0,
            "created_tick": len(stream),
            "importance": importance,
            "last_accessed_day": 0,
            "evidence": evidence or [],
        })

    entry("observation", f"My name is {name}. I live in Kasukabe.", 9)
    entry("observation", f"I am {round(agent['age_years'])} years old.", 5)
    if occupation not in ("child", "student", "retired"):
        entry("observation", f"I work as a {occupation.replace('_', ' ')}.", 8)
    elif occupation == "retired":
        entry("observation", "I am retired. My working years are behind me.", 8)
    else:
        entry("observation", "I am still a child here. I go where my family goes.", 8)

    entry("observation", f"My household is {agent['household_id']}.", 4)
    entry("observation",
          f"I start each day with {agent['starting_sim_balance']:,} in sim currency "
          f"and a cognition wallet of ${agent['usd_wallet_cents'] / 100:.2f}.", 6)

    for rel in relations[:6]:
        other = names.get(rel["agent_id"], rel["agent_id"])
        entry("observation", f"{other} is my {rel['relation']}.", 8 if rel["source"] == "genesis" else 6,
              evidence=[rel["agent_id"]])

    for habit in (agent.get("habits") or [])[:2]:
        entry("observation", f"I {habit}.", 3)

    tension = persona.get("tension_description")
    if tension:
        entry("reflection", tension, 9,
              evidence=[e["id"] for e in stream if e["type"] == "observation"][:3])
    fears = persona.get("fears")
    if fears:
        entry("reflection", f"What I fear most is {fears}.", 9)
    values = persona.get("values") or []
    if values:
        entry("reflection", f"What matters to me most: {values[0]}.", 8)
    limit = persona.get("will_not_compromise")
    if limit:
        entry("reflection", f"There is a line I will not cross: I {limit}.", 10)
    scarcity = persona.get("scarcity_behavior")
    if scarcity:
        entry("reflection", f"When money gets tight, I {scarcity}.", 7)

    ambition = persona.get("ambitions")
    if ambition:
        entry("plan", f"In the long run I want {ambition}.", 9)
    if occupation not in ("child", "student", "retired"):
        entry("plan", f"Today: go to the {WORKPLACE_NAME.get(day_zone(occupation), 'town')} and do a day's work.", 5)
    entry("plan", "Today: pay what I owe, and keep my balance above zero.", 6)
    return stream



# --------------------------------------------------------------------------
# Staff for the institutions the town runs
# --------------------------------------------------------------------------
# genesis.json seeds 250 residents for the genesis buildings only. A town with
# a hospital, police station, fire station, school, library, bank, bakery,
# restaurant and inn needs people to run them, so this adds those workers as
# real residents: their own households, personas, kinship, memory stream and
# expert reflections, exactly like anyone born into genesis. They carry
# `origin: "institution_staff"` so they are always distinguishable from the
# genesis 250 in any analysis.
INSTITUTION_STAFF = [
    ("hospital",   "doctor",           3), ("hospital",   "nurse",          7),
    ("police",     "police_officer",   8),
    ("fire",       "firefighter",      6),
    ("bakery",     "baker",            4),
    ("restaurant", "chef",             3), ("restaurant", "waiter",         4),
    ("school",     "school_teacher",   9),
    ("library",    "librarian",        3),
    ("bank",       "banker",           5),
    ("inn",        "innkeeper",        4),
    ("townsquare", "street_sweeper",   3),
    ("marketrow",  "shopkeeper",       5),
]

STAFF_BIO = {
    "doctor": "treats the sick at the town hospital",
    "nurse": "nurses patients through the ward at the hospital",
    "police_officer": "keeps the peace and works cases out of the police station",
    "firefighter": "answers the bell at the fire station",
    "baker": "is up before dawn at the bakery, turning flour into bread",
    "chef": "cooks the evening service at the restaurant",
    "waiter": "waits tables at the restaurant",
    "school_teacher": "teaches the town's children at the school",
    "librarian": "keeps the public records at the library",
    "banker": "handles deposits and credit at the town bank",
    "innkeeper": "keeps rooms at the inn for anyone between households",
    "street_sweeper": "keeps the square and the streets clean",
    "shopkeeper": "runs a stall on Market Row",
}
STAFF_TENSION = [
    "is generous with time and stingy with trust.",
    "wants to be liked and cannot stop saying the blunt thing.",
    "is patient with strangers and short with family.",
    "works to be needed, and resents being needed.",
    "keeps every promise and makes as few as possible.",
]
STAFF_FEAR = [
    "being found out as less capable than people assume",
    "outliving the work that gives the day its shape",
    "owing anyone anything",
    "a quiet house at the end of it",
    "being the one who was not there when it mattered",
]
STAFF_AMBITION = [
    "to be the one people send for",
    "to train someone better than themselves",
    "to own the roof over their head outright",
    "to see the town outlast them",
    "to be owed nothing and owe nothing",
]
STAFF_VALUES = [
    "showing up when it is inconvenient",
    "telling someone the truth before they ask twice",
    "doing the unglamorous half of the job properly",
    "never charging someone who cannot pay",
]
STAFF_HABITS = [
    "reads the noticeboard twice a day",
    "walks the long way home past the river",
    "keeps a tally of favours owed and owing",
    "feeds the strays behind the building",
    "hums while working and does not notice",
]
STAFF_TRAITS = ["steady", "sharp", "warm", "exacting", "unflappable", "dry", "watchful", "generous"]


def build_staff(existing_agents, names, households):
    """Create one resident per institution post. Returns plain dicts in the
    same shape genesis.json uses, so everything downstream treats them
    identically to the original 250."""
    hh_index = len(households) + 1
    idx = 0
    made = []
    pairs = []          # (male, female) candidates to marry to each other
    for zone, occupation, count in INSTITUTION_STAFF:
        for _ in range(count):
            idx += 1
            aid = f"s{idx:04d}"
            male = stable(aid, "sex") % 2 == 0
            age = 24 + stable(aid, "age") % 40
            hh = f"h9{hh_index:03d}"
            hh_index += 1
            made.append({
                "agent_id": aid,
                "name": None,                    # assigned after, needs the surname map
                "age_years": float(age),
                "sex": "male" if male else "female",
                "household_id": hh,
                "marital_status": "single",
                "spouse_id": None,
                "parent_ids": [],
                "children_ids": [],
                "occupation": occupation,
                "personality_traits": [pick(STAFF_TRAITS, aid, "t1"), pick(STAFF_TRAITS, aid, "t2")],
                "habits": [pick(STAFF_HABITS, aid, "hb")],
                "bio": None,
                "usd_wallet_cents": 200,
                "starting_sim_balance": 5000,
                "ablation_arm": "full",
                "origin": "institution_staff",
                "workplace_zone": zone,
                "persona": {
                    "internal_tension": "",
                    "tension_description": pick(STAFF_TENSION, aid, "tn"),
                    "formative_history": "",
                    "values": [pick(STAFF_VALUES, aid, "v")],
                    "will_not_compromise": "will not sign off on work they have not checked",
                    "fears": pick(STAFF_FEAR, aid, "f"),
                    "ambitions": pick(STAFF_AMBITION, aid, "am"),
                    "scarcity_behavior": "takes extra shifts and says nothing about why",
                    "stranger_treatment": "gives a stranger the same hour as a neighbour",
                    "family_treatment": "assumes family will understand the late nights",
                },
            })
            pairs.append(aid)

    # Marry roughly half of them to one another, so the staff have families too
    # rather than being 60 unattached adults dropped into the town.
    males = [a for a in made if a["sex"] == "male"]
    females = [a for a in made if a["sex"] == "female"]
    for m, f in zip(males, females):
        if stable(m["agent_id"], "wed") % 100 >= 55:
            continue
        m["marital_status"] = f["marital_status"] = "married"
        m["spouse_id"] = f["agent_id"]
        f["spouse_id"] = m["agent_id"]
        f["household_id"] = m["household_id"]      # move in together
    return made

# --------------------------------------------------------------------------
# Expert reflection (Park et al. 2024, "Generative Agent Simulations of 1,000
# People", SM 3)
# --------------------------------------------------------------------------

# That paper's central architectural contribution. Prompting a model with a
# person's raw description in one chain of thought makes it miss latent
# information never stated outright, so they add a reflection pass that adopts
# four social-science expert personas - psychologist, behavioral economist,
# political scientist, demographer - and has each write up to 20 observations
# about the same person. At question time they classify which expert best fits
# the question and retrieve only that expert's reflections.
#
# Two honest caveats, both carried in the output file:
#   - The paper runs this pass with GPT-4o over a two-hour interview transcript.
#     There are no interviews in Kasukabe and no model is called here, so these
#     reflections are rule-derived from the persona fields genesis.json already
#     carries. They demonstrate the structure and give the retrieval step
#     something real to route over; they are not a reproduction of the result.
#   - The paper's own finding is that this architecture works because of the
#     interview. Interview-based agents scored 0.85 normalized accuracy on the
#     GSS against 0.70 for persona-based agents - and Kasukabe's agents are
#     persona-based. See RESEARCH_NOTES.md.
EXPERTS = ("psychologist", "behavioral_economist", "political_scientist", "demographer")

EXPERT_LABEL = {
    "psychologist": "Psychologist",
    "behavioral_economist": "Behavioral Economist",
    "political_scientist": "Political Scientist",
    "demographer": "Demographer",
}


def build_expert_reflections(agent, name, occupation, persona, relations) -> dict[str, list[str]]:
    """Four expert views over the same resident, in the paper's shape."""
    age = round(agent["age_years"])
    sex_word = "He" if agent["sex"] == "male" else "She"
    poss = "his" if agent["sex"] == "male" else "her"
    bal = agent["starting_sim_balance"]
    kin = len(relations)
    out: dict[str, list[str]] = {e: [] for e in EXPERTS}

    p = out["psychologist"]
    if persona.get("tension_description"):
        p.append(f"{persona['tension_description']} This internal tension is the most "
                 f"reliable predictor of how {name} behaves when a situation forces a choice.")
    if persona.get("fears"):
        p.append(f"{sex_word} fears {persona['fears']}, which suggests avoidance behaviour "
                 f"around anything that makes that outcome more likely.")
    if persona.get("will_not_compromise"):
        p.append(f"There is a hard limit here: {name} {persona['will_not_compromise']}. "
                 f"Expect refusal rather than negotiation when a request crosses it.")
    if persona.get("stranger_treatment") and persona.get("family_treatment"):
        p.append(f"Treatment of strangers ({persona['stranger_treatment']}) and of family "
                 f"({persona['family_treatment']}) differ, so in-group membership is doing "
                 f"real work in {poss} decisions.")
    if agent.get("personality_traits"):
        p.append(f"Self-described as {' and '.join(agent['personality_traits'])} — traits that "
                 f"should show up most clearly under time pressure.")

    b = out["behavioral_economist"]
    wage = {"trader": 140, "artisan": 135, "farmer": 125, "miner": 130}.get(occupation)
    if persona.get("scarcity_behavior"):
        b.append(f"Under scarcity {name} {persona['scarcity_behavior']} — the single most "
                 f"useful fact for predicting behaviour in a distributive game.")
    b.append(f"Opens with {bal:,} sim and a cognition wallet of "
             f"${agent['usd_wallet_cents'] / 100:.2f}; the daily head tax of "
             f"{HEAD_TAX_BASE_SIM * TAX_RATE_BPS // 10_000} sim applies whether or not "
             f"{sex_word.lower()} works that day.")
    if occupation not in ("child", "student", "retired"):
        b.append(f"Income comes from work as a {occupation.replace('_', ' ')}"
                 + (f" (about {wage} sim a day)" if wage else "")
                 + ", so an income shock hits directly rather than through a household earner.")
    else:
        b.append(f"No wage income: {name} depends on household transfers or pension, which "
                 f"makes {poss} exposure to a household shock higher than average.")
    if persona.get("ambitions"):
        b.append(f"Stated ambition — {persona['ambitions']} — is the goal a long-horizon "
                 f"trade-off should be scored against.")

    pol = out["political_scientist"]
    if occupation == "government_clerk":
        pol.append(f"{name} works inside the government complex, so is both subject to town "
                   f"policy and an administrator of it — expect a status-quo lean on tax questions.")
    elif occupation in ("construction_company_owner", "grocery_store_owner", "cafe_owner"):
        pol.append(f"As a business owner, {name} is exposed to the credit-store margin and "
                   f"licensing rules, so should be more sensitive to regulation than to the head tax.")
    elif occupation in ("day_laborer", "grocery_clerk", "barista", "construction_worker"):
        pol.append(f"Low, variable wage work means the flat head tax is a larger share of "
                   f"{poss} income; predict support for a progressive amendment.")
    else:
        pol.append(f"A {occupation.replace('_', ' ')}'s stake in town policy runs mainly through "
                   f"the tax rate and the pension terms rather than through licensing.")
    if persona.get("values"):
        pol.append(f"Holds \"{persona['values'][0]}\" as a stated value, which should predict "
                   f"{poss} vote where a charter amendment touches it.")
    if age >= 70:
        pol.append(f"At {age}, {name} is at or past the pension eligibility age of 70, so "
                   f"pension policy is a direct material interest, not an abstract one.")
    pol.append(f"{kin} kinship ties in town — a bloc that size matters in an election decided "
               f"by a simple majority of a 250-person population.")

    d = out["demographer"]
    d.append(f"{name} is {age}, {agent['sex']}, {agent['marital_status']}, in household "
             f"{agent['household_id']}.")
    d.append(f"Occupation: {occupation.replace('_', ' ')}"
             + (f", working at the {WORKPLACE_NAME.get(day_zone(occupation))}" if day_zone(occupation) else "") + ".")
    br = bracket(occupation)
    br_size = {"minor": 38, "working": 174, "elderly": 38}[br]
    br_word = {"minor": "minor", "working": "working-age", "elderly": "elderly"}[br]
    d.append(f"Falls in the {br_word} bracket — one of {br_size} such residents "
             f"in a population of 250.")
    if agent.get("children_ids"):
        d.append(f"Has {len(agent['children_ids'])} recorded child(ren), so appears in the "
                 f"dependency ratio as a supporter rather than a dependent.")
    d.append(f"Connected to {kin} other residents by kinship, against a town mean of about 13.")
    return out


EXPERT_ROUTING = {
    "psychologist": ["feel", "fear", "trust", "angry", "happy", "personality", "friend", "believe"],
    "behavioral_economist": ["money", "buy", "sell", "price", "pay", "save", "cost", "trade", "debt", "wage"],
    "political_scientist": ["vote", "tax", "law", "policy", "government", "charter", "election", "petition"],
    "demographer": ["age", "household", "family", "where", "who", "occupation", "live"],
}


def route_expert(question: str) -> str:
    """The paper's question-time step: pick the one expert whose reflections
    get retrieved. Theirs is an LLM classifier; this is keyword matching, which
    is enough to demonstrate the routing and is labelled as such."""
    q = question.lower()
    best, score = "demographer", 0
    for expert, keys in EXPERT_ROUTING.items():
        n = sum(1 for k in keys if k in q)
        if n > score:
            best, score = expert, n
    return best


# --------------------------------------------------------------------------

def build() -> None:
    data = json.loads(GENESIS.read_text(encoding="utf-8"))
    agents = data["agents"]

    # The institution staff are REAL AGENTS now, generated into genesis.json
    # by world/life/genesis.py (see INSTITUTION_STAFF there). They arrive in
    # `agents` with everyone else, so inventing them again here would give
    # the town two doctors for every post and a cast of 378.
    #
    # build_staff() below is kept, unused, as the record of how these people
    # were faked before they existed - and as the thing to delete once no
    # genesis predating the roster is still in circulation.
    staff: list[dict] = []

    by_id = {a["agent_id"]: a for a in agents}
    names = assign_names(agents)
    # Staff records were created before names existed; fill in the two fields
    # that depend on the final name map.
    for a in staff:
        a["name"] = names[a["agent_id"]]
        # No trailing period here: first_clause() adds one, and two produced
        # "...at the town hospital.." on every staff card.
        a["bio"] = f"{a['name']} is a {round(a['age_years'])}-year-old resident of Kasukabe who " \
                   f"{STAFF_BIO.get(a['occupation'], 'works in the town')}"
    names = assign_names(agents)
    if len(set(names.values())) != len(agents):
        raise SystemExit("name collision")

    households: dict[str, list[str]] = {}
    for a in agents:
        households.setdefault(a["household_id"], []).append(a["agent_id"])

    genesis_rel = build_genesis_relations(agents, by_id)
    extended_rel = build_extended_relations(agents, by_id, genesis_rel)

    swaps = sorted(((a["name"], names[a["agent_id"]]) for a in agents),
                   key=lambda p: len(p[0]), reverse=True)

    def rewrite(text: str) -> str:
        for real, short in swaps:
            if real in text:
                text = text.replace(real, short)
        return text

    def decorate(rels):
        return [{"relation": r["relation"], "agent_id": r["agent_id"],
                 "name": names[r["agent_id"]], "source": r["source"]} for r in rels]

    CHAR_DIR.mkdir(parents=True, exist_ok=True)
    for old in CHAR_DIR.glob("*.json"):
        old.unlink()

    bundle, index = [], []
    per_head_tax = HEAD_TAX_BASE_SIM * TAX_RATE_BPS // 10_000

    for a in agents:
        aid = a["agent_id"]
        occ = a["occupation"]
        persona = a.get("persona") or {}
        name = names[aid]
        zone = day_zone(occ)
        g_rel = decorate(genesis_rel[aid])
        e_rel = decorate(extended_rel[aid])
        pool = SKILL_POOL.get(occ, ["Improvisation"])
        memory = build_memory_stream(a, name, occ, persona, g_rel + e_rel, names)
        reflections = build_expert_reflections(a, name, occ, persona, g_rel + e_rel)

        record = {
            "agent_id": aid,
            "name": name,
            "genesis_name": a["name"],
            "origin": a.get("origin", "genesis"),
            "demographics": {
                "age": round(a["age_years"]),
                "sex": a["sex"],
                "age_bracket": bracket(occ),
                "marital_status": a["marital_status"],
            },
            "household": {
                "household_id": a["household_id"],
                "member_ids": households[a["household_id"]],
                "member_names": [names[m] for m in households[a["household_id"]]],
                "size": len(households[a["household_id"]]),
            },
            "occupation": {
                "title": occ,
                "label": occ.replace("_", " ").title(),
                "workplace": WORKPLACE_NAME.get(zone, "Town"),
                "workplace_zone": zone,
            },
            "persona": {
                "personality_traits": a.get("personality_traits") or [],
                "habits": a.get("habits") or [],
                "bio": rewrite(a.get("bio", "")),
                "internal_tension": persona.get("internal_tension", ""),
                "tension_description": rewrite(persona.get("tension_description", "")),
                "formative_history": rewrite(persona.get("formative_history", "")),
                "values": persona.get("values") or [],
                "will_not_compromise": persona.get("will_not_compromise", ""),
                "fears": rewrite(persona.get("fears", "")),
                "ambitions": rewrite(persona.get("ambitions", "")),
                "scarcity_behavior": persona.get("scarcity_behavior", ""),
                "stranger_treatment": persona.get("stranger_treatment", ""),
                "family_treatment": persona.get("family_treatment", ""),
            },
            "relationships": {
                "genesis": g_rel,
                "inferred": e_rel,
                "note": ("'genesis' edges are stated in world/genesis.json. "
                         "'inferred' edges come from a deterministic lineage layer "
                         "this build script constructs, because genesis.json records "
                         "only one generation of parent links and therefore contains "
                         "no grandparent, uncle, aunt, or cousin to read."),
            },
            "economy": {
                "starting_sim_balance": a["starting_sim_balance"],
                "usd_wallet_cents": a["usd_wallet_cents"],
                "usd_wallet": round(a["usd_wallet_cents"] / 100, 2),
                "daily_head_tax_sim": per_head_tax,
                "ablation_arm": a.get("ablation_arm", "full"),
            },
            "skills": {
                "base": SKILL_BASE.get(occ, "General Ability"),
                "skillified": pick(pool, aid, "skillpick"),
                "unlock_day": 2 + stable(aid, "unlockday") % 5,
                "note": "A skillified ability is a repeated routine promoted into a reusable skill.",
            },
            "memory": {
                "format": "generative-agents/observation-reflection-plan",
                "reference": ("Park et al., Generative Agents: Interactive Simulacra of "
                              "Human Behavior, UIST '23 (doi 10.1145/3586183.3606763)"),
                "scoring": ("importance is 1-10 as in the paper; assigned by rule here "
                            "(identity and kinship high, routine errands low) rather than "
                            "LLM-rated poignancy"),
                "seeded_at": "day 0, before the simulation starts",
                "stream": memory,
            },
            "expert_reflections": {
                "architecture": "expert reflection (Park et al. 2024, Generative Agent Simulations of 1,000 People, SM 3)",
                "experts": list(EXPERTS),
                "retrieval": ("at question time one expert is selected and only that expert's "
                              "reflections are retrieved alongside the agent's own memory"),
                "derivation": ("rule-derived from this resident's genesis persona fields. The paper "
                               "generates these with GPT-4o over a two-hour interview transcript; "
                               "Kasukabe has no interviews and calls no model here, so these show the "
                               "structure and give retrieval something real to route over — they do "
                               "not reproduce the paper's accuracy result. See RESEARCH_NOTES.md."),
                "by_expert": reflections,
            },
        }
        (CHAR_DIR / f"{aid}.json").write_text(json.dumps(record, indent=2) + "\n")

        index.append({"agent_id": aid, "name": name, "genesis_name": a["name"],
                      "occupation": occ, "household_id": a["household_id"],
                      "file": f"data/characters/{aid}.json"})

        # Compact record for the page. Same facts, short keys, so 250 of them
        # stay a reasonable download.
        spouse = a.get("spouse_id")
        bundle.append({
            "id": aid, "n": name, "rn": a["name"], "org": a.get("origin", "genesis"),
            "age": round(a["age_years"]), "sex": a["sex"][0], "occ": occ,
            "br": bracket(occ), "dz": zone, "hh": a["household_id"],
            "ms": a["marital_status"], "sp": names.get(spouse) if spouse in by_id else None,
            "nk": len(a.get("children_ids") or []),
            "pn": [names[p] for p in (a.get("parent_ids") or []) if p in by_id],
            "bal": a["starting_sim_balance"], "usd": a["usd_wallet_cents"],
            "trait": (a.get("personality_traits") or ["—"])[0],
            "habit": (a.get("habits") or [""])[0],
            "bio": rewrite(first_clause(a.get("bio", ""))),
            "tension": rewrite(persona.get("tension_description", "")),
            "fear": rewrite(persona.get("fears", "")),
            "ambition": rewrite(persona.get("ambitions", "")),
            "values": (persona.get("values") or [""])[0],
            "limit": persona.get("will_not_compromise", ""),
            "scarcity": persona.get("scarcity_behavior", ""),
            "rel": [{"r": r["relation"], "id": r["agent_id"], "n": r["name"], "s": r["source"]}
                    for r in (g_rel + e_rel)],
            "mem": [{"t": m["type"][0], "d": m["description"], "i": m["importance"]} for m in memory],
            "exp": {e: reflections[e] for e in EXPERTS},
        })

    (CHAR_DIR / "index.json").write_text(json.dumps(
        {"population": len(index),
         "source": "world/genesis.json",
         "generated_by": "game/static/js/township/build_characters.py",
         "characters": index}, indent=2) + "\n")

    AGENTS_JS.write_text(
        "// The 250 residents of Kasukabe, generated from world/genesis.json.\n"
        "// Regenerate: python3 game/static/js/township/build_characters.py\n"
        "// One full JSON file per resident also lives in data/characters/.\n"
        "// `n` = short display name, `rn` = original genesis name,\n"
        "// `rel` = kinship (s: genesis | inferred), `mem` = seeded memory stream.\n"
        "const AGENTS = " + json.dumps(bundle, separators=(",", ":")) + ";\n"
    )

    g_edges = sum(len(v) for v in genesis_rel.values())
    e_edges = sum(len(v) for v in extended_rel.values())
    mem_total = sum(len(r["mem"]) for r in bundle)
    refl_total = sum(sum(len(v) for v in r["exp"].values()) for r in bundle)
    print(f"characters   {len(bundle)} JSON files -> {CHAR_DIR.relative_to(STATIC)}/")
    print(f"kinship      {g_edges} genesis edges + {e_edges} inferred edges")
    print(f"memory       {mem_total} seeded entries ({mem_total / len(bundle):.1f} per resident)")
    print(f"reflections  {refl_total} expert reflections across {len(EXPERTS)} expert personas")
    print(f"bundle       {AGENTS_JS.name} {AGENTS_JS.stat().st_size:,} bytes")


if __name__ == "__main__":
    build()
