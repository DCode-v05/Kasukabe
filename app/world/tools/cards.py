"""Agent cards and people discovery: who is in this town, and who can help.

Ported from `Prototype/A2A` (Atlas): `atlas/a2a/models.py :: AgentCard` and
`atlas/bus/discovery.py :: Discovery.rank`. `docs/A2A_CONFORMANCE.md` lists
both as absent from Kasukabe and calls them "genuinely missing and genuinely
useful"; this module is that pass.

WHAT IT IS FOR. `list_places` answers "where can I go". Nothing answered "who
can help me", and the cost of that is visible in the trace. Bram Holloway,
holding four units of copper ore, in one tick:

    "Any word who's working metal?"  ... "You know anyone trading copper?"
    "Copper ore here. Four units. Looking for a buyer."  ... "Bram? You home yet?"

Five remarks to a room that did not know either, while the town had a trader
standing two buildings away. Shouting is what an agent does when it has no
way to look somebody up.

THE DATA WAS ALREADY THERE. `world.skills` has proficiency per agent per
skill, `world.businesses` has owners and staff, genesis has occupation, and
`skills_api.py` has served a town-wide skill spread to the operator console
since it was built. The gap was never the data - it is the same shape as
`utterances`, `memories` and `transactions` before them: a real record with
no way for an agent to reach it.

THE PRIVACY LINE. Atlas tiers its cards, withholding the org-profile
extension (clearance, reporting line) from unauthenticated callers. Kasukabe
has no authentication and needs a different line, so the card carries
**what a neighbour could observe by living here**: your name, your trade,
where you work, the shop you own or work at, and what the town has seen you
become good at. It carries no wallet, no secret, and nothing from the private
half of a persona. A discovery tool that returned balances would hand every
resident a surveillance instrument the town never voted for, and would make
`steal` a lookup rather than a risk.
"""

from __future__ import annotations

import re
from typing import Any

# Words that carry no signal about who to ask. Taken from Atlas's own
# STOPWORDS and extended with the ones this town's residents actually open
# with ("anyone", "buying", "know"), which would otherwise match everybody.
STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "what", "whats", "how", "who", "can", "you",
        "please", "need", "want", "get", "give", "tell", "about", "from", "this",
        "that", "have", "has", "are", "our", "your", "their", "they", "them", "any",
        "all", "some", "out", "into", "onto", "was", "were", "will", "would", "should",
        "could", "doing", "done", "make", "made", "let", "lets", "got", "going",
        "anyone", "anybody", "somebody", "someone", "know", "knows", "here", "there",
        "good", "best", "where", "when", "does", "did", "look", "looking", "find",
    }
)

# Weights. Tag-style overlap dominates, exactly as Atlas's scorer does, and
# every term is explainable to somebody reading a result they disagree with.
SKILL_WEIGHT = 2.0
OCCUPATION_WEIGHT = 1.5
# Agents use this as a person-finder as well as a trade-finder: of the first
# ten `find_people` calls a live town made, three were name lookups ("Bram
# Holloway", "where is Tobias Holloway working now"). Before this weight
# existed those only answered by accident, when a surname happened to appear
# in a shared business name.
#
# Deliberately BELOW the occupation weight. A household shares a surname, so
# a name match is weaker evidence than a trade match, and "Ainsley, who does
# the mining here?" must answer with the miner rather than with Ainsley.
NAME_WEIGHT = 1.3
# Naming somebody in full identifies one resident, not a family. Large
# enough to outrank a surname collected several times over from a
# household's shop and workplace names, small enough that it cannot beat a
# genuine trade match plus a skill.
FULL_NAME_BONUS = 2.0
BUSINESS_WEIGHT = 1.2
WORKPLACE_WEIGHT = 1.0
# Proficiency is the Kasukabe analogue of Atlas's seniority bias: it breaks
# ties between two people who both do a thing, in favour of the one the town
# has actually watched get good at it. Scaled to stay below a single extra
# term match, so competence orders equals rather than outvoting relevance.
PROFICIENCY_WEIGHT = 0.9

DEFAULT_LIMIT = 5

# One trade, several words: what a resident ASKS and what the town CALLS the
# job are almost never the same token.
#
# Bram asked who was "trading" copper and who was "buying" it; the town's
# word is "trader". He asked who was "working metal"; the town's words are
# "miner" and "mining". Exact matching answered none of those, which is
# indistinguishable from a town with no trader in it.
#
# An explicit table rather than a stemmer, and for the same reason
# world/tools/place_aliases.py is a table: the vocabulary is small, closed,
# and known, while a stemmer on this vocabulary is actively wrong -
# "baker" stems to "bak" and "bakery" to "bake", so the two would stop
# matching each other. A table is auditable, and a wrong row is one line to
# fix rather than a rule to re-derive.
#
# Keyed by the word an agent might use; the values are the tokens the town
# actually stores in occupations and skill names.
TRADE_ALIASES: dict[str, tuple[str, ...]] = {
    "trade": ("trader",), "trades": ("trader",), "trading": ("trader",),
    "buy": ("trader",), "buys": ("trader",), "buying": ("trader",),
    "buyer": ("trader",), "sell": ("trader",), "sells": ("trader",),
    "selling": ("trader",), "seller": ("trader",), "market": ("trader",),
    "price": ("trader",), "prices": ("trader",),
    "mine": ("miner", "mining"), "mines": ("miner", "mining"),
    "ore": ("miner", "mining"), "metal": ("miner", "mining"),
    "copper": ("miner", "mining"), "iron": ("miner", "mining"),
    "smith": ("miner", "mining"), "smithy": ("miner", "mining"),
    "bake": ("baker", "cooking"), "bakes": ("baker", "cooking"),
    "baking": ("baker", "cooking"), "bread": ("baker", "cooking"),
    "flour": ("baker", "cooking"), "cook": ("baker", "cooking"),
    "food": ("baker", "cooking"),
    "farm": ("farmer", "farming"), "farms": ("farmer", "farming"),
    "crop": ("farmer", "farming"), "crops": ("farmer", "farming"),
    "grain": ("farmer", "farming"), "wheat": ("farmer", "farming"),
    "harvest": ("farmer", "farming"),
    "timber": ("forester", "forestry"), "wood": ("forester", "forestry"),
    "logging": ("forester", "forestry"), "felling": ("forester", "forestry"),
    "forest": ("forester", "forestry"),
    "craft": ("artisan", "artisanry"), "crafts": ("artisan", "artisanry"),
    "crafting": ("artisan", "artisanry"), "furniture": ("artisan", "artisanry"),
    "cloth": ("artisan", "artisanry"), "make": ("artisan", "artisanry"),
    "build": ("construction_worker", "masonry"),
    "building": ("construction_worker", "masonry"),
    "builder": ("construction_worker", "masonry"),
    "teach": ("college_instructor",), "teacher": ("college_instructor",),
    "lend": ("trader",), "loan": ("trader",), "money": ("trader",),
}


def tokenize(text: str) -> set[str]:
    """Meaningful words, plus the town's own word for whatever trade they name.

    Aliases are ADDED, never substituted: "who trades copper" keeps `copper`
    and gains `trader`, `miner` and `mining`, so a query still matches on its
    own words as well as on the trade they imply.
    """
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 2 and token not in STOPWORDS
    }
    for token in tuple(tokens):
        tokens.update(TRADE_ALIASES.get(token, ()))
    return tokens


def read(world: Any, field: str, agent_id: str, default: Any = None) -> Any:
    """One agent's entry in one of world's per-agent dicts.

    A tolerant read on purpose: this module runs across several dicts that
    different composition roots populate at different times, and a town
    mid-boot must not be able to take an agent's turn down. Build Spec v2
    clause 4.4 forbids silent defaults on fields that carry a FINDING - an
    absent occupation is a missing label, not a wrong number, so this is the
    permitted case rather than the forbidden one.
    """
    table = getattr(world, field, None)
    if not isinstance(table, dict):
        return default
    value = table.get(agent_id)
    return default if value is None else value


def business_for(world: Any, agent_id: str) -> "dict[str, str] | None":
    """The shop this agent owns, or works at, if any."""
    businesses = getattr(world, "businesses", None)
    if not isinstance(businesses, dict):
        return None
    for business in businesses.values():
        owner = getattr(business, "owner_agent_id", None)
        staff = getattr(business, "employee_agent_ids", None) or ()
        if owner == agent_id:
            role = "owner"
        elif agent_id in staff:
            role = "employee"
        else:
            continue
        return {
            "business_id": getattr(business, "business_id", "") or "",
            "name": getattr(business, "name", "") or "",
            "role": role,
        }
    return None


def agent_card(world: Any, agent_id: str) -> "dict[str, Any] | None":
    """What this resident publicly is.

    The A2A card's identity-plus-skills half, scoped to what is observable
    in a town rather than what is served over a network. See the module
    docstring for the privacy line and why it sits where it does.
    """
    names = getattr(world, "agent_names", None) or {}
    if agent_id not in names:
        return None
    skills = read(world, "skills", agent_id, {}) or {}
    return {
        "agent_id": agent_id,
        "name": names.get(agent_id, agent_id),
        "occupation": read(world, "occupations", agent_id, "") or "",
        "workplace": read(world, "workplaces", agent_id, "") or "",
        "business": business_for(world, agent_id),
        # Highest proficiency first: what somebody is known for comes before
        # what they have merely tried.
        "skills": dict(sorted(skills.items(), key=lambda kv: (-kv[1], kv[0]))),
        "location_id": world.locations.location_of(agent_id)
        if hasattr(world, "locations")
        else None,
    }


def score_card(card: "dict[str, Any]", query_terms: set[str]) -> float:
    """How well this resident answers the question. Higher is better, 0 is no.

    Deliberately simple and explainable, following Atlas's scorer: an agent
    shown a result it disagrees with should be able to see why it came back.
    """
    score = 0.0

    skills = card.get("skills") or {}
    for skill, proficiency in skills.items():
        if tokenize(skill) & query_terms:
            score += SKILL_WEIGHT
            # 0-100 proficiency, normalised so a master adds just under one
            # extra term match and a novice adds almost nothing.
            score += PROFICIENCY_WEIGHT * (min(max(proficiency, 0), 100) / 100.0)

    # Name match scores per matching part, so a full name beats a shared
    # surname: "Marta Thorne" outranks her household on both words, while
    # a bare "Thorne" ranks the whole family level, which is the honest
    # answer to a question about a family.
    name_parts = tokenize(card.get("name") or "")
    matched_parts = name_parts & query_terms
    score += NAME_WEIGHT * len(matched_parts)
    if name_parts and matched_parts == name_parts:
        # Every part of this person's name is in the question, which
        # identifies ONE resident rather than describing a family. That has
        # to be decisive, because a surname is otherwise scattered across
        # the household's shop and workplace names: "where is Idris Thorne
        # working" was answered with Marta, whose bakery is called
        # "Thorne's Bakery" and who therefore collected the surname three
        # times over.
        score += FULL_NAME_BONUS

    if tokenize(card.get("occupation") or "") & query_terms:
        score += OCCUPATION_WEIGHT

    business = card.get("business") or {}
    if tokenize(business.get("name") or "") & query_terms:
        score += BUSINESS_WEIGHT

    if tokenize(card.get("workplace") or "") & query_terms:
        score += WORKPLACE_WEIGHT

    return score


def rank_people(
    world: Any,
    query: str,
    *,
    exclude: "str | None" = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Residents who can help with `query`, best first.

    Returns [] rather than the whole town when nothing matches. A discovery
    tool that answers every question with everybody has told the agent
    nothing and charged it a paragraph of context for the privilege.

    Sorted by score then agent_id, so the same query twice gives the same
    answer and a trace can be replayed.
    """
    terms = tokenize(query)
    if not terms:
        return []

    dead = getattr(world, "dead", None) or frozenset()
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for agent_id in getattr(world, "agent_names", None) or {}:
        if agent_id == exclude or agent_id in dead:
            continue
        card = agent_card(world, agent_id)
        if card is None:
            continue
        score = score_card(card, terms)
        if score > 0:
            scored.append((score, agent_id, card))

    scored.sort(key=lambda row: (-row[0], row[1]))
    return [
        {**card, "match_score": round(score, 2)} for score, _, card in scored[:limit]
    ]
