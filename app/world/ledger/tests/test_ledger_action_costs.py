"""ActionCostSchedule: DESIGN.md section 3's "Everything costs" law.

Every category cost_for() prices (labour, trade, social, travel, fee),
the affordability refusal, the never-negative-balance guarantee, and the
ledger invariant across a long randomized run of charged actions - see
this file's own module docstring split below for exactly which test
proves which of this task's PROVE bullets.

test_schedule_covers_every_registered_tool_in_the_real_build_world is the
one test in this file that talks to a REAL ToolRegistry (world.tools.
bootstrap.build_world(), no mock) rather than a hand-picked tool-name
list - the project's own lesson ("every serious bug came from two modules
built against an assumed contract, both green because each mocked the
other side") applies exactly here: a hand-maintained mirror of "every
registered tool name" is precisely the kind of assumed contract that
drifts silently the moment another agent adds a tool elsewhere.
"""

from __future__ import annotations

import random

import pytest
from pydantic import BaseModel

from world.ledger.action_costs import (
    ActionActor,
    ActionCostCategory,
    ActionCostRule,
    ActionCostSchedule,
    ActionUnaffordableError,
    DEFAULT_RULE,
    DEFAULT_SCHEDULE,
    SCHEDULE_TABLE,
    charge_for_action,
    cost_for,
    memo_for,
    tool_name_from_memo,
)
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger

ACTOR = ActionActor(actor_id="a1")


# -- labour -------------------------------------------------------------


def test_labour_cost_scales_with_quantity() -> None:
    small = cost_for("harvest", {"crop": "wheat", "quantity": 1}, ACTOR)
    large = cost_for("harvest", {"crop": "wheat", "quantity": 10}, ACTOR)
    assert small == 2 + 3 * 1
    assert large == 2 + 3 * 10
    assert large > small


def test_labour_cost_gets_a_skill_discount_at_high_proficiency() -> None:
    novice = cost_for("craft", {"recipe_id": "x", "batches": 10}, ActionActor("a1", skill_proficiency=0))
    master = cost_for("craft", {"recipe_id": "x", "batches": 10}, ActionActor("a1", skill_proficiency=100))
    assert novice == 6 + 8 * 10  # 86, no discount at proficiency 0
    assert master < novice
    assert master == 86 - (86 * 5_000 // 10_000)  # 50% max discount, floor-divided


def test_skill_discount_never_pushes_cost_below_the_rule_floor() -> None:
    # quantity=0 pushes the raw cost down to exactly base_cost (== min_cost
    # for harvest); even at maximum proficiency the discount cannot cross
    # the floor.
    cost = cost_for(
        "harvest", {"crop": "wheat", "quantity": 0}, ActionActor("a1", skill_proficiency=100)
    )
    assert cost == SCHEDULE_TABLE["harvest"].min_cost == 2


def test_skill_discount_clamps_proficiency_above_the_cap() -> None:
    at_cap = cost_for("craft", {"recipe_id": "x", "batches": 10}, ActionActor("a1", skill_proficiency=100))
    way_above = cost_for("craft", {"recipe_id": "x", "batches": 10}, ActionActor("a1", skill_proficiency=99_999))
    assert at_cap == way_above


def test_steal_is_labour_but_never_discounted_by_skill() -> None:
    rule = SCHEDULE_TABLE["steal"]
    assert rule.category is ActionCostCategory.LABOUR
    assert rule.skill_discount is False
    novice = cost_for("steal", {"storage_id": "s", "item": "wheat", "quantity": 1}, ActionActor("a1"))
    master = cost_for(
        "steal", {"storage_id": "s", "item": "wheat", "quantity": 1}, ActionActor("a1", skill_proficiency=100)
    )
    assert novice == master


def test_build_structure_labour_cost_scales_with_footprint_tiles() -> None:
    footprint = [(0, 0), (1, 0), (0, 1), (1, 1)]
    cost = cost_for(
        "build_structure",
        {"plot_id": "p1", "footprint": footprint, "purpose": "house"},
        ACTOR,
    )
    assert cost == 25 + 15 * len(footprint)


# -- trade ----------------------------------------------------------------


def test_buy_cost_is_one_percent_of_total_price() -> None:
    cost = cost_for("buy", {"business_id": "b", "item": "bread", "quantity": 20, "unit_price_sim": 50}, ACTOR)
    assert cost == (20 * 50) * 100 // 10_000  # 10


def test_trade_cost_never_falls_below_its_floor_on_a_tiny_trade() -> None:
    cost = cost_for("buy", {"business_id": "b", "item": "bread", "quantity": 1, "unit_price_sim": 1}, ACTOR)
    assert cost == SCHEDULE_TABLE["buy"].min_cost == 2


def test_sell_cost_matches_buy_rate() -> None:
    params = {"business_id": "b", "item": "bread", "quantity": 20, "unit_price_sim": 50}
    assert cost_for("sell", params, ACTOR) == cost_for("buy", params, ACTOR)


def test_transfer_cost_is_half_a_percent_of_amount_moved() -> None:
    cost = cost_for("transfer", {"to_agent_id": "a2", "amount_sim": 10_000}, ACTOR)
    assert cost == 10_000 * 50 // 10_000  # 50


def test_storage_handling_fee_scales_with_quantity_not_value() -> None:
    cost = cost_for("store_item", {"storage_id": "s", "item": "wheat", "quantity": 7}, ACTOR)
    assert cost == 1 + 1 * 7


# -- social -----------------------------------------------------------------


def test_social_costs_are_flat_regardless_of_params() -> None:
    assert cost_for("marry", {"spouse_agent_id": "x", "new_household_id": "h"}, ACTOR) == 30
    assert cost_for("pray", {"message": "help", "donation_sim": 500}, ACTOR) == 3
    assert cost_for("pray", {}, ACTOR) == 3
    assert (
        cost_for(
            "teach_skill",
            {"student_agent_id": "s", "skill": "farming", "student_consent": True, "price_sim": 1000},
            ACTOR,
        )
        == 15
    )


# -- travel -------------------------------------------------------------


def test_move_cost_scales_with_resolved_distance() -> None:
    near = cost_for("move", {"destination_location_id": "mill"}, ActionActor("a1", distance_tiles=2))
    far = cost_for("move", {"destination_location_id": "mill"}, ActionActor("a1", distance_tiles=40))
    assert near == 1 + 2 * 2
    assert far == 1 + 2 * 40
    assert far > near


def test_move_still_costs_something_with_unresolved_distance() -> None:
    # The integration point's default when it has not yet resolved a real
    # tile distance (module docstring: "always safe ... never a free
    # ride, just a coarser price").
    cost = cost_for("move", {"destination_location_id": "mill"}, ActionActor("a1"))
    assert cost == SCHEDULE_TABLE["move"].min_cost == 1


# -- fees -----------------------------------------------------------------


def test_civic_and_administrative_fees_are_flat() -> None:
    assert cost_for("vote", {"election_id": "e", "candidate_agent_id": "c"}, ACTOR) == 2
    assert cost_for("petition", {"title": "t", "description": "d", "tile_count_requested": 5}, ACTOR) == 100
    assert cost_for("register_org", {"kind": "party", "name": "n", "charter_text": "t"}, ACTOR) == 3_000
    assert cost_for("file_complaint", {"against_agent_id": "x", "description": "d"}, ACTOR) == 10
    assert cost_for("hire_employee", {"business_id": "b", "employee_agent_id": "e"}, ACTOR) == 10
    assert cost_for("fire_employee", {"business_id": "b", "employee_agent_id": "e"}, ACTOR) == 10
    assert cost_for("transfer_business", {"business_id": "b", "new_owner_agent_id": "o"}, ACTOR) == 20
    assert cost_for("file_subpoena", {"case_id": "c", "target_agent_id": "t", "justification": "j"}, ACTOR) == 25
    assert cost_for("grant_subpoena", {"subpoena_id": "s"}, ACTOR) == 25
    assert cost_for("deny_subpoena", {"subpoena_id": "s", "reason": "r"}, ACTOR) == 10
    assert cost_for("approve_patent", {"application_id": "a"}, ACTOR) == 15
    assert cost_for("reject_patent", {"application_id": "a", "reason": "r"}, ACTOR) == 10
    assert cost_for("revoke_patent", {"patent_id": "p", "tool_name": "t", "reason": "r"}, ACTOR) == 20


def test_rows_with_a_pre_existing_handler_side_charge_are_zero() -> None:
    """register_business and define_tool already move sim-currency inside
    their own handler (civic.py, meta.py) - see module docstring's "Known
    double-charge risk". This schedule must not charge them a second time.
    """
    assert cost_for("register_business", {"business_id": "b", "name": "n", "business_type": "t"}, ACTOR) == 0
    assert cost_for(
        "define_tool", {"name": "n", "description": "d", "param_schema": {}, "steps": []}, ACTOR
    ) == 0


def test_business_and_organization_trade_secret_tools_are_free_by_design() -> None:
    """DESIGN.md section 2: business/organization composite tools need
    "no patent office needed" - zero is the correct price, not a gap."""
    assert cost_for(
        "define_business_tool",
        {"business_id": "b", "name": "n", "description": "d", "param_schema": {}, "steps": []},
        ACTOR,
    ) == 0
    assert cost_for(
        "define_organization_tool",
        {"org_id": "o", "name": "n", "description": "d", "param_schema": {}, "steps": []},
        ACTOR,
    ) == 0


def test_unknown_tool_name_falls_back_to_a_nonzero_default() -> None:
    cost = cost_for("some_future_tool_nobody_has_added_yet", {}, ACTOR)
    assert cost == DEFAULT_RULE.min_cost
    assert cost > 0


# -- purity / determinism ----------------------------------------------------


def test_cost_for_is_deterministic() -> None:
    params = {"crop": "wheat", "quantity": 5}
    first = cost_for("harvest", params, ActionActor("a1", skill_proficiency=40))
    second = cost_for("harvest", params, ActionActor("a1", skill_proficiency=40))
    assert first == second


def test_cost_for_does_not_mutate_its_inputs() -> None:
    params = {"crop": "wheat", "quantity": 5}
    params_copy = dict(params)
    actor = ActionActor("a1", skill_proficiency=40, distance_tiles=3)
    cost_for("harvest", params, actor)
    assert params == params_copy
    assert actor == ActionActor("a1", skill_proficiency=40, distance_tiles=3)


class FakeBuyParams(BaseModel):
    business_id: str
    item: str
    quantity: int
    unit_price_sim: int


def test_cost_for_accepts_a_validated_pydantic_params_model_too() -> None:
    """The real integration point (world/tools/registry.py) hands cost_for
    an already-validated params BaseModel, not a raw dict - see module
    docstring's "Integration point" section."""
    model = FakeBuyParams(business_id="b", item="bread", quantity=20, unit_price_sim=50)
    from_model = cost_for("buy", model, ACTOR)
    from_dict = cost_for("buy", model.model_dump(), ACTOR)
    assert from_model == from_dict == 10


# -- quote(): the disclosure surface world.tools.cost_disclosure.py assumed --


def test_quote_returns_the_per_unit_reference_rate_with_no_params() -> None:
    quote = DEFAULT_SCHEDULE.quote("move")
    assert quote is not None
    assert quote.sim_cost == SCHEDULE_TABLE["move"].per_unit_cost == 2
    assert quote.basis == SCHEDULE_TABLE["move"].description


def test_quote_returns_the_flat_number_for_a_flat_rule_with_no_params() -> None:
    quote = DEFAULT_SCHEDULE.quote("pray")
    assert quote is not None
    assert quote.sim_cost == 3


def test_quote_returns_the_floor_for_a_value_proportional_rule_with_no_params() -> None:
    quote = DEFAULT_SCHEDULE.quote("buy")
    assert quote is not None
    assert quote.sim_cost == SCHEDULE_TABLE["buy"].min_cost == 2


def test_quote_resolves_the_real_total_once_real_params_are_given() -> None:
    params = {"business_id": "b", "item": "bread", "quantity": 20, "unit_price_sim": 50}
    quote = DEFAULT_SCHEDULE.quote("buy", params)
    assert quote is not None
    assert quote.sim_cost == cost_for("buy", params, ActionActor(""))


def test_quote_returns_none_for_a_tool_name_with_no_row_at_all() -> None:
    assert DEFAULT_SCHEDULE.quote("some_future_tool_nobody_has_added_yet") is None


def test_quote_discloses_a_deliberately_free_row_as_zero_not_none() -> None:
    # define_business_tool really is priced (at 0, by design - see
    # SCHEDULE_TABLE) - that is a different fact than "no row exists",
    # and quote() must not conflate the two.
    quote = DEFAULT_SCHEDULE.quote("define_business_tool")
    assert quote is not None
    assert quote.sim_cost == 0


def test_quote_interoperates_with_the_real_cost_disclosure_module() -> None:
    """world/tools/cost_disclosure.py was built concurrently against an
    explicitly documented assumption about this module's shape (its own
    "ASSUMED INTERFACE" docstring section). This test imports ITS real,
    unmodified pure functions and calls them against a real instance of
    THIS module's schedule - proving the two independently-built modules
    actually interoperate, not just that each one's own tests pass in
    isolation (the project's own lesson about mocked seams).
    """
    from world.tools.cost_disclosure import annotate_tool_description, describe_cost, quote_action_cost

    disclosed = describe_cost("harvest", DEFAULT_SCHEDULE)
    assert disclosed == "Cost: 3 sim-currency (tool wear per unit of crop harvested)."

    annotated = annotate_tool_description("Harvest a crop from the farmland strip.", "harvest", DEFAULT_SCHEDULE)
    assert annotated == (
        "Harvest a crop from the farmland strip. "
        "Cost: 3 sim-currency (tool wear per unit of crop harvested)."
    )

    quote = quote_action_cost(
        DEFAULT_SCHEDULE,
        "buy",
        {"business_id": "b", "item": "bread", "quantity": 20, "unit_price_sim": 50},
        current_sim_balance=100,
    )
    assert quote.tool_name == "buy"
    assert quote.sim_cost == 10
    assert quote.sim_balance_after == 90
    assert quote.affordable is True

    poor_quote = quote_action_cost(DEFAULT_SCHEDULE, "register_org", current_sim_balance=10)
    assert poor_quote.sim_cost == 3_000
    assert poor_quote.affordable is False

    # An uncosted tool name (no SCHEDULE_TABLE row) reads as genuinely
    # free/uncosted through their disclosure text, not DEFAULT_RULE's
    # enforcement-side fallback number - see quote()'s own docstring for
    # why that distinction is deliberate.
    assert describe_cost("a_tool_nobody_has_invented", DEFAULT_SCHEDULE) == (
        "Cost: no published sim-currency charge for this action."
    )
    # quote_action_cost used to be the example here, precisely because it had
    # no row. It has one now (an explicit 0 - bootstrap.py registers the tool,
    # and every registered tool must carry a real price), so the distinction
    # this asserts needs a name the table genuinely does not know.
    assert describe_cost("quote_action_cost", DEFAULT_SCHEDULE).startswith("Cost: 0 sim-currency")


def test_describe_returns_json_able_rows_sorted_by_tool_name() -> None:
    rows = DEFAULT_SCHEDULE.describe()
    tool_names = [row["tool_name"] for row in rows]
    assert tool_names == sorted(tool_names)
    assert len(rows) == len(SCHEDULE_TABLE)
    harvest_row = next(row for row in rows if row["tool_name"] == "harvest")
    assert harvest_row["category"] == "labour"
    assert harvest_row["base_cost"] == 2


# -- charging through the real ledger --------------------------------------


@pytest.mark.asyncio
async def test_charge_moves_sim_from_actor_to_treasury_and_attributes_the_tool() -> None:
    ledger = await build_genesis_ledger(["farmer"], sim_per_agent=1_000)

    transaction = await charge_for_action(
        ledger, "harvest", {"crop": "wheat", "quantity": 10}, ActionActor("farmer"), tick=1
    )

    assert transaction is not None
    assert transaction.amount == 32  # 2 + 3*10
    assert transaction.currency is Currency.SIM
    # ECON-03: an action cost is a BILL and now says so. It was filed as
    # TRANSFER, which made it indistinguishable from a resident handing
    # money away - and `run_report` groups this table by `reason`, so
    # neither GDP-per-inference-dollar nor velocity could be computed.
    assert transaction.reason is TransactionReason.ACTION_COST
    assert transaction.from_account == "farmer"
    assert transaction.to_account == TREASURY_ACCOUNT_ID
    assert tool_name_from_memo(transaction) == "harvest"
    assert transaction.memo == memo_for("harvest")
    assert ledger.sim_balance("farmer") == 1_000 - 32
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 32


@pytest.mark.asyncio
async def test_charge_returns_none_and_moves_nothing_for_a_zero_cost_tool() -> None:
    ledger = await build_genesis_ledger(["founder"], sim_per_agent=1_000)
    transactions_before = len(ledger.transactions)

    transaction = await charge_for_action(
        ledger,
        "define_business_tool",
        {"business_id": "b", "name": "n", "description": "d", "param_schema": {}, "steps": []},
        ActionActor("founder"),
        tick=1,
    )

    assert transaction is None
    assert ledger.sim_balance("founder") == 1_000
    assert len(ledger.transactions) == transactions_before


@pytest.mark.asyncio
async def test_charge_refuses_when_balance_cannot_cover_the_cost() -> None:
    ledger = await build_genesis_ledger(["pauper"], sim_per_agent=10)
    transactions_before = len(ledger.transactions)

    with pytest.raises(ActionUnaffordableError) as excinfo:
        await charge_for_action(
            ledger, "register_org", {"kind": "party", "name": "n", "charter_text": "t"}, ActionActor("pauper"), tick=1
        )

    assert excinfo.value.tool_name == "register_org"
    assert excinfo.value.actor_id == "pauper"
    assert excinfo.value.cost == 3_000
    assert excinfo.value.balance == 10
    assert excinfo.value.shortfall == 2_990
    # Refused, not charged: balance and the transaction log are both
    # untouched.
    assert ledger.sim_balance("pauper") == 10
    assert len(ledger.transactions) == transactions_before


@pytest.mark.asyncio
async def test_a_refusal_never_lets_the_balance_go_negative() -> None:
    ledger = await build_genesis_ledger(["worker"], sim_per_agent=2)  # exactly one vote's worth

    # Affordable: balance covers it exactly.
    first = await charge_for_action(
        ledger, "vote", {"election_id": "e", "candidate_agent_id": "c"}, ActionActor("worker"), tick=1
    )
    assert first is not None
    assert ledger.sim_balance("worker") == 0

    # Now broke: even the cheapest fee tool in the table is refused, not
    # charged into debt.
    with pytest.raises(ActionUnaffordableError):
        await charge_for_action(
            ledger, "vote", {"election_id": "e", "candidate_agent_id": "c"}, ActionActor("worker"), tick=2
        )
    assert ledger.sim_balance("worker") == 0  # never negative, never re-debited


@pytest.mark.asyncio
async def test_ledger_invariant_holds_across_a_long_random_sequence_of_charged_actions() -> None:
    """This task's PROVE bullet, literally: a long random sequence of
    charged actions across every category, asserting after EVERY single
    charge that (a) the actor's sim balance is never negative, (b) the
    ledger has not halted, and (c) the $1000 USD invariant still holds
    (sim-only churn must never disturb it) - then, once the actor is
    deliberately drained to insolvency, that refusals keep behaving
    correctly for another batch of attempts and every successful charge's
    amount reconciles exactly against the balance actually lost and the
    treasury actually gained.
    """
    rng = random.Random(20260731)
    ledger = await build_genesis_ledger(["citizen"], sim_per_agent=200_000)
    tick = 0

    calls = [
        ("harvest", lambda: {"crop": "wheat", "quantity": rng.randint(1, 20)}),
        ("mine", lambda: {"metal": "iron", "quantity": rng.randint(1, 20)}),
        ("fell_timber", lambda: {"quantity": rng.randint(1, 20)}),
        ("craft", lambda: {"recipe_id": "r", "batches": rng.randint(1, 10)}),
        ("cook", lambda: {"recipe_id": "r", "batches": rng.randint(1, 10)}),
        ("buy", lambda: {"business_id": "b", "item": "x", "quantity": rng.randint(1, 20), "unit_price_sim": rng.randint(1, 50)}),
        ("sell", lambda: {"business_id": "b", "item": "x", "quantity": rng.randint(1, 20), "unit_price_sim": rng.randint(1, 50)}),
        ("transfer", lambda: {"to_agent_id": "other", "amount_sim": rng.randint(1, 5_000)}),
        ("store_item", lambda: {"storage_id": "s", "item": "x", "quantity": rng.randint(1, 10)}),
        ("withdraw_item", lambda: {"storage_id": "s", "item": "x", "quantity": rng.randint(1, 10)}),
        ("marry", lambda: {"spouse_agent_id": "x", "new_household_id": "h"}),
        ("pray", lambda: {"message": "m", "donation_sim": 0}),
        ("teach_skill", lambda: {"student_agent_id": "s", "skill": "farming", "student_consent": True}),
        ("move", lambda: {"destination_location_id": "mill"}),
        ("vote", lambda: {"election_id": "e", "candidate_agent_id": "c"}),
        ("file_complaint", lambda: {"against_agent_id": "x", "description": "d"}),
        ("hire_employee", lambda: {"business_id": "b", "employee_agent_id": "e"}),
    ]

    starting_balance = ledger.sim_balance("citizen")
    successful_amounts = 0

    for _ in range(400):
        tick += 1
        tool_name, build_params = rng.choice(calls)
        actor = ActionActor(
            "citizen", skill_proficiency=rng.randint(0, 150), distance_tiles=rng.randint(0, 50)
        )
        transaction = await charge_for_action(ledger, tool_name, build_params(), actor, tick=tick)

        assert ledger.halted is False
        ledger.check_invariant()  # must not raise: sim-only movement never trips the USD tripwire
        balance = ledger.sim_balance("citizen")
        assert balance >= 0

        if transaction is not None:
            assert tool_name_from_memo(transaction) == tool_name
            successful_amounts += transaction.amount

    assert ledger.sim_balance("citizen") == starting_balance - successful_amounts
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == successful_amounts

    # Now drain deliberately: keep spending on `petition` until it is refused
    # for lack of funds, proving the refusal path is reached for real (not
    # just asserted never to trigger above) and still leaves the ledger in a
    # consistent, non-negative state.
    #
    # An earlier draft computed the cheapest tool in the whole table here and
    # meant to drain past THAT, but never wrote the assertion - so the
    # variable sat unused and the comment described a stronger test than the
    # one below. The comment now describes what actually runs.
    refused = 0
    for _ in range(100_000):
        tick += 1
        try:
            await charge_for_action(ledger, "petition", {"title": "t", "description": "d", "tile_count_requested": 1}, ActionActor("citizen"), tick=tick)
        except ActionUnaffordableError:
            refused += 1
            if refused > 3:
                break
        assert ledger.sim_balance("citizen") >= 0
        assert ledger.halted is False

    assert refused > 0
    final_balance_before_probe = ledger.sim_balance("citizen")
    with pytest.raises(ActionUnaffordableError):
        await charge_for_action(
            ledger, "petition", {"title": "t", "description": "d", "tile_count_requested": 1}, ActionActor("citizen"), tick=tick + 1
        )
    assert ledger.sim_balance("citizen") == final_balance_before_probe  # refusal moved nothing
    ledger.check_invariant()


@pytest.mark.asyncio
async def test_schedule_covers_every_registered_tool_in_the_real_build_world() -> None:
    """Proves SCHEDULE_TABLE against the REAL tool registry, not a
    hand-maintained mirror of tool names - see this file's module
    docstring. world.tools.bootstrap.build_world() is the same
    composition root world/main.py boots in production.
    """
    from world.tools.bootstrap import build_world

    world_state, registry = await build_world(seed=1)
    registered_tool_names = set(registry.tools.keys())

    missing_from_schedule = registered_tool_names - set(SCHEDULE_TABLE.keys())
    stale_in_schedule = set(SCHEDULE_TABLE.keys()) - registered_tool_names

    assert not missing_from_schedule, (
        f"tool(s) registered by build_world() with no SCHEDULE_TABLE row: {missing_from_schedule}"
    )
    assert not stale_in_schedule, (
        f"SCHEDULE_TABLE row(s) for a tool build_world() no longer registers: {stale_in_schedule}"
    )
    del world_state  # unused beyond proving build_world() itself succeeds


def test_a_custom_small_schedule_can_be_built_for_focused_tests() -> None:
    """ActionCostSchedule itself takes rules as a constructor argument
    (not only the module-level SCHEDULE_TABLE) - this is what lets this
    file's own smaller, targeted tests exist without asserting against
    the full 33-row production table every time."""
    tiny = ActionCostSchedule(
        rules={"widget": ActionCostRule(category=ActionCostCategory.FEE, base_cost=7, min_cost=7)},
        default_rule=DEFAULT_RULE,
    )
    assert tiny.cost_for("widget", {}, ACTOR) == 7
    assert tiny.cost_for("not_widget", {}, ACTOR) == DEFAULT_RULE.min_cost
