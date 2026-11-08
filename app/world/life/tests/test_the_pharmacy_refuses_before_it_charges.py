"""The counter and the sign have to agree, and nobody pays for nothing.

ECON-12. `world/tools/primitives/places.py` advertises the pharmacy as
"Buy treatment for short illnesses"; `health.treat_at_pharmacy` is the
function behind that promise and it has never had a production caller,
because no tool exists to reach it. While it sat unreachable its
signature drifted into a shape no real caller could use: it took a bare
`HealthState` and gave one back. From a `HealthState` alone the function
cannot tell whether the resident is ALIVE - so a corpse could be charged
five hundred sim-currency for a cure - and it cannot tell WHAT DAY it
is, so a resident whose short illness had already run its full course
could be sold a cure for an illness that `health.advance_recovery`
clears for free at the very next day boundary. It also handed back a
loose health field, leaving every future caller to know that the way to
apply it is to rebuild the agent around it.

All three are fixed here: the function takes the record and the day,
refuses both cases before any money moves, and returns the whole updated
`AgentLifeRecord`. The refusal wording lives in one place,
`pharmacy_refusal`, so the tool handler that finally reaches this cannot
grow a second copy of "the pharmacy only covers short illnesses" that
drifts away from `PHARMACY_TREATABLE_AILMENTS` - which is exactly how
`buy_energy`'s description came to quote an exchange rate that had
changed months earlier.

There is no wiring test in this file and that is a reported gap, not an
oversight: the only production route to this function is a registered
tool, every tool lives under `world/tools/`, and that package belongs to
another owner. Registering one from `world/wiring.py` instead would give
the tool to one composition root and not the other, which is the defect
ECON-08 and ECON-22 are about. The blocked half is one handler plus one
registration line in `world/tools/bootstrap.py`, so that both roots get
it at once.
"""

from __future__ import annotations

import pytest

from world.life.constants import PHARMACY_SHORT_TREATMENT_COST_SIM
from world.life.health import (
    TreatmentUnavailableError,
    pharmacy_refusal,
    treat_at_pharmacy,
)
from world.life.models import AgentLifeRecord, Ailment, AilmentKind, DeathCause, HealthState, Sex
from world.life.tests.conftest import InMemoryEconomy

# A short ailment runs one day (AILMENT_DURATION_DAYS_SHORT). Onset on
# day 3 means it is still live on day 3 and already spent on day 4.
ONSET_DAY = 3
STILL_ILL_DAY = 3
ALREADY_OVER_DAY = 4


def resident(ailment: Ailment | None = None, **overrides: object) -> AgentLifeRecord:
    base: dict[str, object] = {
        "agent_id": "a1",
        "age_years": 30.0,
        "sex": Sex.MALE,
        "health": HealthState(current_ailment=ailment),
    }
    base.update(overrides)
    return AgentLifeRecord(**base)  # type: ignore[arg-type]


def a_short_illness() -> Ailment:
    return Ailment(kind=AilmentKind.SHORT, onset_day=ONSET_DAY, duration_days=1)


@pytest.mark.asyncio
async def test_a_living_resident_is_cured_and_charged_once(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 2_000
    treated, events = await treat_at_pharmacy(
        resident(a_short_illness()), day=STILL_ILL_DAY, economy=economy
    )

    assert treated.health.current_ailment is None
    assert economy.debits == [("a1", PHARMACY_SHORT_TREATMENT_COST_SIM, "pharmacy_treatment")]
    assert events[0].type == "health.ailment.treated"
    assert events[0].payload["cost_sim"] == PHARMACY_SHORT_TREATMENT_COST_SIM
    assert events[0].payload["day"] == STILL_ILL_DAY


@pytest.mark.asyncio
async def test_the_whole_record_comes_back_not_a_loose_health_field(
    economy: InMemoryEconomy,
) -> None:
    """The caller writes one object into `world.agents`. Everything that
    is not the cure survives the round trip."""
    economy.sim_balances["a1"] = 2_000
    before = resident(a_short_illness(), household_id="h1", spouse_id="a2")

    treated, _ = await treat_at_pharmacy(before, day=STILL_ILL_DAY, economy=economy)

    assert isinstance(treated, AgentLifeRecord)
    assert treated.agent_id == before.agent_id
    assert treated.household_id == "h1"
    assert treated.spouse_id == "a2"
    assert treated.age_years == before.age_years
    assert before.health.current_ailment is not None, "the input is not mutated"


@pytest.mark.asyncio
async def test_a_dead_resident_is_not_charged_for_medicine(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 2_000
    corpse = resident(a_short_illness(), alive=False, death_cause=DeathCause.ELDERLY_HAZARD)

    with pytest.raises(ValueError):
        await treat_at_pharmacy(corpse, day=STILL_ILL_DAY, economy=economy)

    assert economy.debits == [], (
        "ECON-12: the old signature took a bare HealthState and could not "
        "tell a patient from a corpse, so it charged both"
    )


@pytest.mark.asyncio
async def test_an_illness_that_has_already_run_its_course_is_not_sold_a_cure(
    economy: InMemoryEconomy,
) -> None:
    """`advance_recovery` clears a spent short ailment for free at the
    next day boundary. Charging five hundred sim between those two
    moments sells a resident something they already have."""
    economy.sim_balances["a1"] = 2_000

    with pytest.raises(ValueError):
        await treat_at_pharmacy(
            resident(a_short_illness()), day=ALREADY_OVER_DAY, economy=economy
        )

    assert economy.debits == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [AilmentKind.MEDIUM, AilmentKind.LONG, AilmentKind.TERMINAL])
async def test_anything_worse_than_short_is_still_beyond_the_pharmacy(
    kind: AilmentKind, economy: InMemoryEconomy
) -> None:
    economy.sim_balances["a1"] = 20_000
    ailment = Ailment(kind=kind, onset_day=ONSET_DAY, duration_days=20)

    with pytest.raises(TreatmentUnavailableError):
        await treat_at_pharmacy(resident(ailment), day=STILL_ILL_DAY, economy=economy)

    assert economy.debits == []


@pytest.mark.asyncio
async def test_the_refusal_and_the_charge_can_never_disagree(economy: InMemoryEconomy) -> None:
    """The whole point of `pharmacy_refusal` existing separately: a
    caller that asks first and a caller that just tries must get the same
    answer, or the tool's refusal text becomes a second, drifting copy of
    the rule.
    """
    cases = [
        (resident(a_short_illness()), STILL_ILL_DAY),
        (resident(a_short_illness()), ALREADY_OVER_DAY),
        (resident(None), STILL_ILL_DAY),
        (resident(a_short_illness(), alive=False), STILL_ILL_DAY),
        (
            resident(Ailment(kind=AilmentKind.LONG, onset_day=ONSET_DAY, duration_days=20)),
            STILL_ILL_DAY,
        ),
    ]
    for patient, day in cases:
        economy.sim_balances[patient.agent_id] = 20_000
        refusal = pharmacy_refusal(patient, day)
        charged_before = len(economy.debits)
        try:
            await treat_at_pharmacy(patient, day=day, economy=economy)
        except (ValueError, TreatmentUnavailableError) as exc:
            assert refusal is not None, f"charged and refused disagree for {patient!r}"
            assert str(exc) == refusal
            assert len(economy.debits) == charged_before
        else:
            assert refusal is None, f"cured somebody pharmacy_refusal turned away: {refusal}"
            assert len(economy.debits) == charged_before + 1


def test_every_refusal_names_the_resident_or_the_illness() -> None:
    """A refusal a resident cannot act on is a refusal it will retry
    every tick. Each one says which of the two facts is in the way."""
    assert "a1" in pharmacy_refusal(resident(None), STILL_ILL_DAY)
    assert "a1" in pharmacy_refusal(resident(a_short_illness(), alive=False), STILL_ILL_DAY)
    spent = pharmacy_refusal(resident(a_short_illness()), ALREADY_OVER_DAY)
    assert str(PHARMACY_SHORT_TREATMENT_COST_SIM) in spent
    long_illness = pharmacy_refusal(
        resident(Ailment(kind=AilmentKind.LONG, onset_day=ONSET_DAY, duration_days=20)),
        STILL_ILL_DAY,
    )
    assert AilmentKind.LONG.value in long_illness
