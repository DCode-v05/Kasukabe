"""GOV-09: the `pension_*` levers on `GovernmentPolicy` were dead.

A sitting minister could stand in the government complex and call
`set_pension_config(private_pct=40)`. The policy snapshot changed, the
event fired, the governance viewer showed the new number - and every
pension actually paid in the town came out at 25%, because the module that
pays retirees reads `PENSION_PRIVATE_PCT`, `PENSION_GOVERNMENT_PCT` and
`PENSION_MINIMUM_AGE` as module-level constants (`world/ledger/money.py`
lines 79-81, used at `world/ledger/core.py:705-707` and
`world/life/day_jobs.py:375`). Voting the retirement age down to 65
changed nothing at all: a 66-year-old was still refused as ineligible.
DESIGN section 7 lists pensions as a government power, and it was a
government power over a number nobody consulted.

The reading end of those levers is what this file guards. `charter/` owns
the rule - what percentage, from what age - and deliberately not the
payment: it never sees a balance and never moves money (see `models.py`'s
module docstring on the import boundary). A payer supplies the retiree's
age, last salary and employment type, and gets back an integer count of
sim owed under the policy in force *now*, not under a constant compiled in
at import time.

Evidence the split matters: `pension_employment_type` is the only place in
the tree that can tell a government retiree from a private one. DESIGN
section 4 pays the first twice what it pays the second, and the day job
that pays pensions passes the literal string "private" for everybody
because nothing could answer the question.
"""

from __future__ import annotations

import pytest

from world.charter.genesis import seed_genesis
from world.charter.government import GovernmentActions
from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, GovernmentState
from world.charter.store import CharterStore

GOVERNMENT = GovernmentState(pm_agent_id="pm", minister_agent_ids=["m1", "m2", "m3", "m4"])
HERE = LOCATION_GOVERNMENT_COMPLEX


async def make_actions() -> tuple[GovernmentActions, CharterStore]:
    store = CharterStore()
    await seed_genesis(store, genesis_tick=0)
    await store.set_government_state(GOVERNMENT)
    return GovernmentActions(store), store


@pytest.mark.asyncio
async def test_the_genesis_policy_reproduces_the_designed_split() -> None:
    actions, _ = await make_actions()

    assert await actions.pension_due_sim(4_000, "private", 72) == 1_000
    assert await actions.pension_due_sim(4_000, "government", 72) == 2_000


@pytest.mark.asyncio
async def test_a_government_that_votes_the_rate_up_changes_what_is_owed() -> None:
    actions, _ = await make_actions()

    await actions.set_pension_config("pm", HERE, now_tick=0, private_pct=40)

    assert await actions.pension_due_sim(400, "private", 72) == 160, (
        "a government voted private pensions to 40% and the answer stayed at the 25% "
        "compiled into world/ledger/money.py. The policy field is a lever nobody reads "
        "unless the payer asks the policy (GOV-09)."
    )
    assert await actions.pension_due_sim(400, "government", 72) == 200, (
        "raising the private rate must not disturb the government rate"
    )


@pytest.mark.asyncio
async def test_a_government_that_votes_the_retirement_age_down_retires_people() -> None:
    actions, _ = await make_actions()

    assert await actions.pension_due_sim(400, "private", 66) == 0

    await actions.set_pension_config("pm", HERE, now_tick=0, eligibility_age=65)

    assert await actions.pension_due_sim(400, "private", 66) == 100, (
        "retirement was voted down to 65 and a 66-year-old still drew nothing: the age "
        "test was reading a constant, not the charter (GOV-09)."
    )


@pytest.mark.asyncio
async def test_a_pension_is_whole_sim_units_never_a_float() -> None:
    actions, _ = await make_actions()

    owed = await actions.pension_due_sim(333, "private", 72)

    assert owed == 83, "25% of 333 sim floors to 83, not 83.25"
    assert isinstance(owed, int) and not isinstance(owed, bool), (
        "money is an integer count of sim units - app/CONVENTIONS.md is explicit that "
        "floating-point currency is a bug"
    )


@pytest.mark.asyncio
async def test_a_retiree_who_never_earned_draws_nothing() -> None:
    actions, _ = await make_actions()

    assert await actions.pension_due_sim(0, "private", 90) == 0


@pytest.mark.asyncio
async def test_the_town_can_tell_a_government_retiree_from_a_private_one() -> None:
    actions, _ = await make_actions()
    job = await actions.hire_police("orin", 300, "pm", HERE, now_tick=0)

    assert await actions.pension_employment_type("orin") == "government"
    assert await actions.pension_employment_type("mila") == "private", (
        "a resident who never held a public post is a private-sector retiree"
    )

    await actions.cut_job(job.job_id, "pm", HERE, now_tick=144)

    assert await actions.pension_employment_type("orin") == "government", (
        "a retired officer served; abolishing the post afterwards does not un-serve them"
    )


@pytest.mark.asyncio
async def test_the_answer_comes_from_the_policy_currently_in_force() -> None:
    """Not from the policy that happened to be current when anything was
    constructed: `GovernmentActions` is built once at boot and lives for
    the whole run, across every amendment the town makes.
    """
    actions, store = await make_actions()

    await actions.set_pension_config("pm", HERE, now_tick=0, private_pct=10)
    first = await actions.pension_due_sim(1_000, "private", 80)
    await actions.set_pension_config("m1", HERE, now_tick=500, private_pct=90)
    second = await actions.pension_due_sim(1_000, "private", 80)

    assert (first, second) == (100, 900)
    assert len(store.policy_history) == 3, "each change is a new snapshot, history kept"
