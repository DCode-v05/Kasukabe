"""B3: a tick cannot spend an unbounded share of the wallet on thinking.

One resident's tick was measured at **fifty model calls**. Two residents
with the same brief on the same morning differed eightfold in what they
cost. Spend per agent per game-day came out at $2.00 against a roadmap
budget of $0.10, and that arithmetic is what ends the research before it
starts.

Everything else in this town is priced per act. Thinking was the one
exception, and this is the exception being removed - not by telling
residents to think less, which would make every frugality measurement a
measurement of that sentence, but by bounding what one tick may draw from
a wallet that is already running down.
"""

from __future__ import annotations

import pytest

from world.call_budget import (
    MAX_CALLS_PER_TICK,
    MIN_CALLS_PER_TICK,
    budget_for,
    local_tool_schemas,
    spent_line,
)
from world.retrieval_depth import format_volatile_line


def test_a_tick_cannot_exceed_the_call_budget():
    """The spec's first named test. A rich resident is still capped:
    wealth buys runway, not a fifty-call turn."""
    rich = budget_for(wallet_cents=100_000)

    assert rich.calls == MAX_CALLS_PER_TICK
    assert rich.capped is True


def test_a_nearly_empty_wallet_still_buys_a_turn():
    """The floor, and why it is 2 and not 0.

    A resident at zero is about to fall dormant anyway - the runner's own
    per-tick sweep sees the same zero. Taking its last turn away first
    would mean it could never spend that turn asking somebody for help,
    which is exactly the behaviour under scarcity the design is here to
    observe.
    """
    broke = budget_for(wallet_cents=0)

    assert broke.calls == MIN_CALLS_PER_TICK
    assert broke.floored is True


def test_the_budget_tightens_as_the_wallet_empties():
    """The whole mechanism in one assertion, and the reason `buy_energy`
    is a decision rather than a curiosity."""
    full = budget_for(wallet_cents=200).calls
    thin = budget_for(wallet_cents=45).calls
    empty = budget_for(wallet_cents=20).calls

    assert full > thin > empty
    assert empty == MIN_CALLS_PER_TICK


def test_the_floor_and_ceiling_are_the_specs_own_numbers():
    """B3's params row: "at least 2, at most 12".

    The floor is the spec's. The ceiling is 6 from 2026-09-18 - inside the
    spec's bound, and chosen from the first measurement of what turns are
    actually made of: 1,957 turns in the 50-resident run at a mean of 4.85
    calls and a median of 3, where a ceiling of 12 bound 4% of them. See
    `test_a_turn_is_a_handful_of_acts.py`.
    """
    assert MIN_CALLS_PER_TICK == 2
    assert MAX_CALLS_PER_TICK == 6
    assert MAX_CALLS_PER_TICK <= 12, "B3's bound is a maximum, not a target"


def test_a_typo_in_the_knob_does_not_remove_the_bound(monkeypatch):
    """Everything this module exists for is the bound. An unparseable
    value must not silently mean "unlimited"."""
    monkeypatch.setenv("KASUKABE_MAX_CALLS_PER_TICK", "lots")

    assert budget_for(wallet_cents=100_000).calls == MAX_CALLS_PER_TICK


def test_the_bound_can_be_widened_for_a_control_arm(monkeypatch):
    monkeypatch.setenv("KASUKABE_MAX_CALLS_PER_TICK", "40")

    assert budget_for(wallet_cents=100_000).calls == 40


def test_a_ceiling_below_the_floor_does_not_invert_the_bound(monkeypatch):
    monkeypatch.setenv("KASUKABE_MAX_CALLS_PER_TICK", "1")

    budget = budget_for(wallet_cents=100_000)
    assert budget.calls == MIN_CALLS_PER_TICK


def test_the_budget_line_is_rendered_and_names_no_remedy():
    """The spec's second named test, and the addendum's section 10 in as
    many words: "No verb is named. `buy_energy` must be found from the
    description, which is the L1 test for it."

    A resident told to buy energy has been handed the answer. One told it
    ran out has been handed the fact, and which of those it does is S2.
    """
    line = format_volatile_line({
        "usd_wallet_cents": 8,
        "sim_balance": 2_600,
        "ran_out_after_steps": 6,
    })

    assert "You ran out of energy for thinking last turn after 6 steps." in line
    assert "buy_energy" not in line
    for word in ("should", "consider", "try ", "you could", "government store"):
        assert word not in line.lower()


def test_the_line_is_absent_on_a_turn_that_did_not_run_out():
    """A sentence every resident reads every tick is a sentence every
    resident learns to skip."""
    line = format_volatile_line({"usd_wallet_cents": 142, "sim_balance": 478})

    assert "ran out" not in line


def test_the_sentence_is_past_tense_and_carries_the_count():
    assert spent_line(6) == (
        "You ran out of energy for thinking last turn after 6 steps."
    )


@pytest.mark.asyncio
async def test_the_runner_budgets_each_resident_from_its_own_wallet(tmp_path) -> None:
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    poor, rich = list(runner.world_state.agents)[:2]
    runner.ledger.get_account(poor).usd_cents = 0
    runner.ledger.get_account(rich).usd_cents = 100_000

    assert runner.call_budget_for(poor) == MIN_CALLS_PER_TICK
    assert runner.call_budget_for(rich) == MAX_CALLS_PER_TICK


@pytest.mark.asyncio
async def test_an_account_the_ledger_has_never_heard_of_does_not_end_a_turn(
    tmp_path,
) -> None:
    """A cosmetic figure may never be the reason a resident loses a turn -
    the same rule `wallet_line` already holds."""
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)

    assert runner.call_budget_for("nobody-at-all") == MIN_CALLS_PER_TICK


@pytest.mark.asyncio
async def test_the_sentence_arrives_on_exactly_one_turn(tmp_path) -> None:
    """Popped, not read. A message about something that happened, that
    keeps arriving, stops being read - the same reason `wake_reasons` is
    popped."""
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    agent_id = next(iter(runner.world_state.agents))
    runner.budget_hit_last_turn[agent_id] = 6

    first = runner.wallet_line(agent_id, tick=40)
    second = runner.wallet_line(agent_id, tick=41)

    assert first["ran_out_after_steps"] == 6
    assert second["ran_out_after_steps"] is None


@pytest.mark.asyncio
async def test_a_tool_gated_elsewhere_comes_back_as_one_line(tmp_path) -> None:
    """A fresh context is about 8,000 tokens before a word of perception,
    and most of that is schemas for verbs the resident cannot use where it
    is standing."""
    from world.tools.bootstrap import build_world
    from world.tools.registry import tool_registry_rows
    from world.tools.tests.conftest import spawn_agent

    world, registry = await build_world()
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    rows = {row["name"]: row for row in tool_registry_rows(registry, world, "n01")}

    reduced = rows["buy_energy"]
    assert reduced.get("elsewhere") is True
    assert reduced["input_schema"]["properties"] == {}
    assert "buy_energy" == reduced["name"]
    assert "move" in reduced["description"]


@pytest.mark.asyncio
async def test_an_ungated_tool_is_always_served_in_full(tmp_path) -> None:
    from world.tools.bootstrap import build_world
    from world.tools.registry import tool_registry_rows
    from world.tools.tests.conftest import spawn_agent

    world, registry = await build_world()
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    rows = {row["name"]: row for row in tool_registry_rows(registry, world, "n01")}

    for name in ("say", "move", "look_around", "eat"):
        assert rows[name].get("elsewhere") is not True, name
        assert rows[name]["input_schema"].get("properties") is not None


@pytest.mark.asyncio
async def test_a_resolver_gated_tool_is_never_reduced(tmp_path) -> None:
    """`craft`'s workshop depends on which recipe was named, so it cannot
    be decided in advance. Reducing it wrongly is a verb the resident cannot
    form a call for.

    Written before TRADE-02, when a reduced row declared `properties: {}`,
    so "reduced" and "cannot form the call" were the same thing and this
    asserted the first. Since TRADE-02 (`_index_row`) a row for a tool
    usable here names its required parameters, and the budget may reduce
    `craft` - the used-verbs ranking now does, at the farmland. What must
    never happen is still what the docstring says: a row that tells the
    resident `craft` takes nothing, or lives somewhere else."""
    from world.tools.bootstrap import build_world
    from world.tools.registry import tool_registry_rows
    from world.tools.tests.conftest import spawn_agent

    world, registry = await build_world()
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    rows = {row["name"]: row for row in tool_registry_rows(registry, world, "n01")}
    craft = rows["craft"]

    if craft.get("elsewhere"):
        assert craft["input_schema"].get("required") == ["recipe_id"], craft
        assert "recipe_id" in craft["input_schema"]["properties"], craft
        assert "Needs: recipe_id" in craft["description"], craft
        assert "Only at" not in craft["description"], craft


@pytest.mark.asyncio
async def test_an_anonymous_caller_still_gets_the_whole_registry(tmp_path) -> None:
    """The observatory, and a boot before anybody has a location."""
    from world.tools.bootstrap import build_world
    from world.tools.registry import tool_registry_rows

    world, registry = await build_world()

    rows = tool_registry_rows(registry)

    assert rows and not any(row.get("elsewhere") for row in rows)


def test_the_companion_setting_can_be_turned_off(monkeypatch):
    assert local_tool_schemas() is True
    monkeypatch.setenv("KASUKABE_LOCAL_TOOL_SCHEMAS", "0")
    assert local_tool_schemas() is False


@pytest.mark.asyncio
async def test_the_runner_reads_each_residents_arm_from_the_genesis_cast(
    tmp_path,
) -> None:
    """`genesis.py` has assigned every resident an arm since it was
    written and nothing honoured it, so `no_learning` residents behaved
    exactly like the treated ones and C1 was a correlation."""
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)

    arms = {runner.ablation_arm_for(a) for a in runner.world_state.agents}
    assert arms <= {"full", "no_learning", "no_reflection", "deepseek_locked"}
    assert len(arms) > 1, "every resident is in the same arm; there is no control"


@pytest.mark.asyncio
async def test_a_resident_genesis_never_heard_of_is_in_the_full_arm(
    tmp_path,
) -> None:
    """Allow-by-default: a resident whose arm nobody recorded is not a
    control for anything, and calling it one would quietly put an
    untreated resident in the treated group."""
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)

    assert runner.ablation_arm_for("nobody-at-all") == "full"
