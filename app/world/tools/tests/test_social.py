from __future__ import annotations

import pytest

from world.life.constants import TEMPLE_WEDDING_FEE_SIM
from world.life.models import MaritalStatus
from world.tools.errors import ActorRequirementError, LocationGateError
from world.tools.locations import LOCATION_TEMPLE
from world.tools.tests.conftest import action_cost, spawn_agent


@pytest.mark.asyncio
async def test_marry_merges_households_and_charges_temple_fee(world, registry) -> None:
    await spawn_agent(world, "alice", LOCATION_TEMPLE, age_years=25.0)
    await spawn_agent(world, "bob", LOCATION_TEMPLE, age_years=27.0)
    balance_before = world.ledger.sim_balance("alice")

    result = await registry.execute(
        world, "alice", "marry", {"spouse_agent_id": "bob", "new_household_id": "hh-new"}
    )

    assert world.agents["alice"].marital_status is MaritalStatus.MARRIED
    assert world.agents["alice"].spouse_id == "bob"
    assert world.agents["bob"].spouse_id == "alice"
    assert set(world.households["hh-new"].member_ids) == {"alice", "bob"}
    assert world.ledger.sim_balance("alice") == balance_before - TEMPLE_WEDDING_FEE_SIM - action_cost(
        "marry", {}
    )
    assert any(e.type == "agent.marriage.recorded" for e in result.events)


@pytest.mark.asyncio
async def test_marry_requires_spouse_also_at_temple(world, registry) -> None:
    await spawn_agent(world, "alice", LOCATION_TEMPLE, age_years=25.0)
    await spawn_agent(world, "bob", "park", age_years=27.0)
    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "alice", "marry", {"spouse_agent_id": "bob", "new_household_id": "hh-new"}
        )


@pytest.mark.asyncio
async def test_marry_rejects_already_married(world, registry) -> None:
    await spawn_agent(world, "alice", LOCATION_TEMPLE, age_years=25.0)
    await spawn_agent(world, "bob", LOCATION_TEMPLE, age_years=27.0)
    await registry.execute(world, "alice", "marry", {"spouse_agent_id": "bob", "new_household_id": "hh-1"})

    await spawn_agent(world, "carl", LOCATION_TEMPLE, age_years=40.0)
    from world.life.household import MarriageIneligibleError

    with pytest.raises(MarriageIneligibleError):
        await registry.execute(world, "carl", "marry", {"spouse_agent_id": "alice", "new_household_id": "hh-2"})


@pytest.mark.asyncio
async def test_marry_underage_blocked_by_actor_requirement(world, registry) -> None:
    await spawn_agent(world, "kid", LOCATION_TEMPLE, age_years=12.0)
    await spawn_agent(world, "adult", LOCATION_TEMPLE, age_years=30.0)
    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "kid", "marry", {"spouse_agent_id": "adult", "new_household_id": "hh"})


@pytest.mark.asyncio
async def test_pray_with_donation_moves_sim_to_treasury(world, registry) -> None:
    from world.ledger.money import TREASURY_ACCOUNT_ID

    await spawn_agent(world, "worshipper", LOCATION_TEMPLE)
    treasury_before = world.ledger.sim_balance(TREASURY_ACCOUNT_ID)

    result = await registry.execute(
        world, "worshipper", "pray", {"message": "thank you", "donation_sim": 250}
    )

    assert result.result["request_id"]
    assert world.ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_before + 250 + action_cost(
        "pray", {"donation_sim": 250}
    )
    assert len(world.prayer_requests) == 1


@pytest.mark.asyncio
async def test_pray_without_temple_is_gated(world, registry) -> None:
    await spawn_agent(world, "worshipper", "park")
    with pytest.raises(LocationGateError):
        await registry.execute(world, "worshipper", "pray", {})


@pytest.mark.asyncio
async def test_teach_skill_transfers_reduced_proficiency(world, registry) -> None:
    await spawn_agent(world, "teacher", "workshop")
    await spawn_agent(world, "student", "workshop")
    world.skills["teacher"] = {"smithing": 100}

    result = await registry.execute(
        world,
        "teacher",
        "teach_skill",
        {"student_agent_id": "student", "skill": "smithing", "student_consent": True, "price_sim": 0},
    )

    assert result.result == {"skill": "smithing", "proficiency": 70}
    assert world.skills["student"]["smithing"] == 70


@pytest.mark.asyncio
async def test_teach_skill_requires_consent(world, registry) -> None:
    await spawn_agent(world, "teacher", "workshop")
    await spawn_agent(world, "student", "workshop")
    world.skills["teacher"] = {"smithing": 100}

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "teacher",
            "teach_skill",
            {"student_agent_id": "student", "skill": "smithing", "student_consent": False},
        )


@pytest.mark.asyncio
async def test_teach_skill_requires_co_location(world, registry) -> None:
    await spawn_agent(world, "teacher", "workshop")
    await spawn_agent(world, "student", "elsewhere")
    world.skills["teacher"] = {"smithing": 100}

    with pytest.raises(LocationGateError):
        await registry.execute(
            world,
            "teacher",
            "teach_skill",
            {"student_agent_id": "student", "skill": "smithing", "student_consent": True},
        )


@pytest.mark.asyncio
async def test_teach_skill_charges_tuition(world, registry) -> None:
    await spawn_agent(world, "teacher", "workshop")
    await spawn_agent(world, "student", "workshop")
    world.skills["teacher"] = {"smithing": 100}
    teacher_before = world.ledger.sim_balance("teacher")
    student_before = world.ledger.sim_balance("student")

    await registry.execute(
        world,
        "teacher",
        "teach_skill",
        {"student_agent_id": "student", "skill": "smithing", "student_consent": True, "price_sim": 300},
    )

    assert world.ledger.sim_balance("teacher") == teacher_before + 300 - action_cost(
        "teach_skill", {"price_sim": 300}
    )
    assert world.ledger.sim_balance("student") == student_before - 300
