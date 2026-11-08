"""ECON-17: a resident must be quoted the price of the tool it named, and
told plainly when it named no tool at all.

THE DEFECT, as filed (docs/review/12-economy-and-life.md, ECON-17, P1):
`quote_action_cost` passed `params.tool_name` to the cost schedule
verbatim, and turned the schedule's `None` into `sim_cost=0` with the
words "no published sim-currency charge for this action". The engine
publishes every world tool under a namespace prefix
(engine/src/sim/world_tools.py's `WORLD_TOOL_NAME_PREFIX = "world:"`,
sanitised downstream to `world_`), so the name a resident reads in its
own tool list is `world:mine` / `world_mine` while the registry, the cost
schedule and the dispatch charge all use the bare `mine`. The observed
consequence is in the run evidence: docs/runs/run-23-report.md:1529-1532
shows n-something asking `world:quote_action_cost` about `world_mine`,
being answered `{"sim_cost": 0, ... "no published sim-currency charge for
this action"}`, and then calling `world:mine` on the next line - which
`ActionCostSchedule.cost_for` DOES charge, because unlike `quote` it
falls back to the default rule for a name with no row. The resident was
told an action was free, priced its whole plan on that, and was billed.

The same `None` also collapsed two different answers into one sentence:
"this tool exists and carries no charge" and "I have no idea what you are
naming" both rendered as free. Those are opposite pieces of news for
somebody deciding whether it can afford to act.

WHAT THIS FILE PINS. Four things, in the order they can regress:
`normalise_tool_name` resolves a prefixed name to the registry's own name
and refuses an unknown one; `quote_action_cost` prices the resolved name
and answers an unresolvable one as no-such-tool rather than as free, with
`known_tool` and `priced` telling the two apart; and - the tests that
matter most - the whole thing is REACHED through the real
`ToolRegistry.execute("quote_action_cost", ...)` path on a world built by
`bootstrap.build_world()`, with the real `DEFAULT_SCHEDULE` behind it.
The wiring cases exist because this codebase's commonest defect by far is
a correct, tested function that nothing calls: a test that only exercised
`normalise_tool_name` would stay green if the `known_tool_names=` argument
were dropped from the handler, and the run-23 failure would be back
untouched.

`test_the_live_registry_is_what_a_name_is_checked_against` guards a
narrower trap: `bootstrap.build_world` freezes
`WorldState.registry_tool_names` BEFORE it registers `quote_action_cost`
itself, so a vocabulary read only from that snapshot would answer "no such
tool" for the very tool doing the answering, and for every tool a resident
invents at runtime. The handler unions the live registry in, and that case
fails if it stops doing so.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from world.ledger.action_costs import ActionActor
from world.tools.cost_disclosure import (
    normalise_tool_name,
    quote_action_cost,
    world_tool_vocabulary,
)
from world.tools.tests.conftest import spawn_agent


class StubQuote(BaseModel):
    """Satisfies cost_disclosure.ActionCostQuoteLike structurally."""

    sim_cost: int
    basis: str


class StubSchedule:
    """A real dict lookup, not a mock: returns None for any name it has no
    row for, exactly as `ActionCostSchedule.quote` does.
    """

    def __init__(self, prices: dict[str, tuple[int, str]]) -> None:
        self.prices = prices
        self.asked: list[str] = []

    def quote(self, tool_name: str, params: dict | None = None) -> StubQuote | None:
        self.asked.append(tool_name)
        priced = self.prices.get(tool_name)
        if priced is None:
            return None
        return StubQuote(sim_cost=priced[0], basis=priced[1])


class TestNormaliseToolName:
    def test_a_bare_registered_name_is_returned_unchanged(self) -> None:
        assert normalise_tool_name("mine", {"mine", "say"}) == "mine"

    @pytest.mark.parametrize("asked", ["world:mine", "world_mine"])
    def test_either_engine_prefix_resolves_to_the_registry_name(self, asked: str) -> None:
        assert normalise_tool_name(asked, {"mine", "say"}) == "mine"

    def test_an_unknown_name_resolves_to_nothing(self) -> None:
        assert normalise_tool_name("mien", {"mine", "say"}) is None
        assert normalise_tool_name("world_mien", {"mine", "say"}) is None

    def test_a_literal_name_wins_over_the_stripped_one(self) -> None:
        # A tool genuinely called `world_map` must not be answered as `map`
        # just because both are registered.
        assert normalise_tool_name("world_map", {"world_map", "map"}) == "world_map"

    def test_an_unverifiable_name_is_stripped_not_refuted(self) -> None:
        # No vocabulary to check against (the pure function, no world
        # behind it): strip the prefix, but never claim a tool is absent
        # on no evidence.
        assert normalise_tool_name("world_mine", None) == "mine"
        assert normalise_tool_name("anything_at_all", None) == "anything_at_all"


class TestQuoteActionCost:
    def test_a_prefixed_name_is_priced_as_the_tool_it_names(self) -> None:
        schedule = StubSchedule({"mine": (12, "3 sim per unit")})
        quote = quote_action_cost(
            schedule, "world_mine", {"quantity": 4}, known_tool_names={"mine"}
        )
        assert quote.sim_cost == 12
        assert quote.tool_name == "mine"
        assert quote.requested_tool_name == "world_mine"
        assert quote.known_tool is True
        assert quote.priced is True
        # The schedule was asked about the name it actually has a row for.
        assert schedule.asked == ["mine"]

    def test_an_unknown_name_is_not_quoted_as_free(self) -> None:
        schedule = StubSchedule({"mine": (12, "3 sim per unit")})
        quote = quote_action_cost(
            schedule, "mien", current_sim_balance=500, known_tool_names={"mine"}
        )
        assert quote.known_tool is False
        assert quote.priced is False
        # Not "you can afford it": there is nothing to afford.
        assert quote.affordable is None
        assert quote.sim_balance_after is None
        assert "no published sim-currency charge" not in quote.summary
        assert "no published sim-currency charge" not in quote.basis
        assert "mien" in quote.summary
        # And the schedule was never consulted about a name it could only
        # have answered with a misleading None.
        assert schedule.asked == []

    def test_a_real_tool_with_no_published_price_still_quotes_as_free(self) -> None:
        # The other half of the collapsed answer: this one IS free, and
        # must keep saying so.
        schedule = StubSchedule({})
        quote = quote_action_cost(
            schedule, "look_around", current_sim_balance=500, known_tool_names={"look_around"}
        )
        assert quote.known_tool is True
        assert quote.priced is False
        assert quote.sim_cost == 0
        assert quote.affordable is True
        assert "no published sim-currency charge" in quote.basis


class TestThroughTheRealRegistry:
    """The wiring. `build_world()` (conftest's `world`/`registry` fixtures)
    with the real DEFAULT_SCHEDULE, driven through
    `ToolRegistry.execute`, which is the only path a resident's call ever
    takes.
    """

    @pytest.mark.asyncio
    async def test_run_23s_exact_call_is_priced_at_what_it_will_be_charged(
        self, world, registry
    ) -> None:
        await spawn_agent(world, "a1", "temple", starting_sim=50_000)
        call_params = {"metal": "copper", "quantity": 1}

        result = await registry.execute(
            world,
            "a1",
            "quote_action_cost",
            {"tool_name": "world_mine", "call_params": call_params},
        )

        quoted = result.result["sim_cost"]
        # The finding's whole point: this was 0, and mining was not free.
        assert quoted > 0
        assert result.result["tool_name"] == "mine"
        assert result.result["requested_tool_name"] == "world_mine"
        assert result.result["known_tool"] is True
        # The number a resident is quoted is the number dispatch charges.
        charged = world.action_costs.cost_for("mine", call_params, ActionActor(actor_id="a1"))
        assert quoted == charged

    @pytest.mark.asyncio
    async def test_the_colon_form_prices_the_same_as_the_bare_form(
        self, world, registry
    ) -> None:
        await spawn_agent(world, "a1", "temple", starting_sim=50_000)
        call_params = {"metal": "copper", "quantity": 3}

        prefixed = await registry.execute(
            world, "a1", "quote_action_cost",
            {"tool_name": "world:mine", "call_params": call_params},
        )
        bare = await registry.execute(
            world, "a1", "quote_action_cost",
            {"tool_name": "mine", "call_params": call_params},
        )

        assert prefixed.result["sim_cost"] == bare.result["sim_cost"] > 0
        assert prefixed.result["tool_name"] == bare.result["tool_name"] == "mine"

    @pytest.mark.asyncio
    async def test_a_name_no_tool_answers_to_is_refused_not_priced_at_zero(
        self, world, registry
    ) -> None:
        await spawn_agent(world, "a1", "temple", starting_sim=50_000)

        result = await registry.execute(
            world,
            "a1",
            "quote_action_cost",
            {"tool_name": "smelt_gold_from_nothing", "call_params": {}},
        )

        assert result.result["known_tool"] is False
        assert result.result["affordable"] is None
        assert "no published sim-currency charge" not in result.result["summary"]
        assert "smelt_gold_from_nothing" in result.result["summary"]

    @pytest.mark.asyncio
    async def test_the_live_registry_is_what_a_name_is_checked_against(
        self, world, registry
    ) -> None:
        # `quote_action_cost` is registered AFTER build_world freezes
        # `registry_tool_names`, so it is the proof that the vocabulary is
        # read live rather than off the snapshot.
        assert "quote_action_cost" not in world.registry_tool_names
        assert "quote_action_cost" in world_tool_vocabulary(world)

        await spawn_agent(world, "a1", "temple", starting_sim=50_000)
        result = await registry.execute(
            world,
            "a1",
            "quote_action_cost",
            {"tool_name": "world_quote_action_cost", "call_params": {}},
        )

        assert result.result["known_tool"] is True
        assert result.result["tool_name"] == "quote_action_cost"
