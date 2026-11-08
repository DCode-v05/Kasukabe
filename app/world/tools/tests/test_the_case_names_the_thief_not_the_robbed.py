"""GOV-02: the justice tools could only convict the person who was robbed.

`steal` filed `CrimeCase(against_agent_id=storage.owner_id)` - the victim.
`arrest` requires `case.against_agent_id == params.agent_id`. `rule` fines
`case.against_agent_id`. So on any theft case, the one person a police
officer could arrest was the person the theft was committed against, and a
fine took that person's money. The `arrest` description promises the
opposite in as many words: "`steal` opens one by itself."

Nobody hit this in 23 runs only because `steal`, `arrest` and `rule` were
never called - the seats have been vacant since genesis (GOV-01). It would
have fired on the first theft the town ever prosecuted.

The second half is the metric. DESIGN section 6 makes the false-conviction
rate the flagship justice number and ROADMAP question 20 asks for it; it
was unanswerable, and not because the town was just. A case carried one
name. Suspect and perpetrator could not differ, so a wrong conviction was
not a thing the world could represent.
"""

from __future__ import annotations

import pytest

from world.ledger.models import TransactionReason
from world.tools.errors import ToolValidationError
from world.tools.models import CrimeCaseKind, Storage
from world.tools.primitives.institutions import false_conviction_stats
from world.tools.state import WorldState

from world.charter.models import GovernmentState

from .conftest import move_agent, spawn_agent

YARD = "Kasukabe Town Square"
ELSEWHERE = "Kasukabe Farmland"
COMPLEX = "Kasukabe Government Complex"


async def _a_seated_magistrate(world: WorldState, agent_id: str = "n09"):
    """`rule` is seat-gated, and the seats have been vacant since genesis
    (GOV-01) - which is the other reason none of this ever fired live."""
    await spawn_agent(world, agent_id, COMPLEX, age_years=40.0, household_id="h1")
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id=agent_id, minister_agent_ids=[])
    )
    return agent_id


async def _a_theft(world: WorldState, *, thief="n02", victim="n01", witness=None):
    """n02 robs n01's unattended box. Returns the case."""
    await spawn_agent(world, victim, ELSEWHERE)   # the owner is away
    await spawn_agent(world, thief, YARD)
    if witness:
        await spawn_agent(world, witness, YARD)

    world.storages["box"] = Storage(
        storage_id="box", owner_id=victim, location_id=YARD
    )
    world.inventory.credit("box", "bread", 10)
    # Both rolls succeed, deterministically.
    world.rng.random = lambda: 0.0  # type: ignore[method-assign]

    from world.tools.primitives.crime import steal_handler, StealParams

    outcome = await steal_handler(
        world, world.agents[thief], StealParams(storage_id="box", item="bread", quantity=2)
    )
    return next(c for c in world.crime_cases if c.case_id == outcome.result["case_id"])


# ── the case ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_theft_accuses_nobody_and_names_the_victim(world):
    case = await _a_theft(world)

    assert case.kind is CrimeCaseKind.THEFT
    assert case.victim_agent_id == "n01"
    # The town knows it was robbed. It does not know by whom.
    assert case.against_agent_id == ""
    # But the world does, underneath.
    assert case.ground_truth_perpetrator_id == "n02"


@pytest.mark.asyncio
async def test_the_thiefs_name_does_not_reach_everybody_who_perceives_it(world):
    """GOV-05: police knowledge and ground truth were identical.

    The `crime.theft.attempted` payload carried `thief_agent_id` to every
    perceiver, so there was never anything to investigate.
    """
    await spawn_agent(world, "n01", ELSEWHERE)
    await spawn_agent(world, "n02", YARD)
    world.storages["box"] = Storage(storage_id="box", owner_id="n01", location_id=YARD)
    world.inventory.credit("box", "bread", 10)
    # Theft succeeds, nobody witnesses it.
    rolls = iter([0.0, 0.99])
    world.rng.random = lambda: next(rolls)  # type: ignore[method-assign]

    from world.tools.primitives.crime import steal_handler, StealParams

    outcome = await steal_handler(
        world, world.agents["n02"], StealParams(storage_id="box", item="bread", quantity=1)
    )
    attempted = next(e for e in outcome.events if e.type == "crime.theft.attempted")

    assert "thief_agent_id" not in attempted.payload
    assert attempted.payload["victim_agent_id"] == "n01"


@pytest.mark.asyncio
async def test_a_witness_does_learn_who(world):
    await spawn_agent(world, "n01", ELSEWHERE)
    await spawn_agent(world, "n02", YARD)
    await spawn_agent(world, "n03", YARD)
    world.storages["box"] = Storage(storage_id="box", owner_id="n01", location_id=YARD)
    world.inventory.credit("box", "bread", 10)
    world.rng.random = lambda: 0.0  # type: ignore[method-assign]

    from world.tools.primitives.crime import steal_handler, StealParams

    outcome = await steal_handler(
        world, world.agents["n02"], StealParams(storage_id="box", item="bread", quantity=1)
    )
    attempted = next(e for e in outcome.events if e.type == "crime.theft.attempted")

    assert attempted.payload["thief_agent_id"] == "n02"
    assert "n03" in attempted.payload["witness_agent_ids"]


# ── the tools cannot touch the victim ─────────────────────────────────


@pytest.mark.asyncio
async def test_the_victim_cannot_be_arrested_on_their_own_robbery(world, registry):
    """The exact act the old code made the only possible one."""
    case = await _a_theft(world)
    world.police.add("n05")
    await spawn_agent(world, "n05", ELSEWHERE)

    with pytest.raises(ToolValidationError) as raised:
        await registry.execute(
            world, "n05", "arrest", {"case_id": case.case_id, "agent_id": "n01"}
        )
    assert "accuses nobody" in str(raised.value)


@pytest.mark.asyncio
async def test_a_verdict_needs_a_defendant(world, registry):
    case = await _a_theft(world)
    pm = await _a_seated_magistrate(world)

    with pytest.raises(ToolValidationError) as raised:
        await registry.execute(
            world, pm, "rule",
            {"case_id": case.case_id, "verdict": "fine", "fine_sim": 50},
        )
    assert "accuses nobody" in str(raised.value)


# ── naming a suspect, rightly and wrongly ─────────────────────────────


@pytest.mark.asyncio
async def test_the_victim_can_name_a_suspect_on_the_open_case(world, registry):
    case = await _a_theft(world)
    # The victim goes to the desk and accuses somebody.
    await move_agent(world, "n01", COMPLEX)
    await registry.execute(
        world,
        "n01",
        "file_complaint",
        {
            "against_agent_id": "n02",
            "description": "my bread is gone and n02 was standing there",
            "case_id": case.case_id,
        },
    )

    assert case.against_agent_id == "n02"
    # And the case still remembers who really did it.
    assert case.ground_truth_perpetrator_id == "n02"


@pytest.mark.asyncio
async def test_a_case_that_already_accuses_somebody_is_not_overwritten(world, registry):
    case = await _a_theft(world)
    case.against_agent_id = "n07"
    await spawn_agent(world, "n07", ELSEWHERE)
    await move_agent(world, "n01", COMPLEX)

    with pytest.raises(ToolValidationError):
        await registry.execute(
            world,
            "n01",
            "file_complaint",
            {"against_agent_id": "n02", "description": "actually it was n02",
             "case_id": case.case_id},
        )


# ── the metric ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convicting_the_wrong_person_is_counted_as_wrong(world):
    """A steals from B; B accuses C; the seat fines C. That is one false
    conviction, and until now the town could not say so."""
    case = await _a_theft(world)          # ground truth: n02
    await spawn_agent(world, "n03", ELSEWHERE)
    case.against_agent_id = "n03"          # the town accuses the wrong man
    case.verdict = "fine"

    stats = false_conviction_stats(world)
    assert stats == {
        "convictions": 1, "true": 0, "false": 1, "unknowable": 0, "dismissed": 0
    }


@pytest.mark.asyncio
async def test_convicting_the_right_person_is_counted_as_right(world):
    case = await _a_theft(world)
    case.against_agent_id = "n02"
    case.verdict = "jail"

    stats = false_conviction_stats(world)
    assert stats["true"] == 1 and stats["false"] == 0


@pytest.mark.asyncio
async def test_a_complaint_the_world_did_not_witness_is_unknowable(world, registry):
    """Not quietly scored as correct. The world has no view on who is right."""
    await spawn_agent(world, "n01", COMPLEX)
    await spawn_agent(world, "n02", ELSEWHERE)

    await registry.execute(
        world,
        "n01",
        "file_complaint",
        {"against_agent_id": "n02", "description": "he insulted my mother"},
    )
    world.crime_cases[-1].verdict = "fine"

    stats = false_conviction_stats(world)
    assert stats["unknowable"] == 1
    assert stats["true"] == 0 and stats["false"] == 0


# ── GOV-15 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fine_is_booked_as_a_fine_not_as_tax(world, registry):
    """`treasury_view.tax_collected_sim` counted every penalty as revenue."""
    case = await _a_theft(world)
    case.against_agent_id = "n02"
    pm = await _a_seated_magistrate(world)
    await move_agent(world, pm, "town_hall")

    await registry.execute(
        world, pm, "rule",
        {"case_id": case.case_id, "verdict": "fine", "fine_sim": 50},
    )

    reasons = [t.reason for t in world.ledger.transactions]
    assert TransactionReason.FINE in reasons
