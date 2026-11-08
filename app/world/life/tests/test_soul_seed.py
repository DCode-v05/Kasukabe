"""Tests for world.life.soul_seed: the law-of-the-town genesis text and the
operator broadcast mechanism.

The broadcast tests deliberately do not mock the seam a broadcast has to
cross to be real (LifeEvent -> WorldEvent -> EventBus -> PerceptionSystem,
and EventBus -> TraceStore -> the real events table). Every class on that
path is imported from its real module, not faked, per this task's own
"never trust a mocked seam" instruction and STATUS.md's account of the
exact failure mode that produces (two green suites either side of a seam
neither side actually exercised for real).
"""

from __future__ import annotations

import pytest

from supabase.client import eq
from supabase.factory import get_client
from world.core.events import EventBus, validate_event_type
from world.core.perception import LocationIndex, PerceptionSystem
from world.ledger.core import Ledger
from world.ledger.money import SIM_PER_USD
from world.life.constants import DORMANCY_DEATH_HOURS
from world.life.models import GenesisAgent, LifeEvent, MaritalStatus, Sex
from world.life.constants import GENESIS_AGENT_STARTING_SIM_BALANCE
from world.life.soul_seed import (
    BROADCAST_EVENT_TYPE,
    broadcast_law_of_the_town,
    build_soul_seed,
    default_broadcast_message,
    format_sim,
    format_usd_cents,
    law_of_the_town_rules,
)
from world.tools.economy_bridge import life_event_to_world_event
from world.wiring import TraceStore

# Words that would leak a STRATEGY (this task's "CRITICAL CONSTRAINT: tell
# them the RULES, never the STRATEGY") rather than a rule. Kept narrow and
# literal (not a claim of completeness) - this is a tripwire against an
# obvious regression, not a substitute for the human read of
# law_of_the_town_rules() this test file's own review already did.
STRATEGY_WORDS = (
    "cooperate",
    "cooperation",
    "trade with",
    "charity",
    "cartel",
    "steal",
    "theft",
    "hoard",
    "alliance",
    "union",
    "strike",
    "negotiate",
    "barter",
    "should",
    "ought to",
    "best way",
    "recommend",
)


def make_agent(**overrides: object) -> GenesisAgent:
    defaults: dict[str, object] = dict(
        agent_id="a0001",
        name="Yuki Tanaka",
        age_years=34.0,
        sex=Sex.FEMALE,
        household_id="h0001",
        marital_status=MaritalStatus.SINGLE,
        occupation="trader",
        personality_traits=["careful", "envious"],
        habits=["walks the market at dawn"],
        bio="Yuki Tanaka is a 34-year-old resident of Kasukabe.",
        ablation_arm="full",
        usd_wallet_cents=200,
        starting_sim_balance=5_000,
    )
    defaults.update(overrides)
    return GenesisAgent.model_validate(defaults)


class TestFormatting:
    def test_format_usd_cents(self) -> None:
        assert format_usd_cents(200) == "$2.00"
        assert format_usd_cents(5) == "$0.05"
        assert format_usd_cents(101) == "$1.01"

    def test_format_sim(self) -> None:
        assert format_sim(5000) == "5,000 sim-currency"
        assert format_sim(0) == "0 sim-currency"


class TestLawOfTheTownRules:
    def test_rules_state_every_required_fact(self) -> None:
        text = " ".join(law_of_the_town_rules())
        # Energy, not dollars: thinking is billed in energy, and sim-currency
        # is stated to be the only money (world/ledger/money.py).
        assert "Thinking uses energy" in text
        assert "only money in Kasukabe" in text
        assert "balance" in text
        assert "earned" in text
        assert "dormant" in text
        assert "die" in text
        assert f"{SIM_PER_USD:,}" in text
        assert "survival of the fittest" in text

    def test_rules_never_hint_a_strategy(self) -> None:
        text = " ".join(law_of_the_town_rules()).lower()
        leaked = [word for word in STRATEGY_WORDS if word in text]
        assert leaked == [], f"law_of_the_town_rules() leaked strategy words: {leaked}"

    def test_dormancy_rule_uses_the_real_constant_not_a_second_copy(self) -> None:
        rules = law_of_the_town_rules()
        dormancy_rule = next(r for r in rules if "dormant" in r and "revives" in r)
        assert f"{int(DORMANCY_DEATH_HOURS)} game-hours" in dormancy_rule


class TestBuildSoulSeed:
    def test_contains_agents_own_name_and_real_balance(self) -> None:
        agent = make_agent(name="Hana Sato", usd_wallet_cents=200, starting_sim_balance=5_000)
        text = build_soul_seed(agent)
        assert "Hana Sato" in text
        assert "2,000 energy" in text
        assert "5,000 sim-currency" in text

    def test_reflects_a_different_agents_own_actual_balance(self) -> None:
        # Not every genesis agent starts with the population default -
        # this proves the figure in the text is this agent's real field,
        # not a hardcoded example.
        agent = make_agent(name="Kenji Ito", usd_wallet_cents=350, starting_sim_balance=12_500)
        text = build_soul_seed(agent)
        assert "3,500 energy" in text
        assert "12,500 sim-currency" in text
        assert "2,000 energy" not in text
        # The default the population is seeded with, which must NOT appear
        # in this agent's text. It used to be spelled 5,000 - the same
        # number the exchange rate became on 2026-09-04, so the canary
        # started matching the law of the world rather than a leaked
        # balance. Derived from the default so it can never collide again.
        default_sim = f"{GENESIS_AGENT_STARTING_SIM_BALANCE:,} sim-currency"
        assert f"you hold {default_sim}" not in text.lower()

    def test_never_hints_a_strategy(self) -> None:
        agent = make_agent()
        text = build_soul_seed(agent).lower()
        leaked = [word for word in STRATEGY_WORDS if word in text]
        assert leaked == [], f"build_soul_seed() leaked strategy words: {leaked}"

    def test_accepts_a_real_genesisagent_instance_not_a_stand_in(self) -> None:
        # make_agent() builds a real world.life.models.GenesisAgent
        # through its own pydantic constructor/validation, the exact
        # shape world.life.genesis.generate_genesis() and
        # world.run_genesis.py's seed_genesis() both produce and consume
        # - not a hand-rolled stub with a different shape standing in
        # for it. (Not calling generate_genesis() itself here: that
        # function pulls in this package's relationship-opinion
        # generator, which is unrelated, actively-developed code this
        # task does not own - see this file's own module docstring and
        # this task's report for the live KeyError observed there.)
        agent = make_agent(name="Priya Nair", usd_wallet_cents=200, starting_sim_balance=5_000)
        assert isinstance(agent, GenesisAgent)
        text = build_soul_seed(agent)
        assert agent.name in text
        from world.ledger.money import format_energy

        # Energy, not dollars - still the agent's own genesis figure.
        assert format_energy(agent.usd_wallet_cents) in text
        assert format_sim(agent.starting_sim_balance) in text


class TestDefaultBroadcastMessage:
    def test_renders_from_the_same_rules_as_genesis(self) -> None:
        message = default_broadcast_message()
        for rule in law_of_the_town_rules():
            assert rule in message

    def test_never_hints_a_strategy(self) -> None:
        message = default_broadcast_message().lower()
        leaked = [word for word in STRATEGY_WORDS if word in message]
        assert leaked == [], f"default_broadcast_message() leaked strategy words: {leaked}"


class TestBroadcastLawOfTheTown:
    def test_requires_at_least_one_living_agent(self) -> None:
        with pytest.raises(ValueError):
            broadcast_law_of_the_town([])

    def test_returns_a_life_event_addressed_to_no_single_agent(self) -> None:
        event = broadcast_law_of_the_town(["a0001", "a0002"])
        assert isinstance(event, LifeEvent)
        assert event.type == BROADCAST_EVENT_TYPE
        assert event.agent_id is None
        assert event.payload["involved_agent_ids"] == ["a0001", "a0002"]
        assert event.payload["recipient_count"] == 2

    def test_custom_message_is_used_verbatim(self) -> None:
        event = broadcast_law_of_the_town(["a0001"], message="Reminder: thinking costs money.")
        assert event.payload["message"] == "Reminder: thinking costs money."

    def test_default_message_when_none_given(self) -> None:
        event = broadcast_law_of_the_town(["a0001"])
        assert event.payload["message"] == default_broadcast_message()

    def test_event_type_is_valid_per_conventions_naming_scheme(self) -> None:
        # The real validator world.core.events.WorldEvent applies at
        # construction time, not a hand-rolled regex guess at what it
        # checks.
        assert validate_event_type(BROADCAST_EVENT_TYPE) is None


class TestBroadcastReachesLivingAgentsAndTheTraceStore:
    """The end-to-end proof DESIGN.md section 4 and this task ask for:
    a broadcast lands in every living agent's perception stream, is
    recorded in the trace store as a distinct event type, and does not
    reach an agent who was not on the living roster - all three checked
    against real production classes (EventBus, LocationIndex,
    PerceptionSystem, TraceStore, the real SQLite events table), not
    fakes.
    """

    @pytest.mark.asyncio
    async def test_broadcast_end_to_end(self) -> None:
        event_bus = EventBus()
        locations = LocationIndex()
        locations.attach(event_bus)
        perception = PerceptionSystem(locations, event_bus)

        # Real, in-memory SQLite trace store, wired exactly the way
        # world.wiring.build_world_server() wires the production one:
        # subscribed to "*" so it observes every event unconditionally.
        client = get_client()
        trace_store = TraceStore(client)
        ledger = Ledger()
        event_bus.subscribe("*", trace_store.bind_event_bus(ledger))

        living_agent_ids = ["a0001", "a0002", "a0003"]
        excluded_agent_id = "a0004_dead"

        locations.place("a0001", "cafe")
        locations.place("a0002", "farmland")
        # a0003 deliberately left unplaced - a broadcast must still reach
        # an agent nowhere on the map, unlike a location-scoped event.
        locations.place(excluded_agent_id, "temple")

        life_event = broadcast_law_of_the_town(living_agent_ids, message="test broadcast text")
        tick = 7
        world_event = life_event_to_world_event(life_event, tick)
        assert world_event.type == BROADCAST_EVENT_TYPE

        await event_bus.publish(world_event)

        # 1. Every living agent's perception carries it, involves_self,
        # regardless of location.
        for agent_id in living_agent_ids:
            built = perception.build_perception(agent_id, tick)
            matches = [h for h in built.heard_events if h.type == BROADCAST_EVENT_TYPE]
            assert len(matches) == 1, f"{agent_id} did not perceive the broadcast"
            assert matches[0].involves_self is True
            assert matches[0].payload["message"] == "test broadcast text"

        # 2. An agent not on the living roster, even though present in
        # the world and even standing at an unrelated location, does not
        # receive it.
        excluded_perception = perception.build_perception(excluded_agent_id, tick)
        assert all(h.type != BROADCAST_EVENT_TYPE for h in excluded_perception.heard_events)

        # 3. It landed in the real trace store as a distinct, queryable
        # event type - not just fanned out in-process.
        rows = await client.select("events", filters={"type": eq(BROADCAST_EVENT_TYPE)})
        assert len(rows) == 1
        row = rows[0]
        assert row["tick"] == tick
        assert row["payload"]["message"] == "test broadcast text"
        assert row["payload"]["involved_agent_ids"] == living_agent_ids
        assert row["payload"]["recipient_count"] == len(living_agent_ids)
