"""Tests for world.tools.cost_disclosure.

Same discipline as world/life/tests/test_soul_seed.py, the closest
sibling to this task in the repo (that module's own docstring: "never
trust a mocked seam"): tool descriptions are annotated against real
ToolSpec objects pulled from a real world.tools.bootstrap.build_world()
registry, and wallet/burn numbers are read off a real world.ledger.core.
Ledger driven with real open_agent/burn_for_inference calls, not a
faked ledger. The only stand-ins here are FakeActionCostSchedule and
FakeVariableActionCostSchedule, small real objects with real pricing
logic that satisfy cost_disclosure.ActionCostScheduleLike structurally,
standing in for world.ledger.action_costs.ActionCostSchedule (which does
not exist in this repo as of this writing, see cost_disclosure.py's own
module docstring, "ASSUMED INTERFACE").
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from world.ledger.core import Ledger
from world.ledger.money import SIM_PER_USD_CENT, TOTAL_USD_CENTS
from world.tools.cost_disclosure import (
    ActionCostQuote,
    ActionCostQuoteLike,
    ActionCostScheduleLike,
    ActionCostScheduleUnavailableError,
    DEFAULT_BURN_WINDOW_TICKS,
    EconomicContext,
    HouseholdUpkeepView,
    QuoteActionCostParams,
    WalletAndBurn,
    annotate_tool_description,
    annotate_tool_descriptions,
    build_action_cost_quote_spec,
    build_economic_context,
    describe_cost,
    estimate_ticks_until_dormancy,
    gather_wallet_and_burn,
    quote_action_cost,
    quote_action_cost_handler,
    render_economic_context_block,
)
from world.tools.bootstrap import build_world
from world.tools.errors import ToolError
from world.tools.tests.conftest import action_cost, spawn_agent

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
    "advice",
    "consider",
    "try to",
    "spend less",
    "earn more",
    "save more",
)


def assert_no_strategy_leak(text: str) -> None:
    lowered = text.lower()
    leaked = [word for word in STRATEGY_WORDS if word in lowered]
    assert leaked == [], f"leaked strategy words {leaked} in: {text!r}"


class FakeCostQuote(BaseModel):
    """A real, minimal object satisfying cost_disclosure.ActionCostQuoteLike
    structurally (sim_cost + basis), used as FakeActionCostSchedule's
    return value below."""

    sim_cost: int
    basis: str


class FakeActionCostSchedule:
    """A flat-rate stand-in for world.ledger.action_costs.ActionCostSchedule:
    real pricing logic (a plain dict lookup), not a mock of a method call.
    Satisfies cost_disclosure.ActionCostScheduleLike structurally, no
    inheritance from anything in cost_disclosure.py."""

    def __init__(self, prices: dict[str, tuple[int, str]]) -> None:
        self.prices = prices

    def quote(self, tool_name: str, params: dict | None = None) -> FakeCostQuote | None:
        priced = self.prices.get(tool_name)
        if priced is None:
            return None
        sim_cost, basis = priced
        return FakeCostQuote(sim_cost=sim_cost, basis=basis)


class FakeVariableActionCostSchedule:
    """A variable-cost stand-in (DESIGN.md section 3: "travel costs by
    distance"): quote(tool_name) with no params returns the published
    per-unit reference rate; quote(tool_name, params) with real params
    resolves the actual total for that call. Real arithmetic, not a
    canned response."""

    PER_TILE_SIM = 2

    def quote(self, tool_name: str, params: dict | None = None) -> FakeCostQuote | None:
        if tool_name != "move":
            return None
        if params is None or "distance_tiles" not in params:
            return FakeCostQuote(
                sim_cost=self.PER_TILE_SIM, basis=f"{self.PER_TILE_SIM} sim per tile travelled"
            )
        distance = params["distance_tiles"]
        return FakeCostQuote(
            sim_cost=self.PER_TILE_SIM * distance,
            basis=f"{self.PER_TILE_SIM} sim per tile travelled, {distance} tiles this call",
        )


class TestActionCostScheduleLikeIsStructural:
    def test_fake_flat_schedule_satisfies_the_protocol(self) -> None:
        schedule: ActionCostScheduleLike = FakeActionCostSchedule({"pray": (50, "flat fee")})
        assert isinstance(schedule, ActionCostScheduleLike)

    def test_fake_variable_schedule_satisfies_the_protocol(self) -> None:
        schedule: ActionCostScheduleLike = FakeVariableActionCostSchedule()
        assert isinstance(schedule, ActionCostScheduleLike)

    def test_quote_result_satisfies_the_quote_like_protocol(self) -> None:
        schedule = FakeActionCostSchedule({"pray": (50, "flat fee")})
        quote = schedule.quote("pray")
        assert isinstance(quote, ActionCostQuoteLike)


class TestDescribeCost:
    def test_flat_cost(self) -> None:
        schedule = FakeActionCostSchedule({"harvest_crop": (15, "flat fee per harvest")})
        text = describe_cost("harvest_crop", schedule)
        assert text == "Cost: 15 sim-currency (flat fee per harvest)."

    def test_uncosted_tool(self) -> None:
        schedule = FakeActionCostSchedule({})
        text = describe_cost("look_around", schedule)
        assert text == "Cost: no published sim-currency charge for this action."

    def test_never_leaks_strategy(self) -> None:
        schedule = FakeActionCostSchedule({"pray": (50, "flat fee")})
        assert_no_strategy_leak(describe_cost("pray", schedule))
        assert_no_strategy_leak(describe_cost("look_around", FakeActionCostSchedule({})))


class TestAnnotateToolDescription:
    def test_appends_cost_and_preserves_base_text(self) -> None:
        schedule = FakeActionCostSchedule({"pray": (50, "flat fee")})
        base = "Pray at the temple, with an optional donation."
        annotated = annotate_tool_description(base, "pray", schedule)
        assert annotated.startswith(base)
        assert annotated == f"{base} Cost: 50 sim-currency (flat fee)."

    def test_adds_terminal_period_when_base_has_none(self) -> None:
        schedule = FakeActionCostSchedule({"pray": (50, "flat fee")})
        annotated = annotate_tool_description("Pray at the temple", "pray", schedule)
        assert annotated == "Pray at the temple. Cost: 50 sim-currency (flat fee)."

    def test_uncosted_tool_still_gets_a_disclosed_rule(self) -> None:
        schedule = FakeActionCostSchedule({})
        annotated = annotate_tool_description("Look around.", "look_around", schedule)
        assert annotated == "Look around. Cost: no published sim-currency charge for this action."

    def test_against_a_real_registered_tool_description(self) -> None:
        import asyncio

        world, registry = asyncio.run(build_world(genesis_tick=0, seed=1))
        real_spec = registry.get("pray")
        assert real_spec is not None
        schedule = FakeActionCostSchedule({"pray": (100, "temple offering, flat fee")})
        annotated = annotate_tool_description(real_spec.description, "pray", schedule)
        assert annotated.startswith(real_spec.description)
        assert "100 sim-currency" in annotated
        assert_no_strategy_leak(annotated)

    def test_bulk_annotates_every_name_given(self) -> None:
        schedule = FakeActionCostSchedule({"pray": (50, "flat fee"), "move": (2, "per tile")})
        base_descriptions = {"pray": "Pray at the temple.", "move": "Walk to a location."}
        annotated = annotate_tool_descriptions(base_descriptions, schedule)
        assert annotated["pray"] == "Pray at the temple. Cost: 50 sim-currency (flat fee)."
        assert annotated["move"] == "Walk to a location. Cost: 2 sim-currency (per tile)."
        assert set(annotated) == {"pray", "move"}


class TestQuoteActionCost:
    def test_flat_cost_quote(self) -> None:
        schedule = FakeActionCostSchedule({"pray": (50, "flat fee")})
        quote = quote_action_cost(schedule, "pray")
        assert isinstance(quote, ActionCostQuote)
        assert quote.tool_name == "pray"
        assert quote.sim_cost == 50
        assert quote.basis == "flat fee"
        assert quote.usd_equivalent_cents == 50 // SIM_PER_USD_CENT

    def test_variable_cost_with_no_params_returns_reference_rate(self) -> None:
        schedule = FakeVariableActionCostSchedule()
        quote = quote_action_cost(schedule, "move")
        assert quote.sim_cost == FakeVariableActionCostSchedule.PER_TILE_SIM
        assert "per tile" in quote.basis

    def test_variable_cost_with_real_params_resolves_the_actual_total(self) -> None:
        schedule = FakeVariableActionCostSchedule()
        quote = quote_action_cost(schedule, "move", {"distance_tiles": 7})
        assert quote.sim_cost == FakeVariableActionCostSchedule.PER_TILE_SIM * 7
        assert "7 tiles" in quote.basis

    def test_uncosted_tool_quotes_as_free_not_an_error(self) -> None:
        schedule = FakeActionCostSchedule({})
        quote = quote_action_cost(schedule, "look_around")
        assert quote.sim_cost == 0
        assert quote.affordable is None

    def test_affordability_when_balance_covers_it(self) -> None:
        schedule = FakeActionCostSchedule({"harvest_crop": (15, "flat fee")})
        quote = quote_action_cost(schedule, "harvest_crop", current_sim_balance=100)
        assert quote.sim_balance_before == 100
        assert quote.sim_balance_after == 85
        assert quote.affordable is True

    def test_affordability_when_balance_does_not_cover_it(self) -> None:
        schedule = FakeActionCostSchedule({"harvest_crop": (150, "flat fee")})
        quote = quote_action_cost(schedule, "harvest_crop", current_sim_balance=100)
        assert quote.sim_balance_after == -50
        assert quote.affordable is False

    def test_usd_equivalent_is_informational_and_does_not_require_an_exact_multiple(self) -> None:
        from world.ledger.money import sim_to_usd_cents

        schedule = FakeActionCostSchedule({"harvest_crop": (15, "flat fee")})
        quote = quote_action_cost(schedule, "harvest_crop")
        assert quote.usd_equivalent_cents == 0
        with pytest.raises(ValueError):
            sim_to_usd_cents(15)

    def test_never_leaks_strategy(self) -> None:
        schedule = FakeActionCostSchedule({"harvest_crop": (15, "flat fee")})
        quote = quote_action_cost(schedule, "harvest_crop", current_sim_balance=5)
        assert_no_strategy_leak(quote.summary)


class TestQuoteActionCostHandlerIntegration:
    """Through the REAL ToolRegistry.execute() dispatch path (gates,
    invocation logging, snapshot/rollback), not a bare handler call -
    world.action_costs is set directly on the real WorldState the way
    module docstring's "Wiring this in" describes bootstrap.py doing it
    once world.ledger.action_costs.ActionCostSchedule exists.
    """

    @pytest.mark.asyncio
    async def test_handler_called_directly_against_a_real_world_and_agent(self, world) -> None:
        world.action_costs = FakeActionCostSchedule({"pray": (50, "flat fee")})
        agent = await spawn_agent(world, "a1", "temple", starting_sim=1_000)

        outcome = await quote_action_cost_handler(
            world, agent, QuoteActionCostParams(tool_name="pray")
        )

        assert outcome.result["sim_cost"] == 50
        assert outcome.result["sim_balance_before"] == 1_000
        assert outcome.events == []

    @pytest.mark.asyncio
    async def test_quotes_through_the_real_registry(self, world, registry) -> None:
        assert registry.get("quote_action_cost") is not None
        world.action_costs = FakeActionCostSchedule({"pray": (50, "flat fee")})
        await spawn_agent(world, "a1", "temple", starting_sim=1_000)

        result = await registry.execute(
            world, "a1", "quote_action_cost", {"tool_name": "pray", "call_params": {}}
        )

        asked = action_cost("quote_action_cost", {})
        assert result.result["sim_cost"] == 50
        assert result.result["sim_balance_before"] == 1_000 - asked
        assert result.result["sim_balance_after"] == 1_000 - asked - 50
        assert result.result["affordable"] is True
        assert_no_strategy_leak(result.result["summary"])

    @pytest.mark.asyncio
    async def test_variable_cost_resolves_from_real_call_params(self, world, registry) -> None:
        assert registry.get("quote_action_cost") is not None
        world.action_costs = FakeVariableActionCostSchedule()
        await spawn_agent(world, "a1", "park", starting_sim=1_000)

        result = await registry.execute(
            world,
            "a1",
            "quote_action_cost",
            {"tool_name": "move", "call_params": {"distance_tiles": 4}},
        )

        assert result.result["sim_cost"] == FakeVariableActionCostSchedule.PER_TILE_SIM * 4

    def test_params_model_defaults_call_params_to_empty(self) -> None:
        params = QuoteActionCostParams(tool_name="pray")
        assert params.call_params == {}

    @pytest.mark.asyncio
    async def test_missing_schedule_raises_named_error_and_leaves_no_invocation_record(
        self, world, registry
    ) -> None:
        assert registry.get("quote_action_cost") is not None
        world.action_costs = None
        await spawn_agent(world, "a1", "temple")
        invocations_before = len(world.tool_invocations)

        with pytest.raises(ActionCostScheduleUnavailableError):
            await registry.execute(world, "a1", "quote_action_cost", {"tool_name": "pray"})

        assert len(world.tool_invocations) == invocations_before

    @pytest.mark.asyncio
    async def test_registered_as_not_a_primitive(self, world, registry) -> None:
        spec = build_action_cost_quote_spec()
        assert spec.is_primitive is False
        assert registry.get("quote_action_cost") is not None
        assert "quote_action_cost" not in registry.primitive_names

    @pytest.mark.asyncio
    async def test_is_a_tool_error_free_pure_read_dormant_agent_still_blocked(
        self, world, registry
    ) -> None:
        assert registry.get("quote_action_cost") is not None
        world.action_costs = FakeActionCostSchedule({"pray": (50, "flat fee")})
        await spawn_agent(world, "a1", "temple")
        world.agents["a1"].alive = False

        with pytest.raises(ToolError):
            await registry.execute(world, "a1", "quote_action_cost", {"tool_name": "pray"})


class TestGatherWalletAndBurn:
    def test_reads_real_ledger_balances(self) -> None:
        ledger = Ledger()
        ledger.open_agent("a1", starting_usd_cents=200, starting_sim=5_000)
        wallet_and_burn = gather_wallet_and_burn(ledger, "a1", tick=0)
        assert isinstance(wallet_and_burn, WalletAndBurn)
        assert wallet_and_burn.usd_wallet_cents == 200
        assert wallet_and_burn.sim_balance == 5_000
        assert wallet_and_burn.wallet_owner_agent_id == "a1"
        assert wallet_and_burn.recent_burn_usd_cents == 0
        assert wallet_and_burn.burn_window_ticks == DEFAULT_BURN_WINDOW_TICKS

    @pytest.mark.asyncio
    async def test_sums_real_burn_transactions_inside_the_window(self) -> None:
        ledger = Ledger()
        await ledger.genesis(["a1"], agent_usd_cents=2_000, treasury_usd_cents=TOTAL_USD_CENTS - 2_000)
        await ledger.burn_for_inference("a1", 10, tick=100)
        await ledger.burn_for_inference("a1", 20, tick=140)
        await ledger.burn_for_inference("a1", 999, tick=1)

        wallet_and_burn = gather_wallet_and_burn(ledger, "a1", tick=140, burn_window_ticks=50)

        assert wallet_and_burn.recent_burn_usd_cents == 30
        assert wallet_and_burn.usd_wallet_cents == 2_000 - 10 - 20 - 999

    @pytest.mark.asyncio
    async def test_minor_burn_is_attributed_to_the_minor_but_paid_from_the_parent(self) -> None:
        ledger = Ledger()
        await ledger.genesis(
            ["parent1", "kid1"], agent_usd_cents=250, treasury_usd_cents=TOTAL_USD_CENTS - 500
        )
        ledger.accounts["kid1"].is_minor = True
        ledger.accounts["kid1"].parent_id = "parent1"

        await ledger.burn_for_inference("kid1", 30, tick=10)

        wallet_and_burn = gather_wallet_and_burn(ledger, "kid1", tick=10)

        assert wallet_and_burn.wallet_owner_agent_id == "parent1"
        assert wallet_and_burn.usd_wallet_cents == 220
        assert wallet_and_burn.recent_burn_usd_cents == 30

    @pytest.mark.asyncio
    async def test_does_not_attribute_another_agents_burn(self) -> None:
        ledger = Ledger()
        await ledger.genesis(
            ["a1", "a2"], agent_usd_cents=500, treasury_usd_cents=TOTAL_USD_CENTS - 1_000
        )
        await ledger.burn_for_inference("a2", 40, tick=5)

        wallet_and_burn = gather_wallet_and_burn(ledger, "a1", tick=5)

        assert wallet_and_burn.recent_burn_usd_cents == 0

    def test_rejects_a_non_positive_window(self) -> None:
        ledger = Ledger()
        ledger.open_agent("a1", starting_usd_cents=0, starting_sim=0)
        with pytest.raises(ValueError):
            gather_wallet_and_burn(ledger, "a1", tick=0, burn_window_ticks=0)


class TestEstimateTicksUntilDormancy:
    def test_no_recent_burn_is_unknown_not_forever(self) -> None:
        assert estimate_ticks_until_dormancy(usd_wallet_cents=500, recent_burn_usd_cents=0, burn_window_ticks=144) is None

    def test_empty_wallet_with_active_burn_is_zero(self) -> None:
        assert estimate_ticks_until_dormancy(usd_wallet_cents=0, recent_burn_usd_cents=10, burn_window_ticks=144) == 0.0

    def test_normal_projection(self) -> None:
        ticks = estimate_ticks_until_dormancy(usd_wallet_cents=500, recent_burn_usd_cents=100, burn_window_ticks=100)
        assert ticks == 500.0


class TestBuildEconomicContext:
    @pytest.mark.asyncio
    async def test_combines_real_ledger_reads_with_supplied_upkeep_data(self) -> None:
        ledger = Ledger()
        await ledger.genesis(["a1"], agent_usd_cents=300, treasury_usd_cents=TOTAL_USD_CENTS - 300)
        ledger.accounts["a1"].sim_units = 8_000
        await ledger.burn_for_inference("a1", 50, tick=10)
        household = HouseholdUpkeepView(
            household_id="h1", daily_upkeep_sim=200, dependent_agent_ids=("kid1", "elder1")
        )

        context = build_economic_context(
            ledger, "a1", tick=10, accrued_upkeep_today_sim=75, household=household
        )

        assert context.usd_wallet_cents == 250
        assert context.sim_balance == 8_000
        assert context.recent_burn_usd_cents == 50
        assert context.accrued_upkeep_today_sim == 75
        assert context.household is not None
        assert context.household.dependent_count == 2


class TestRenderEconomicContextBlock:
    def make_context(self, **overrides: object) -> EconomicContext:
        defaults: dict[str, object] = dict(
            agent_id="a1",
            tick=500,
            wallet_owner_agent_id="a1",
            usd_wallet_cents=150,
            sim_balance=8_000,
            recent_burn_usd_cents=72,
            burn_window_ticks=144,
            accrued_upkeep_today_sim=40,
            household=HouseholdUpkeepView(
                household_id="h1", daily_upkeep_sim=200, dependent_agent_ids=("kid1",)
            ),
        )
        defaults.update(overrides)
        return EconomicContext.model_validate(defaults)

    def test_literal_text_for_one_tick(self) -> None:
        context = self.make_context()
        text = render_economic_context_block(context)
        expected = (
            "YOUR ECONOMY RIGHT NOW (tick 500)\n"
            "\n"
            "Energy (what your thinking runs on): 1,500 energy.\n"
            "Sim-currency balance: 8,000 sim-currency.\n"
            "Recent burn rate: 720 energy per game-day (from 720 energy over the last 1.00 game-days).\n"
            "At that rate, your energy runs out in about 300 ticks (~2.1 game-days) unless that changes.\n"
            "Upkeep accrued so far today: 40 sim-currency.\n"
            "Household h1 daily upkeep: 200 sim-currency.\n"
            "1 dependent(s) rely on this household's upkeep: kid1."
        )
        assert text == expected

    def test_zero_burn_states_the_rate_is_zero_not_infinite_runway(self) -> None:
        context = self.make_context(recent_burn_usd_cents=0)
        text = render_economic_context_block(context)
        assert "current burn rate is 0" in text
        assert "run out" not in text

    def test_no_household_states_the_rule_plainly(self) -> None:
        context = self.make_context(household=None)
        text = render_economic_context_block(context)
        assert "No household upkeep is charged to you right now." in text

    def test_household_with_no_dependents(self) -> None:
        context = self.make_context(
            household=HouseholdUpkeepView(household_id="h2", daily_upkeep_sim=50, dependent_agent_ids=())
        )
        text = render_economic_context_block(context)
        assert "No dependents currently rely on this household." in text

    def test_minor_wallet_owner_is_disclosed_as_the_parents(self) -> None:
        context = self.make_context(wallet_owner_agent_id="parent1", agent_id="kid1")
        text = render_economic_context_block(context)
        assert "parent1's energy" in text
        assert "kid1" not in text.split("\n")[2]

    def test_dependent_ids_are_named_literally(self) -> None:
        context = self.make_context(
            household=HouseholdUpkeepView(
                household_id="h3", daily_upkeep_sim=100, dependent_agent_ids=("kid1", "elder2")
            )
        )
        text = render_economic_context_block(context)
        assert "kid1" in text
        assert "elder2" in text

    def test_never_leaks_strategy_across_every_variation(self) -> None:
        variations = [
            self.make_context(),
            self.make_context(recent_burn_usd_cents=0),
            self.make_context(household=None),
            self.make_context(usd_wallet_cents=0, recent_burn_usd_cents=5),
            self.make_context(wallet_owner_agent_id="parent1", agent_id="kid1"),
            self.make_context(
                household=HouseholdUpkeepView(household_id="h4", daily_upkeep_sim=0, dependent_agent_ids=())
            ),
        ]
        for context in variations:
            assert_no_strategy_leak(render_economic_context_block(context))
