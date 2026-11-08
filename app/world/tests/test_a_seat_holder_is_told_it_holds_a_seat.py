"""The town elected a government and the government did nothing.

GOV-3, from the 8-resident election run of 2026-09-23. GOV-1 and GOV-2
both held - an election completed, seats filled, five ballots cast, three
by non-candidates - and then: `GOV-3 | a seat acts as the state | no
government tool used`.

NOT REFUSED. `registry.visible_to` gates the GOVERNMENT namespace on
`is_sitting(actor_id)` alone, not on location, so every government verb
was already in the winners' schemas wherever they stood. The run's
refusal table ("What the town could not do") carries `location:town_hall`
and `charter:ElectionPhaseError` for `declare_candidacy` and not one
government-tool entry. They were served the tools and never reached for
them.

What they were told was `Who governs: <names>` - a list, in the third
person, naming no verb. A resident reading its own name in it is not
thereby told that the town's government verbs are now its own.

That is the finding `tools/state.py::authoring_hint` already paid for:
"an agent told 'write a procedure down', with forty-eight tools in its
schema, is not declining to author - it has not connected the sentence to
the verb. Naming the verb costs nine words and leaves every interesting
decision with the agent." It moved `skillify` from 0% to 4 of 5.

The will was not the missing part. Delphine Quintero campaigned on it -
"I'm running charter change, not charity. Changed rules, changed who gets
heard when votes are counted." She won, and went back to trading flour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.retrieval_depth import format_civic_notice
from world.run_genesis import apply_scenario
from world.tests.test_run_genesis import make_runner

SCENARIO = "world/scenarios/election_at_dawn_8.json"


async def booted_under_the_scenario(tmp_path: Path):
    """Built the way the CLI builds it.

    `make_runner` alone LOADS the scenario and does not apply it - the
    run-shaped settings are folded in by `apply_scenario`, which lives in
    the argument path. The runner warns when the two disagree ('scenario
    election-at-dawn-8 is not what this run is doing'), and a test that
    ignored that warning would be asserting against a town the scenario
    never shaped.
    """
    runner = make_runner(tmp_path)
    runner.config = apply_scenario(runner.config, SCENARIO)
    runner = make_runner(tmp_path, **{
        f: getattr(runner.config, f)
        for f in ("scenario_path", "genesis_path", "start_tick", "max_ticks")
        if getattr(runner.config, f, None) is not None
    })
    await runner.boot(resume=False)
    from world.core.clock import timestamp_for

    await runner.charter_tick(timestamp_for(runner.config.start_tick))
    return runner


def test_a_seat_holder_is_told_the_seat_and_the_verbs():
    rendered = format_civic_notice(
        {"government": "Delphine Quintero", "your_seat": "Prime Minister"}
    )

    assert "You hold a seat: Prime Minister" in rendered
    assert "set_tax_rate" in rendered, "the seat is named and no verb with it"
    assert "Kasukabe Government Complex" in rendered, (
        "the verbs are gated to the complex, so a holder told the verb and "
        "not the place spends a paid call learning where to stand"
    )


def test_an_ordinary_resident_is_told_nothing_of_the_sort():
    """The line is for whoever holds the seat. Everyone else reads the same
    notice they always did."""
    rendered = format_civic_notice({"government": "Delphine Quintero"})

    assert "Who governs: Delphine Quintero." in rendered
    assert "You hold a seat" not in rendered
    assert "set_tax_rate" not in rendered


def test_a_caretaker_is_told_it_was_appointed():
    """GROW-12. A seat filled before any ballot carries no mandate, and the
    one place that matters most is the holder's own reading of it."""
    rendered = format_civic_notice({"government": "A, B", "your_seat": "minister",
                                    "your_seat_is_caretaker": True})

    assert "by appointment rather than by election" in rendered


def test_the_line_states_what_a_seat_may_do_and_never_what_to_do():
    """The standing rule: prices and rules, never strategy. `soul_seed` and
    `cost_disclosure` both carry this check over every string they render;
    this one is new resident-facing text and gets the same."""
    from world.life.tests.test_soul_seed import STRATEGY_WORDS

    for notice in (
        {"government": "A", "your_seat": "Prime Minister"},
        {"government": "A", "your_seat": "minister", "your_seat_is_caretaker": True},
    ):
        text = format_civic_notice(notice).lower()
        leaked = [word for word in STRATEGY_WORDS if word in text]
        assert not leaked, f"the notice tells a seat-holder what to do: {leaked}"
        for plan in ("you must", "you need to", "go and", "make sure"):
            assert plan not in text, f"{plan!r} is an instruction, not a fact"


@pytest.mark.asyncio
async def test_the_seated_government_actually_reads_it(tmp_path: Path) -> None:
    """A line nothing serves is a line nobody reads. The scenario seats a
    government at genesis, so the holders exist from tick one."""
    runner = await booted_under_the_scenario(tmp_path)
    state = await runner.world_state.charter.store.get_government_state()

    pm = state.pm_agent_id
    assert pm is not None, "the scenario seated nobody, so this proves nothing"

    seated_notice = format_civic_notice(runner.civic_notice_for(pm))
    assert "You hold a seat: Prime Minister" in seated_notice
    assert "set_tax_rate" in seated_notice

    outsider = next(
        a for a in sorted(runner.world_state.agents) if not state.is_sitting(a)
    )
    assert "You hold a seat" not in (
        format_civic_notice(runner.civic_notice_for(outsider)) or ""
    )


@pytest.mark.asyncio
async def test_a_minister_is_told_too_not_only_the_pm(tmp_path: Path) -> None:
    """Three seats were filled and only one is the PM. A notice that spoke
    only to the PM would leave two thirds of the government unaware of it."""
    runner = await booted_under_the_scenario(tmp_path)
    state = await runner.world_state.charter.store.get_government_state()

    assert state.minister_agent_ids, "no ministers were seated"
    for minister in state.minister_agent_ids:
        rendered = format_civic_notice(runner.civic_notice_for(minister))
        assert "You hold a seat: minister" in rendered
        assert "Prime Minister" not in rendered.split("You hold a seat")[1], (
            f"{minister} is told it is the PM"
        )
