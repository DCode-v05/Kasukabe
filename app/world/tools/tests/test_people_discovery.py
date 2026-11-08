"""Finding the person who can help, instead of shouting at the room.

Ported from `Prototype/A2A` (Atlas): `atlas/a2a/models.py :: AgentCard` +
`AgentSkill`, and `atlas/bus/discovery.py :: Discovery.rank`. See
`docs/A2A_CONFORMANCE.md` - this is the "genuinely missing and genuinely
useful" row that audit called out and deliberately left for its own pass.

WHAT IT COSTS TO NOT HAVE THIS. Bram Holloway, in a live run, holding four
units of copper ore:

    "Any word who's working metal?"
    "Store's empty. No buyer here. You know anyone trading copper?"
    "Copper ore here. Four units. Looking for a buyer."
    "Four units from the seam today. You know anyone buying copper raw?"
    "Bram? You home yet?"

Five remarks, one tick, to a room that did not know either. The town HAS a
trader (Ainsley) and a supply store; Bram had no way to learn that short of
walking into every building. `list_places` answers "where can I go"; nothing
answered "who can help me". Discovery is the missing half of that pair.

THE DATA ALREADY EXISTS. `world.skills` holds proficiency per agent per
skill, `world.businesses` holds owners and employees, genesis holds
occupation and workplace, and `skills_api.py` already serves a town-wide
skill spread to the CONSOLE. As with `utterances`, `memories` and the rest,
the gap was never the data - it was that no agent could reach it.

THE PRIVACY LINE, AND WHY IT IS DRAWN HERE. Atlas tiers its cards: a PUBLIC
card carries identity, skills and capabilities, while the org-profile
extension (clearance, reporting line) is withheld from unauthenticated
callers. Kasukabe's equivalent line is what a neighbour could observe by
living in the same town: your trade, your workplace, your shop, what you are
known to be good at. A wallet balance, a secret, and a persona's private
fears are not observable, and a card that leaked them would hand every agent
a surveillance tool the town never voted for.
"""

from __future__ import annotations


from world.tools.cards import agent_card, rank_people


class FakeBusiness:
    def __init__(self, business_id, name, owner, employees=()):
        self.business_id = business_id
        self.name = name
        self.owner_agent_id = owner
        self.employee_agent_ids = list(employees)


def make_world() -> object:
    """A small town with the shapes discovery has to cope with: a trader, a
    miner, a baker who owns a shop, and a forester standing elsewhere."""
    world = type("W", (), {})()
    world.agent_names = {
        "n01": "Bram Holloway",
        "n02": "Ainsley Holloway",
        "n07": "Marta Thorne",
        "n08": "Idris Thorne",
    }
    world.agents = dict.fromkeys(world.agent_names)
    world.occupations = {
        "n01": "miner",
        "n02": "trader",
        "n07": "baker",
        "n08": "forester",
    }
    world.skills = {
        "n01": {"mining": 62},
        "n02": {"farming": 13},
        "n07": {"cooking": 40, "artisanry": 12},
        "n08": {"forestry": 32},
    }
    world.businesses = {"b_bakery": FakeBusiness("b_bakery", "Thorne's Bakery", "n07", ["n02"])}
    world.workplaces = {
        "n01": "Kasukabe Ore Outcrop",
        "n02": "Harvey Oak Supply Store",
        "n07": "Thorne's Bakery",
        "n08": "Kasukabe Forest",
    }
    places = {"n01": "cafe", "n02": "cafe", "n07": "cafe", "n08": "forest"}
    world.locations = type("L", (), {"location_of": staticmethod(lambda a: places.get(a))})()
    return world


def test_a_card_carries_who_you_are_and_what_you_can_do() -> None:
    card = agent_card(make_world(), "n01")
    assert card["name"] == "Bram Holloway"
    assert card["occupation"] == "miner"
    assert card["skills"] == {"mining": 62}
    assert card["workplace"] == "Kasukabe Ore Outcrop"


def test_a_card_names_the_business_you_own() -> None:
    card = agent_card(make_world(), "n07")
    assert card["business"]["name"] == "Thorne's Bakery"
    assert card["business"]["role"] == "owner"


def test_a_card_names_the_business_you_work_at() -> None:
    card = agent_card(make_world(), "n02")
    assert card["business"]["name"] == "Thorne's Bakery"
    assert card["business"]["role"] == "employee"


def test_a_card_never_carries_money_or_secrets() -> None:
    """The privacy line. Atlas withholds clearance and reporting line from a
    public card; Kasukabe withholds what a neighbour could not observe. A
    discovery tool that returned wallets would be a surveillance tool."""
    world = make_world()
    world.wallets = {"n01": 5000}
    card = agent_card(world, "n01")
    leaked = {"sim", "sim_balance", "wallet", "usd", "usd_wallet_cents", "secret", "fears"}
    assert not (leaked & set(card)), f"card leaked private state: {leaked & set(card)}"


def test_an_unknown_agent_has_no_card() -> None:
    assert agent_card(make_world(), "nobody") is None


def test_asking_for_a_trade_finds_the_person_who_does_it() -> None:
    """The whole point. Bram asked the room five times who trades; the town
    has a trader and could have said so."""
    ranked = rank_people(make_world(), "who buys copper, anyone trading", exclude="n01")
    assert ranked, "discovery found nobody"
    assert ranked[0]["name"] == "Ainsley Holloway"
    assert ranked[0]["occupation"] == "trader"


def test_asking_by_skill_finds_the_skilled() -> None:
    ranked = rank_people(make_world(), "who is good at mining", exclude="n02")
    assert ranked[0]["name"] == "Bram Holloway"


def test_proficiency_breaks_a_tie_between_two_who_share_a_skill() -> None:
    """Someone who is GOOD at a thing outranks someone who has merely
    touched it. Atlas biases on seniority; the Kasukabe analogue is the
    proficiency the town actually measured.

    A genuine tie is required for this to test what it claims: two miners,
    both mining, differing only in how good they are. An earlier version of
    this test pitted a miner against a forester who happened to mine, and
    the miner won on OCCUPATION - which is correct behaviour and not the
    invariant being asserted here.
    """
    world = make_world()
    world.occupations["n08"] = "miner"
    world.workplaces["n08"] = "Kasukabe Ore Outcrop"
    world.skills["n08"] = {"mining": 90}
    ranked = rank_people(world, "mining", exclude="n02")
    assert [r["name"] for r in ranked[:2]] == ["Idris Thorne", "Bram Holloway"]


def test_trade_outranks_a_bare_skill() -> None:
    """The other half of the same rule, stated so a change to the weights
    has to break one of the two. A miner asked about mining outranks a
    forester who once swung a pick, even at a lower proficiency: what you
    DO for a living is stronger evidence than what you have touched."""
    world = make_world()
    world.skills["n08"] = {"mining": 90}
    ranked = rank_people(world, "mining", exclude="n02")
    assert ranked[0]["name"] == "Bram Holloway"


def test_the_asker_is_never_their_own_answer() -> None:
    ranked = rank_people(make_world(), "mining", exclude="n01")
    assert all(r["agent_id"] != "n01" for r in ranked)


def test_a_business_name_is_searchable() -> None:
    ranked = rank_people(make_world(), "bakery", exclude="n01")
    assert ranked[0]["name"] == "Marta Thorne"


def test_a_workplace_is_searchable() -> None:
    """Bram's actual words were "the store was empty". The store has a name
    and somebody works there."""
    ranked = rank_people(make_world(), "supply store", exclude="n01")
    assert ranked[0]["name"] == "Ainsley Holloway"


def test_results_say_where_the_person_is_and_where_they_work() -> None:
    """A name alone is not actionable - the agent has to be able to walk to
    them, which is what `move` needs."""
    ranked = rank_people(make_world(), "trading", exclude="n01")
    top = ranked[0]
    assert top["workplace"] == "Harvey Oak Supply Store"
    assert top["location_id"] == "cafe"


def test_a_query_matching_nobody_returns_nothing_rather_than_everybody() -> None:
    """A discovery tool that answers every question with the whole town has
    told the agent nothing, at the cost of a paragraph."""
    assert rank_people(make_world(), "astronavigation submarine", exclude="n01") == []


def test_stopwords_alone_match_nobody() -> None:
    assert rank_people(make_world(), "who can the and for", exclude="n01") == []


def test_an_empty_query_matches_nobody() -> None:
    assert rank_people(make_world(), "   ", exclude="n01") == []


def test_results_are_capped() -> None:
    ranked = rank_people(make_world(), "mining trading baking forestry", exclude="n01", limit=2)
    assert len(ranked) <= 2


def test_ranking_is_stable_for_equal_scores() -> None:
    """Two runs of the same query must not return different orders, or a
    trace cannot be replayed."""
    world = make_world()
    first = rank_people(world, "farming", exclude="n01")
    second = rank_people(world, "farming", exclude="n01")
    assert [r["agent_id"] for r in first] == [r["agent_id"] for r in second]


def test_the_dead_are_not_discoverable() -> None:
    """Sending an agent to negotiate with a corpse is worse than telling it
    nobody can help."""
    world = make_world()
    world.dead = {"n02"}
    ranked = rank_people(world, "trading", exclude="n01")
    assert all(r["agent_id"] != "n02" for r in ranked)


def test_missing_world_attributes_do_not_break_discovery() -> None:
    """WorldState is assembled by a composition root and this reads across
    several of its dicts. A town mid-boot must not take a turn down."""
    bare = type("W", (), {})()
    bare.agent_names = {"n01": "Bram"}
    bare.agents = {"n01": None}
    assert rank_people(bare, "mining", exclude="n02") == []
    assert agent_card(bare, "n01")["name"] == "Bram"


def test_a_person_can_be_looked_up_by_name() -> None:
    """Driven by observed usage, not by design intuition.

    Of the first ten `find_people` calls a live town made, three were name
    lookups: "Bram Holloway", "Wren Thorne farmer", "where is Tobias
    Holloway working now". Agents reached for this as a person-finder as
    well as a trade-finder, and it only answered them by accident - the
    surname happened to appear in a shared business name.
    """
    ranked = rank_people(make_world(), "where is Idris Thorne working now", exclude="n01")
    assert ranked[0]["name"] == "Idris Thorne"


def test_a_given_name_alone_finds_the_person() -> None:
    ranked = rank_people(make_world(), "Marta", exclude="n01")
    assert ranked[0]["name"] == "Marta Thorne"


def test_a_surname_finds_the_family_not_one_person() -> None:
    """A household shares a surname, so "Holloway" is a family and not an
    individual. Returning both is correct; ranking one first on a
    coin-flip would not be."""
    ranked = rank_people(make_world(), "Holloway", exclude=None)
    assert {r["name"] for r in ranked} >= {"Bram Holloway", "Ainsley Holloway"}


def test_a_name_does_not_outrank_the_trade_actually_asked_for() -> None:
    """"Ainsley, who mines?" must still answer with the miner. A name match
    that beat a trade match would turn every question containing a
    greeting into a lookup of the person greeted.
    """
    ranked = rank_people(make_world(), "Ainsley, who does the mining here", exclude=None)
    assert ranked[0]["name"] == "Bram Holloway"
