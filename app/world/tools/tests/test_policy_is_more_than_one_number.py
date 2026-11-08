"""GOV-4: policy is more than one number.

The checklist's bar: "a policy book with at least four levers read at
dispatch time (tax, margin, price caps or floors, licensing)". When it was
written exactly one lever was read at all - the head-tax rate, at the day
boundary. Since then the credit-store margin is read by `buy_energy`
(ECON-30) and the pension terms by retirement. What did not exist were the
two the item names: price controls and business licensing.

Now a sitting government can set, at the government complex:

  * a PRICE CAP and a PRICE FLOOR per item - read when a price is posted
    and again when a sale settles, so a price outside the band can neither
    be offered nor paid;
  * BUSINESS LICENSING - "open", or "state_only" (only a sitting seat may
    found a business) - read when a business is registered.

Each change is a new policy version, like the tax rate, and emits an event.
Nobody but a sitting seat can set any of them. With the defaults - no caps,
no floors, open licensing - the market is exactly as free as it was.
"""

from __future__ import annotations

import pytest

from world.charter.models import GovernmentPolicy, GovernmentState
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX
from world.tools.models import PricePost
from world.tools.tests.conftest import in_conversation, spawn_agent


async def _a_government(world) -> None:
    await spawn_agent(world, "pm", LOCATION_GOVERNMENT_COMPLEX)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="pm", minister_agent_ids=[])
    )


async def _refused(registry, world, actor, tool, params) -> str:
    try:
        result = await registry.execute(world, actor, tool, params)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return str(result.error) if getattr(result, "error", None) else ""


# ── the book ──────────────────────────────────────────────────────────


def test_the_defaults_are_a_free_market() -> None:
    policy = GovernmentPolicy()

    assert policy.price_caps == {} and policy.price_floors == {}
    assert policy.business_licensing == "open"


def test_a_floor_above_its_cap_is_not_a_policy() -> None:
    with pytest.raises(ValueError):
        GovernmentPolicy(price_caps={"bread": 10}, price_floors={"bread": 20})


# ── setting it ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_a_sitting_seat_can_set_a_price_control(world, registry):
    await _a_government(world)
    await spawn_agent(world, "citizen", LOCATION_GOVERNMENT_COMPLEX)

    why = await _refused(registry, world, "citizen", "set_price_control",
                         {"item": "bread", "max_sim": 20})

    assert why, "a citizen set a price control"
    assert (await world.charter.store.get_policy()).price_caps == {}


@pytest.mark.asyncio
async def test_a_seat_sets_a_cap_and_it_is_a_new_version(world, registry):
    await _a_government(world)
    before = (await world.charter.store.get_policy()).version

    assert await _refused(registry, world, "pm", "set_price_control",
                          {"item": "bread", "max_sim": 20}) == ""

    policy = await world.charter.store.get_policy()
    assert policy.price_caps == {"bread": 20}
    assert policy.version == before + 1


@pytest.mark.asyncio
async def test_a_control_can_be_lifted(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_price_control", {"item": "bread", "max_sim": 20})

    await registry.execute(world, "pm", "set_price_control", {"item": "bread", "clear": True})

    policy = await world.charter.store.get_policy()
    assert "bread" not in policy.price_caps and "bread" not in policy.price_floors


# ── read at dispatch: posting ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_price_above_the_cap_cannot_be_posted(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_price_control", {"item": "bread", "max_sim": 20})
    await spawn_agent(world, "baker", "market_square")
    world.inventory.credit("baker", "bread", 3)

    why = await _refused(registry, world, "baker", "post_price", {"item": "bread", "ask_sim": 30})

    assert "20" in why and "cap" in why.lower()
    assert world.posted("baker", "bread") is None


@pytest.mark.asyncio
async def test_a_price_below_the_floor_cannot_be_posted(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_price_control", {"item": "wheat", "min_sim": 10})
    await spawn_agent(world, "farmer", "market_square")

    why = await _refused(registry, world, "farmer", "post_price", {"item": "wheat", "ask_sim": 4})

    assert "10" in why and "floor" in why.lower()


@pytest.mark.asyncio
async def test_a_price_inside_the_band_posts(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_price_control",
                           {"item": "bread", "max_sim": 20, "min_sim": 5})
    await spawn_agent(world, "baker", "market_square")

    assert await _refused(registry, world, "baker", "post_price", {"item": "bread", "ask_sim": 12}) == ""


# ── read at dispatch: settling ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_price_posted_before_the_cap_cannot_be_paid_after_it(world, registry):
    """The cap binds the sale, not only the sign in the window."""
    await spawn_agent(world, "buyer", "market_square")
    await spawn_agent(world, "seller", "market_square")
    world.ledger.get_account("buyer").sim_units = 5_000
    world.price_board["seller"] = {"bread": PricePost(item="bread", ask_sim=40)}
    world.inventory.credit("seller", "bread", 3)
    in_conversation(world, "buyer", "seller")
    await _a_government(world)
    await registry.execute(world, "pm", "set_price_control", {"item": "bread", "max_sim": 20})

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "bread", "quantity": 1})

    assert "cap" in why.lower()
    assert world.inventory.quantity("seller", "bread") == 3


@pytest.mark.asyncio
async def test_a_trade_below_the_floor_is_refused(world, registry):
    await spawn_agent(world, "a", "market_square")
    await spawn_agent(world, "b", "market_square")
    world.inventory.credit("a", "wheat", 5)
    world.ledger.get_account("b").sim_units = 5_000
    in_conversation(world, "a", "b")
    await _a_government(world)
    await registry.execute(world, "pm", "set_price_control", {"item": "wheat", "min_sim": 10})

    why = await _refused(registry, world, "a", "trade",
                         {"counterparty_agent_id": "b", "item": "wheat", "quantity": 2, "price_sim": 8})

    assert "floor" in why.lower()


# ── read at dispatch: founding a business ─────────────────────────────


@pytest.mark.asyncio
async def test_state_only_licensing_stops_a_citizen_founding_a_business(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_business_licensing", {"mode": "state_only"})
    await spawn_agent(world, "founder", LOCATION_GOVERNMENT_COMPLEX)
    world.ledger.get_account("founder").sim_units = 50_000

    why = await _refused(registry, world, "founder", "register_business",
                         {"business_id": "b_new", "name": "New Shop", "business_type": "shop"})

    assert "state" in why.lower() or "licens" in why.lower()
    assert "b_new" not in world.businesses


@pytest.mark.asyncio
async def test_licensing_can_only_be_one_of_the_named_modes(world, registry):
    await _a_government(world)

    why = await _refused(registry, world, "pm", "set_business_licensing", {"mode": "anything_goes"})

    assert why


@pytest.mark.asyncio
async def test_permit_required_asks_for_a_permit_and_a_seat_can_issue_one(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_business_licensing", {"mode": "permit_required"})
    await spawn_agent(world, "founder", LOCATION_GOVERNMENT_COMPLEX)
    shop = {"business_id": "b_new", "name": "New Shop", "business_type": "shop"}

    why = await _refused(registry, world, "founder", "register_business", shop)
    assert "permit" in why.lower() and "b_new" not in world.businesses

    fee = (await world.charter.store.get_charter()).permit_fee_sim
    before = world.ledger.sim_balance("founder")
    assert await _refused(registry, world, "pm", "issue_business_permit",
                          {"holder_agent_id": "founder"}) == ""
    assert world.ledger.sim_balance("founder") == before - fee

    assert await _refused(registry, world, "founder", "register_business", shop) == ""
    assert "b_new" in world.businesses


@pytest.mark.asyncio
async def test_a_founder_refused_by_licensing_is_charged_nothing(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_business_licensing", {"mode": "state_only"})
    await spawn_agent(world, "founder", LOCATION_GOVERNMENT_COMPLEX)
    before = world.ledger.sim_balance("founder")

    await _refused(registry, world, "founder", "register_business",
                   {"business_id": "b_new", "name": "New Shop", "business_type": "shop"})

    from world.tools.tests.conftest import action_cost
    assert world.ledger.sim_balance("founder") >= before - action_cost("register_business")


@pytest.mark.asyncio
async def test_a_seat_may_still_found_under_state_only(world, registry):
    await _a_government(world)
    await registry.execute(world, "pm", "set_business_licensing", {"mode": "state_only"})

    assert await _refused(registry, world, "pm", "register_business",
                          {"business_id": "b_state", "name": "State Store", "business_type": "shop"}) == ""


def test_the_book_reads_at_least_four_levers_at_dispatch() -> None:
    """The item's own bar: every lever is read by the code that acts on it."""
    import inspect

    from world import run_genesis
    from world.tools.primitives import civic, commerce

    read_by = {
        "tax_rate_bps": inspect.getsource(run_genesis),
        "credit_store_margin_bps": inspect.getsource(civic.buy_energy_handler),
        "price_caps": inspect.getsource(commerce._require_price_in_band),
        "price_floors": inspect.getsource(commerce._require_price_in_band),
        "business_licensing": inspect.getsource(civic._require_licensed_to_found),
    }
    for lever, source in read_by.items():
        assert lever in source, f"{lever} is not read where it should act"
    for handler in (commerce.buy_handler, commerce.sell_handler,
                    commerce.trade_handler, commerce.post_price_handler):
        assert "_require_price_in_band" in inspect.getsource(handler), handler.__name__
    assert "_require_licensed_to_found" in inspect.getsource(civic.register_business_handler)
