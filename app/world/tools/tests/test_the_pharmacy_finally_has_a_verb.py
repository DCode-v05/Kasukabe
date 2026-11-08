"""The pharmacy had a purpose, a price, a payment path - and no verb.

ECON-12, named in `life/health.py`'s own docstring: "The pharmacy is the one
genesis building whose advertised purpose ('Buy treatment for short
illnesses', tools/primitives/places.py) has never had a tool behind it."

`treat_at_pharmacy` was written and tested ten times over. Every reference to
it in the tree was its own definition or one of those tests. Nothing a
resident could call reached it, so nobody in any run has ever been cured of
anything by paying for it.

It cost more than the cure. `economy_bridge` routes a `pharmacy_treatment`
fee to the business that earns it rather than to the treasury (ECON-29), so
the pharmacy's entire trade was a payment path with nothing able to fire it:
the shop could be stocked, priced and staffed and still had no way to earn
from the one service it exists to sell.
"""

from __future__ import annotations

import pytest

from world.life.constants import PHARMACY_SHORT_TREATMENT_COST_SIM
from world.life.models import Ailment, AilmentKind
from world.tools.errors import ActorRequirementError, LocationGateError
from world.tools.models import Business
from world.tools.tests.conftest import spawn_agent

PREMISES = "business:b_pharmacy"


async def _a_pharmacy(world, owner: str = "chemist") -> Business:
    shop = Business(
        business_id="b_pharmacy",
        name="Kasukabe Pharmacy",
        business_type="pharmacy",
        owner_agent_id=owner,
        location_id=PREMISES,
    )
    world.businesses["b_pharmacy"] = shop
    await spawn_agent(world, owner, PREMISES)
    world.ledger.open_business("b_pharmacy", owner)
    return shop


def _make_ill(world, who: str, kind: AilmentKind = AilmentKind.SHORT) -> None:
    rec = world.agents[who]
    world.agents[who] = rec.model_copy(
        update={
            "health": rec.health.model_copy(
                update={"current_ailment": Ailment(kind=kind, onset_day=0, duration_days=5)}
            )
        }
    )


@pytest.mark.asyncio
async def test_a_sick_resident_can_buy_the_cure(world, registry) -> None:
    """The finding itself: a verb that reaches `treat_at_pharmacy`."""
    await _a_pharmacy(world)
    await spawn_agent(world, "patient", PREMISES)
    _make_ill(world, "patient")

    await registry.execute(world, "patient", "buy_treatment", {})

    assert world.agents["patient"].health.current_ailment is None, "still ill"


@pytest.mark.asyncio
async def test_the_fee_reaches_the_pharmacy_and_not_the_treasury(world, registry) -> None:
    """ECON-29's whole point, which had nothing able to exercise it.

    "A resident who paid a pharmacist for medicine was paying the government,
    and the pharmacist earned nothing from the one service it exists to sell."
    """
    await _a_pharmacy(world)
    await spawn_agent(world, "patient", PREMISES)
    _make_ill(world, "patient")
    before = world.ledger.sim_balance("b_pharmacy")

    await registry.execute(world, "patient", "buy_treatment", {})

    assert world.ledger.sim_balance("b_pharmacy") == before + PHARMACY_SHORT_TREATMENT_COST_SIM


@pytest.mark.asyncio
async def test_the_patient_pays_for_it(world, registry) -> None:
    await _a_pharmacy(world)
    await spawn_agent(world, "patient", PREMISES)
    _make_ill(world, "patient")
    before = world.ledger.sim_balance("patient")

    await registry.execute(world, "patient", "buy_treatment", {})

    assert world.ledger.sim_balance("patient") <= before - PHARMACY_SHORT_TREATMENT_COST_SIM


@pytest.mark.asyncio
async def test_somebody_who_is_well_is_refused_at_the_counter(world, registry) -> None:
    """Standing in the shop is not the same as needing it - and the refusal
    has to be about health, not about where the resident is standing."""
    await _a_pharmacy(world)
    await spawn_agent(world, "hale", PREMISES)

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "hale", "buy_treatment", {})


@pytest.mark.asyncio
async def test_the_rule_is_asked_for_and_never_re_derived(world, registry) -> None:
    """`pharmacy_refusal` owns "short ailments only". A handler with its own
    copy of that rule is a second source of truth that drifts - which is
    exactly what `life/health.py` asks this handler not to become.
    """
    import inspect

    from world.tools.primitives import living

    source = inspect.getsource(living.buy_treatment_handler)
    assert "pharmacy_refusal" in source
    for reimplemented in ("AilmentKind.SHORT", "PHARMACY_TREATABLE_AILMENTS"):
        assert reimplemented not in source, (
            f"the handler decides treatability itself via {reimplemented}; "
            "ask pharmacy_refusal instead"
        )

    await _a_pharmacy(world)
    await spawn_agent(world, "gravely", PREMISES)
    _make_ill(world, "gravely", AilmentKind.LONG)

    with pytest.raises(Exception) as caught:
        await registry.execute(world, "gravely", "buy_treatment", {})
    assert "short" in str(caught.value).lower()


@pytest.mark.asyncio
async def test_you_cannot_be_treated_from_the_fields(world, registry) -> None:
    """In person, like every other counter in this town."""
    await _a_pharmacy(world)
    await spawn_agent(world, "farmer", "Kasukabe Farmland")
    _make_ill(world, "farmer")

    with pytest.raises(LocationGateError) as caught:
        await registry.execute(world, "farmer", "buy_treatment", {})

    assert "b_pharmacy" in str(caught.value), (
        "the refusal must name a pharmacy to walk to - 'a pharmacy' is not "
        "somewhere `move` can take you"
    )


@pytest.mark.asyncio
async def test_a_town_with_no_pharmacy_refuses_without_crashing(world, registry) -> None:
    """Some casts have none, and that is a plain refusal rather than an error."""
    await spawn_agent(world, "patient", "Kasukabe Farmland")
    _make_ill(world, "patient")

    with pytest.raises(LocationGateError):
        await registry.execute(world, "patient", "buy_treatment", {})
