"""ECON-10: government jobs, police and tenders created records that paid
nobody.

`create_job` and `hire_police` stored a `salary_sim` on a `Job` row and
`award_tender` recorded a winner, and no money ever moved for any of them:
grepping `world/charter/government.py` for the money layer found nothing at
all, in a module whose whole subject is the government's spending powers.
A town could hire a police force on a treasury salary, and the officers
would work for free forever without anything in the tree noticing, because
nothing could even ask the question - there was no way to list the filled
posts, no way to say what one was owed, and no way to remember that a day
had already been settled.

The paying itself cannot live in charter/. `models.py`'s module docstring
names the import boundary: charter/ is imported BY the tool layer, so
importing back would be a cycle, which is why GOV-23's licence fee was
fixed in the tool handler rather than in `issue_license`. So the fix here
is the half that belongs here - the questions a payer has to ask before it
can move a single sim - and this file is the guard on those questions:
which posts are owed, how much, for how many whole game-days, and the
refusal that stops the same game-day being paid twice.

The last test is the boundary itself, checked structurally rather than by
grepping for a word: if a future edit reaches for the money layer directly
from this module instead of answering the question and letting the caller
pay, that is the cycle the architecture is built to avoid, and it fails
here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import world.charter.government as government_module
from world.charter.errors import CharterValidationError, JobNotFound
from world.charter.genesis import seed_genesis
from world.charter.government import EVENT_PAYROLL_PAID, GovernmentActions
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    TICKS_PER_GAME_DAY,
    GovernmentState,
    JobStatus,
)
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder

GOVERNMENT = GovernmentState(pm_agent_id="pm", minister_agent_ids=["m1", "m2", "m3", "m4"])
HERE = LOCATION_GOVERNMENT_COMPLEX


async def make_actions(
    recorder: EventRecorder | None = None,
) -> tuple[GovernmentActions, CharterStore]:
    store = CharterStore()
    sink = recorder.sink if recorder is not None else None
    await seed_genesis(store, genesis_tick=0, event_sink=sink)
    await store.set_government_state(GOVERNMENT)
    return GovernmentActions(store, event_sink=sink), store


@pytest.mark.asyncio
async def test_an_officer_hired_today_is_owed_a_days_pay_tomorrow() -> None:
    actions, _ = await make_actions()
    await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)

    lines = await actions.payroll_due(TICKS_PER_GAME_DAY)

    assert len(lines) == 1, (
        f"a police officer on a 300 sim salary produced {len(lines)} payroll lines a "
        "game-day after being hired. The salary was a number on a row nobody read (ECON-10)."
    )
    line = lines[0]
    assert line.agent_id == "orin"
    assert line.job_type == "police"
    assert line.periods == 1
    assert line.amount_sim == 300
    assert await actions.payroll_due_sim(TICKS_PER_GAME_DAY) == 300


@pytest.mark.asyncio
async def test_nothing_is_owed_before_a_whole_game_day_has_passed() -> None:
    actions, _ = await make_actions()
    await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)

    assert await actions.payroll_due(TICKS_PER_GAME_DAY - 1) == []
    assert await actions.payroll_due_sim(TICKS_PER_GAME_DAY - 1) == 0


@pytest.mark.asyncio
async def test_an_unfilled_post_owes_nobody() -> None:
    actions, _ = await make_actions()
    job = await actions.create_job("clerk", "administration", 250, "pm", HERE, now_tick=0)

    assert job.status is JobStatus.OPEN
    assert job.assigned_agent_id is None
    assert await actions.payroll_due(TICKS_PER_GAME_DAY * 10) == []


@pytest.mark.asyncio
async def test_a_settled_day_cannot_be_paid_twice() -> None:
    actions, _ = await make_actions()
    job = await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)
    day = TICKS_PER_GAME_DAY

    (line,) = await actions.payroll_due(day)
    await actions.record_payroll_paid(job.job_id, line.periods, line.amount_sim, now_tick=day)

    assert await actions.payroll_due(day) == [], (
        "the same game-day came up owing a second time. A payer that runs twice, or "
        "retries after a partial failure, would pay the salary twice (ECON-10)."
    )
    with pytest.raises(CharterValidationError):
        await actions.record_payroll_paid(job.job_id, 1, 300, now_tick=day)


@pytest.mark.asyncio
async def test_a_payer_that_misses_a_day_still_owes_it() -> None:
    actions, _ = await make_actions()
    job = await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)
    three_days = TICKS_PER_GAME_DAY * 3

    (line,) = await actions.payroll_due(three_days)

    assert line.periods == 3
    assert line.amount_sim == 900, (
        f"three worked game-days came to {line.amount_sim} sim, not 900. A payroll job "
        "that missed two days must owe two days, not forgive them (ECON-10)."
    )
    await actions.record_payroll_paid(job.job_id, 3, 900, now_tick=three_days)
    assert await actions.payroll_due(three_days) == []


@pytest.mark.asyncio
async def test_cutting_a_post_does_not_erase_the_days_it_worked() -> None:
    actions, _ = await make_actions()
    job = await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)
    await actions.cut_job(job.job_id, "pm", HERE, now_tick=TICKS_PER_GAME_DAY * 2)

    (line,) = await actions.payroll_due(TICKS_PER_GAME_DAY * 5)

    assert line.periods == 2, (
        f"an officer cut after two days is owed {line.periods} day(s). Accrual must stop "
        "at the cut, and must not stop before it - otherwise cutting a post is a way to "
        "erase wages it had already earned."
    )
    assert line.amount_sim == 600


@pytest.mark.asyncio
async def test_the_payment_is_recorded_and_announced() -> None:
    recorder = EventRecorder()
    actions, store = await make_actions(recorder)
    job = await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)
    day = TICKS_PER_GAME_DAY

    await actions.record_payroll_paid(job.job_id, 1, 300, now_tick=day)
    await actions.record_payroll_paid(job.job_id, 1, 300, now_tick=day * 2)

    stored = await store.get_job(job.job_id)
    assert stored is not None
    assert stored.total_paid_sim == 600
    assert stored.last_paid_tick == day * 2

    paid = recorder.of_type(EVENT_PAYROLL_PAID)
    assert len(paid) == 2, (
        f"{len(paid)} payroll events reached the sink. A wage that leaves no event is a "
        "wage the trace cannot show anybody was paid."
    )
    assert paid[0]["payload"]["involved_agent_ids"] == ["orin"], (
        "the resident who was paid must be the one woken by it - GOV-20: that payload key "
        "is the interrupt list, and a town-wide wake costs one model call per resident."
    )


@pytest.mark.asyncio
async def test_settling_a_job_that_does_not_exist_is_refused() -> None:
    actions, _ = await make_actions()

    with pytest.raises(JobNotFound):
        await actions.record_payroll_paid("job-nobody-posted", 1, 300, now_tick=TICKS_PER_GAME_DAY)


@pytest.mark.asyncio
async def test_two_posts_held_by_one_resident_are_both_owed() -> None:
    actions, store = await make_actions()
    badge = await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)
    clerk = await actions.create_job("clerk", "administration", 250, "pm", HERE, now_tick=0)
    filled = await store.get_job(clerk.job_id)
    assert filled is not None
    filled.assigned_agent_id = "orin"
    filled.status = JobStatus.FILLED
    await store.put_job(filled)

    lines = await actions.payroll_due(TICKS_PER_GAME_DAY)

    assert {line.job_id for line in lines} == {badge.job_id, clerk.job_id}, (
        "a resident holding two posts is owed for both, one line per job - the payer "
        "settles by job_id, so two posts cannot collapse into one line."
    )
    assert all(line.agent_id == "orin" for line in lines)
    assert await actions.payroll_due_sim(TICKS_PER_GAME_DAY) == 550


def test_the_government_module_still_borrows_nothing_from_the_money_layer() -> None:
    """The architectural reason the fix above is a query and not a payment.

    Checked on the parsed import statements rather than on the text of the
    file, so a docstring is free to explain the boundary while the module
    is held to it.
    """
    source = Path(government_module.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    offenders = [name for name in imported if "ledger" in name or "world.tools" in name]
    assert offenders == [], (
        f"world/charter/government.py imports {offenders}. charter/ is imported BY the "
        "tool layer (models.py's module docstring), so importing back is a cycle: this "
        "module answers what is owed and the caller moves the money."
    )
