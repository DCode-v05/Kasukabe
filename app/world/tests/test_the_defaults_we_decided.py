"""The operator's defaults of 2026-09-04, turned into code.

Four questions from the 19 documents' "Open questions" sections that had an
answer but no implementation. Each was checked against the tree first and
had zero references; each is now wired and each of these tests was watched
to fail before its fix was kept.

  * `12`.4 / `06`.5  upkeep bills at midnight, not at dawn
  * `12`.6 / `06`.6  the household's richest member pays its head tax
  * `11`.7           hiring asks the employee first
  * `11`.8           `steal` can reach a shop's shelf
"""
from __future__ import annotations

import pytest

from world.core.clock import TICKS_PER_DAY, timestamp_for
from world.run_genesis import DAWN_TICK, GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path, **kw) -> GenesisRunner:
    cfg = dict(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    )
    cfg.update(kw)
    return GenesisRunner(GenesisRunnerConfig(**cfg))


# ── 12.4 / 06.5 — midnight, not dawn ────────────────────────────────────

def test_the_day_boundary_is_midnight_not_dawn():
    """`game_day` must turn over at day_tick 0, so the day's billing lands
    at midnight rather than at whatever hour a run happened to boot."""
    assert timestamp_for(0).day_tick == 0
    assert timestamp_for(TICKS_PER_DAY).day_tick == 0
    assert timestamp_for(TICKS_PER_DAY).game_day == 1
    assert timestamp_for(DAWN_TICK).day_tick == DAWN_TICK
    assert timestamp_for(DAWN_TICK).game_day == 0, (
        "dawn is inside day 0; if it were its own game_day the billing job "
        "would fire at dawn, which is the accident CORE-04 removed"
    )


@pytest.mark.asyncio
async def test_a_dawn_boot_does_not_bill_at_dawn(tmp_path):
    runner = _runner(tmp_path, start_at_dawn=True)
    await runner.boot()
    assert runner.counters.last_life_day_processed == 0
    # The next billing is tick 144 - midnight - not tick 36.
    assert timestamp_for(TICKS_PER_DAY).game_day != runner.counters.last_life_day_processed
    assert timestamp_for(DAWN_TICK + 1).game_day == runner.counters.last_life_day_processed


# ── 12.6 / 06.6 — the richest member pays ───────────────────────────────

@pytest.mark.asyncio
async def test_the_richest_member_of_a_household_pays_its_tax(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    ids = list(runner.world_state.agents)[:3]
    poor, middle, rich = ids

    ledger = runner.world_state.ledger
    # Top the richest up rather than trying to drain the others: this test
    # is about WHO is picked, not about exact balances.
    await ledger.mint_sim(rich, 50_000, tick=0, reason="blessing")

    assert runner.who_pays_for([poor, middle, rich]) == rich, (
        "the household's tax fell on whoever the world happened to seed "
        "first, which in practice was often a dependent with no income "
        "while an earning adult beside them held sim"
    )
    # Order must not matter - that was the whole bug.
    assert runner.who_pays_for([rich, poor, middle]) == rich


@pytest.mark.asyncio
async def test_the_payer_is_deterministic_on_a_tie(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    a, b = list(runner.world_state.agents)[:2]
    # Equal balances: the id breaks the tie, so two runs of one seed bill
    # the same person.
    first = runner.who_pays_for([a, b])
    assert runner.who_pays_for([b, a]) == first


# ── 11.7 — hiring asks ──────────────────────────────────────────────────

def test_hiring_requires_the_employee_to_have_agreed():
    from world.tools.primitives.civic import HireEmployeeParams

    assert "employee_consent" in HireEmployeeParams.model_fields, (
        "an owner can still name anyone in the town as their employee, and "
        "the first that person learns of it is a wage they did not agree to"
    )
    # Silence is not consent - and it is now not even expressible.
    #
    # This used to read `default=False`, so omitting the flag meant "not
    # consented" and the handler refused. Correct in spirit, and it made
    # the tool uncallable: pydantic does not list a defaulted field in
    # `required`, an index row carries only the required names
    # (TRADE-02), so the one parameter deciding success never reached the
    # resident's schema at all. Run 25's baker was refused six times for a
    # parameter she had never been shown, after her employee had said
    # "I agree" out loud.
    #
    # Required is the stronger form of the same rule: the caller must say
    # true or false, and cannot leave it unsaid. `teach_skill` has always
    # done it this way.
    import pytest as _pytest

    with _pytest.raises(Exception):
        HireEmployeeParams(business_id="b", employee_agent_id="n02")

    assert (
        HireEmployeeParams(
            business_id="b", employee_agent_id="n02", employee_consent=False
        ).employee_consent
        is False
    )


# ── 11.8 — steal reaches a shelf ────────────────────────────────────────

def test_a_shop_shelf_is_a_thing_that_can_be_stolen_from():
    from world.tools.primitives.crime import _stealable

    class _World:
        storages = {}
        businesses = {
            "b_bakery": type(
                "B", (), {"business_id": "b_bakery", "owner_agent_id": "n06",
                          "location_id": "Kasukabe Bakery"},
            )()
        }

    target = _stealable(_World(), "b_bakery")
    assert target is not None, (
        "a resident can `buy` from a shelf but not take from it, which makes "
        "shoplifting the one crime the world forbids by omission"
    )
    assert target.owner_id == "n06", "the shopkeeper must be the victim (GOV-02)"
    assert target.location_id == "Kasukabe Bakery"


def test_an_unknown_target_is_still_not_found():
    from world.tools.primitives.crime import _stealable

    class _World:
        storages = {}
        businesses = {}

    assert _stealable(_World(), "nothing") is None


def test_the_tax_job_actually_calls_the_payer_rule():
    """The call site, not just the method.

    The tests above prove `who_pays_for` picks the right person. They pass
    just as happily when nothing calls it - which is exactly how INFRA-02
    slipped through earlier in this work. This reads the day-tax job's own
    source for the call.
    """
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(GenesisRunner.charge_the_day_tax))
    tree = ast.parse(src)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "who_pays_for" in called, (
        "the day-tax job does not ask who should pay, so the bill still "
        "falls on whoever the world seeded first (12.6)"
    )


def test_the_steal_handler_actually_uses_the_shelf_lookup():
    """Same lesson, third time: a helper nothing calls is not a fix."""
    import ast
    import inspect
    import textwrap

    from world.tools.primitives.crime import steal_handler

    tree = ast.parse(textwrap.dedent(inspect.getsource(steal_handler)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_stealable" in called, (
        "steal_handler still reads world.storages directly, so a shop's "
        "shelf remains unreachable however good the helper is (11.8)"
    )
