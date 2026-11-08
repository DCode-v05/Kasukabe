"""A teacher who names the wrong skill is told which ones it could teach.

`teach_skill` has been reached twice in the project's whole recorded history,
in two different runs, and both calls were the same call:

    teach_skill(student_agent_id="n18", skill="Reading",
                student_consent=True, price_sim=15)
    -> Error: no skill with id 'Reading'

Both were paid calls that changed nothing, and the sentence they got back is
wrong twice over. The skill was not missing from the world; the TEACHER did
not have it - the handler refuses on `proficiency <= 0` and then reports it as
a missing entity. And it names nothing that would have worked, so the resident
had the same empty vocabulary to guess from on the next turn. It never tried
again. Nobody has taught anything for money in any run (ECON-7).

`NotFoundError` has taken an `available` list since it was written, for
exactly this - "a bare 'no business with id b_smelter' tells an agent its
guess was wrong and leaves it guessing from the same empty vocabulary". The
lesson refusal simply never passed one.
"""

from __future__ import annotations

import pytest

from world.tools.errors import ActorRequirementError, NotFoundError
from world.tools.primitives.social import TeachSkillParams, teach_skill_handler
from world.tools.tests.conftest import spawn_agent

SCHOOL = "school"


async def _two(world) -> tuple[str, str]:
    """A teacher and a student, in the same room - the lesson's own gate."""
    await spawn_agent(world, "teacher", SCHOOL)
    await spawn_agent(world, "student", SCHOOL)
    return "teacher", "student"


async def _teach(world, teacher, student, skill, price=0, consent=True):
    return await teach_skill_handler(
        world,
        world.agents[teacher],
        TeachSkillParams(
            student_agent_id=student, skill=skill, student_consent=consent, price_sim=price
        ),
    )


@pytest.mark.asyncio
async def test_the_refusal_names_the_skills_the_teacher_actually_has(world) -> None:
    teacher, student = await _two(world)
    world.skills.setdefault(teacher, {}).update({"carpentry": 40, "milling": 15})

    with pytest.raises(NotFoundError) as caught:
        await _teach(world, teacher, student, "Reading")

    said = str(caught.value)
    assert "carpentry" in said and "milling" in said, said
    assert "reading" in said.lower(), "the guess is still named, so the model can see what it typed"


@pytest.mark.asyncio
async def test_a_teacher_with_nothing_to_teach_is_told_that_plainly(world) -> None:
    """Silence here would read as 'the skill does not exist', which is the bug."""
    teacher, student = await _two(world)
    world.skills.pop(teacher, None)

    with pytest.raises(NotFoundError) as caught:
        await _teach(world, teacher, student, "Reading")

    said = str(caught.value).lower()
    assert "you have no skills" in said or "knows no skill" in said, said


@pytest.mark.asyncio
async def test_a_skill_the_teacher_has_is_taught_and_paid_for(world) -> None:
    """The other half: the verb works, so the two wasted calls were reachability."""
    teacher, student = await _two(world)
    world.skills.setdefault(teacher, {})["carpentry"] = 40

    outcome = await _teach(world, teacher, student, "carpentry")

    assert world.skills[student]["carpentry"] > 0, "the student learned something"
    assert outcome is not None


@pytest.mark.asyncio
async def test_consent_is_still_required(world) -> None:
    teacher, student = await _two(world)
    world.skills.setdefault(teacher, {})["carpentry"] = 40

    with pytest.raises(ActorRequirementError):
        await _teach(world, teacher, student, "carpentry", consent=False)
