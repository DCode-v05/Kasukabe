"""How much of the town's vocabulary L1 actually covers.

`EVAL-08`. The L1 harness covered 14 of 77 registered tools, and the
spec's own section 1.6 backfill list named seven more that had no
scenario at all: `start_conversation`, `find_people`, `toolify`,
`define_tool`, `teach_skill`, `vote`, `pray`. Those seven are written
now.

The rest are not, and that is the point of this file. Under the
evaluation spec's rule 8, a tool's absence from a run's trace is "not
evidence of anything" unless something checked whether a resident would
reach for it - so 63 verbs were in a state where neither their use nor
their disuse meant anything, and nothing said so.

This does not invent the missing scenarios. A scenario is a research
artifact: it puts a resident in the situation a verb exists for, with a
threshold fixed before anything is measured, and one written from a
situation nobody has ever been in is a guess about the town dressed as a
measurement. What this does is make the gap a NUMBER the suite prints,
so the next person to quote "41 of 76 verbs never called" knows how much
of that is a finding and how much is an untested verb.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

SCENARIOS = Path(__file__).resolve().parents[1] / "evals" / "scenarios"

BACKFILL = (
    "trade", "sell", "buy", "start_conversation", "find_people",
    "skillify", "toolify", "define_tool", "teach_skill", "vote", "pray",
)


@pytest.fixture(scope="module")
def registry_tools() -> set[str]:
    from world.tools.bootstrap import build_world

    _world, registry = asyncio.run(build_world(genesis_tick=0, seed=1))
    return set(registry.tools)


@pytest.fixture(scope="module")
def covered() -> set[str]:
    """Every tool with at least one scenario on disk, read from the files
    rather than from the directory names - a directory named for one verb
    holding a scenario for another would otherwise count as coverage."""
    tools: set[str] = set()
    for path in SCENARIOS.rglob("*.json"):
        try:
            tools.add(json.loads(path.read_text(encoding="utf-8"))["tool"])
        except (ValueError, KeyError):
            continue
    return tools


@pytest.mark.parametrize("tool", BACKFILL)
def test_every_verb_on_the_backfill_list_has_a_scenario(
    tool: str, covered: set[str]
) -> None:
    """The seven the review named as missing, plus the four already there."""
    assert tool in covered, (
        f"EVAL-08: `{tool}` is on the evaluation spec's own backfill list and "
        "has no L1 scenario, so whether a resident would reach for it has "
        "never been checked"
    )


def test_a_positive_and_a_negative_where_the_verb_is_gated(covered: set[str]) -> None:
    """A location-gated verb needs the negative case: reached for in the
    wrong place it is a refusal the RESIDENT pays for, and a description
    that hides the gate is a description problem, not a model one."""
    kinds: dict[str, set[str]] = {}
    for path in SCENARIOS.rglob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        kinds.setdefault(body["tool"], set()).add(body["kind"])

    for gated in ("pray", "teach_skill"):
        assert "negative" in kinds.get(gated, set()), (
            f"`{gated}` is gated and has no negative scenario, so nothing "
            "checks whether a resident reaches for it where it cannot work"
        )


def test_every_scenario_explains_why_it_exists() -> None:
    """`_why` is the difference between a measurement and a guess. Every
    scenario already on disk carries one and the new ones must too."""
    for path in SCENARIOS.rglob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        why = body.get("_why", "")
        assert len(why) > 30, (
            f"{path.name} gives no reason it exists - a scenario written "
            "from a situation nobody has been in is a guess about the town "
            "dressed as a measurement"
        )
        assert not why.lower().startswith(("todo", "tbd", "placeholder")), (
            f"{path.name}'s reason is a placeholder"
        )


def test_the_uncovered_verbs_are_reported_not_hidden(
    registry_tools: set[str], covered: set[str]
) -> None:
    """The finding itself. Not "there must be none" - writing 63 research
    artifacts is not a repair - but "the number is known and printed".

    Under rule 8 an uncovered verb's absence from a trace is not evidence
    of anything, and 63 of them were in that state silently.
    """
    uncovered = sorted(registry_tools - covered)
    coverage = len(covered & registry_tools) / max(len(registry_tools), 1)

    print(
        f"\nL1 covers {len(covered & registry_tools)} of {len(registry_tools)} "
        f"registered tools ({coverage:.0%}). No scenario for {len(uncovered)}:"
    )
    for name in uncovered:
        print(f"  - {name}")
    print(
        "EVAL-08: under the evaluation spec's rule 8, these verbs' absence "
        "from a run's trace is not evidence of anything."
    )

    assert coverage > 0.20, (
        f"L1 coverage has fallen to {coverage:.0%} of the registry - either "
        "scenarios were deleted or a great many verbs arrived at once"
    )


def test_coverage_has_not_gone_backwards(
    registry_tools: set[str], covered: set[str]
) -> None:
    """A floor, moved deliberately, rather than a target nobody owns. It
    was 14 verbs when EVAL-08 was written and is 21 now; a change that
    drops it fails here and has to say why."""
    assert len(covered & registry_tools) >= 50, (
        f"only {len(covered & registry_tools)} registered tools have an L1 "
        "scenario; EVAL-08 left it at 21 and the run-25 replays took it to 50"
    )


def test_no_scenario_names_a_verb_the_town_does_not_have(
    registry_tools: set[str], covered: set[str]
) -> None:
    """EVAL-01, inside the evaluation harness itself.

    A scenario whose `tool` is a name the registry does not serve scores
    zero reach forever and reads as coverage while doing it - a
    measurement keyed to a name nothing writes, which is the failure
    that printed eleven real price posts as `None` and the one this
    project keeps re-committing in new places.

    Cheap to check and impossible to notice otherwise: nothing else in
    the harness would complain, because a scenario for a verb nobody has
    looks exactly like a verb no model reaches for.
    """
    phantom = sorted(covered - registry_tools)
    assert not phantom, (
        f"these scenarios name verbs the registry does not serve: {phantom}. "
        "Each one would score 0% reach forever and be counted as covered."
    )


def test_every_registered_verb_is_covered_or_named_as_uncovered(registry_tools) -> None:
    """`test_the_uncovered_verbs_are_reported_not_hidden` prints the gap and
    asserts only that coverage is above 20%, so a NEW tool can ship with no
    scenario and nothing says a word - which is how `toolify`, `do_in_order`
    and `buy_energy` each reached a live run unmeasured.

    This is the other half: the uncovered verbs are a written-down list, so
    adding a tool either comes with scenarios or comes with a decision to
    put it on the list. Nothing here demands a scenario for every verb -
    guessing at the situation a verb exists for is the thing EVAL-08 refuses
    to do - it demands that the gap be named rather than discovered.
    """
    from world.evals.verb_reach import UNCOVERED_BY_DESIGN, load_scenarios

    registry_verbs = set(registry_tools)
    covered = {s.tool for s in load_scenarios()}
    unaccounted = registry_verbs - covered - set(UNCOVERED_BY_DESIGN)

    assert not unaccounted, (
        f"{len(unaccounted)} registered verbs have no L1 scenario and are not on "
        f"the named uncovered list: {sorted(unaccounted)}. Write scenarios, or add "
        "them to UNCOVERED_BY_DESIGN with the reason."
    )
    stale = set(UNCOVERED_BY_DESIGN) & covered
    assert not stale, (
        f"these verbs are listed as uncovered and now have scenarios: {sorted(stale)}"
    )
