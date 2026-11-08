"""L1: does the model reach for the verb, in the verb's own situation.

The evaluation spec's finding, and it is the sharpest one in the
assessment: **every tool in the series shipped and was discovered unused
in a live run.** `trade`, `find_people`, `start_conversation`, `skillify`,
`toolify`, `define_tool`, `teach_skill`, `vote`, `pray` - nine verbs
discovered dead, each at the cost of a $4 to $10 run, and run 19's six
fixes were every one of them a description fix.

There has never been a layer between "the tool passes its unit tests" and
"the town spent ten dollars finding out nobody calls it". This is that
layer. It puts one resident in the exact situation a verb exists for,
renders what it would really see - the real perception text, the real soul
seed, the real tool schemas - asks the real model once, and records which
verb came back.

What it scores, all from the spec's §1.1:

    reach              the verb was called at least once
    params right       of reaches, the params validate and name the target
    misfire            a different world-changing verb was called instead
    refusal recovery   after a wrong call was refused, the next call is right
    negative reach     in a situation the verb does NOT fit, it was called

These tests cover the harness, not the model: scenario loading, scoring,
the thresholds, and that the perception handed to the model is the real
one. Whether a given tool passes is a question only the real model can
answer, and `python -m world.evals.verb_reach` asks it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.evals.verb_reach import (
    THRESHOLDS,
    Scenario,
    ScoreCard,
    load_scenarios,
    score_samples,
    verdict,
)

SCENARIOS = Path(__file__).resolve().parent.parent / "evals" / "scenarios"


def test_every_scenario_on_disk_loads_and_names_a_real_tool() -> None:
    """A scenario naming a verb the registry does not have is a test that
    can only ever fail, and it would fail as though the model were at
    fault."""
    import asyncio

    from world.tools.bootstrap import build_world

    _world, registry = asyncio.run(build_world(genesis_tick=0, seed=1))
    scenarios = load_scenarios(SCENARIOS)
    assert scenarios, "no scenarios on disk"
    for scenario in scenarios:
        assert scenario.tool in registry.tools, (
            f"{scenario.name} expects `{scenario.tool}`, which is not a tool"
        )
        assert scenario.kind in {"positive", "negative", "near_miss", "after_refusal"}


def test_a_positive_scenario_scores_reach_when_the_verb_is_called() -> None:
    scenario = Scenario(
        name="canonical", tool="trade", kind="positive", resident="n09",
        location="carpentry", inventory={"plank": 3}, sim=1708,
        people_here=["n05"], recent_speech=[], open_question_to_me=None,
        expect={"verb": "trade", "params_include": {"counterparty_id": "n05"}},
    )
    samples = [
        {"calls": [{"tool": "trade", "params": {"counterparty_id": "n05", "item": "plank"}}]},
        {"calls": [{"tool": "trade", "params": {"counterparty_id": "n05", "item": "plank"}}]},
        {"calls": [{"tool": "say", "params": {"content": "thirty-six for three"}}]},
    ]
    card = score_samples(scenario, samples)
    assert card.reach == pytest.approx(2 / 3)
    assert card.params_right == pytest.approx(1.0)
    assert card.misfire == pytest.approx(0.0), "say is speech, not a rival world-changing verb"


def test_calling_a_different_world_changing_verb_is_a_misfire() -> None:
    """The failure that matters: the resident acted, and acted wrongly.
    `sell` instead of `trade` is the exact confusion run 19 found, where
    residents concluded they needed to register a business."""
    scenario = Scenario(
        name="canonical", tool="trade", kind="positive", resident="n09",
        location="carpentry", inventory={"plank": 3}, sim=1708,
        people_here=["n05"], recent_speech=[], open_question_to_me=None,
        expect={"verb": "trade", "params_include": {}},
    )
    card = score_samples(scenario, [
        {"calls": [{"tool": "sell", "params": {"item": "plank"}}]},
        {"calls": [{"tool": "trade", "params": {"counterparty_id": "n05"}}]},
    ])
    assert card.misfire == pytest.approx(0.5)
    assert card.reach == pytest.approx(0.5)


def test_looking_around_first_is_not_a_misfire() -> None:
    """A resident that looks before it acts is behaving well. Only a
    world-CHANGING verb counts against it, or the harness would punish
    exactly the caution the town wants."""
    scenario = Scenario(
        name="c", tool="trade", kind="positive", resident="n09", location="carpentry",
        inventory={}, sim=100, people_here=["n05"], recent_speech=[],
        open_question_to_me=None, expect={"verb": "trade", "params_include": {}},
    )
    card = score_samples(scenario, [
        {"calls": [
            {"tool": "look_around", "params": {}},
            {"tool": "quote_action_cost", "params": {"tool_name": "trade"}},
            {"tool": "trade", "params": {"counterparty_id": "n05"}},
        ]},
    ])
    assert card.reach == pytest.approx(1.0)
    assert card.misfire == pytest.approx(0.0)


def test_a_negative_scenario_wants_the_verb_NOT_called() -> None:
    """A verb reached for when it does not apply is as much a description
    failure as one never reached at all - it just costs the resident
    instead of the researcher."""
    scenario = Scenario(
        name="no counterparty", tool="trade", kind="negative", resident="n09",
        location="Kasukabe Forest", inventory={}, sim=10, people_here=[],
        recent_speech=[], open_question_to_me=None, expect={"verb": "trade"},
    )
    card = score_samples(scenario, [
        {"calls": [{"tool": "fell_timber", "params": {}}]},
        {"calls": [{"tool": "trade", "params": {}}]},
    ])
    assert card.reach == pytest.approx(0.5)
    assert verdict(scenario, card) is False, (
        "half the samples reached for a verb that does not apply, which is a fail"
    )


def test_the_thresholds_are_the_ones_the_spec_fixed() -> None:
    """Written down before any run, and not movable afterwards - Build
    Spec v2 §10.4."""
    assert THRESHOLDS["positive"]["reach"] == 0.60
    assert THRESHOLDS["positive"]["params_right"] == 0.80
    assert THRESHOLDS["positive"]["misfire"] == 0.20
    assert THRESHOLDS["negative"]["reach"] == 0.10
    assert THRESHOLDS["after_refusal"]["refusal_recovery"] == 0.50


def test_a_passing_positive_scenario_is_a_pass() -> None:
    scenario = Scenario(
        name="c", tool="trade", kind="positive", resident="n09", location="carpentry",
        inventory={}, sim=100, people_here=["n05"], recent_speech=[],
        open_question_to_me=None, expect={"verb": "trade", "params_include": {}},
    )
    card = ScoreCard(samples=10, reach=0.7, params_right=0.9, misfire=0.1,
                     refusal_recovery=None)
    assert verdict(scenario, card) is True


def test_a_reach_below_the_bar_fails_even_with_perfect_params() -> None:
    scenario = Scenario(
        name="c", tool="skillify", kind="positive", resident="n09", location="mill",
        inventory={}, sim=100, people_here=[], recent_speech=[],
        open_question_to_me=None, expect={"verb": "skillify", "params_include": {}},
    )
    card = ScoreCard(samples=20, reach=0.0, params_right=1.0, misfire=0.0,
                     refusal_recovery=None)
    assert verdict(scenario, card) is False, (
        "zero reach is exactly what three live runs measured for skillify; the "
        "harness has to call that a failure"
    )


def test_the_perception_handed_to_the_model_is_the_real_one(tmp_path: Path) -> None:
    """The harness is worthless if it shows the model a prompt no resident
    ever sees. It must render through the same function the runner uses."""
    import inspect

    from world.evals import verb_reach

    source = inspect.getsource(verb_reach)
    assert "render_perception_text" in source, (
        "the harness builds its own prompt instead of the town's"
    )
    assert "build_specs" in source or "registry.tools" in source, (
        "the harness does not hand the model the real tool schemas"
    )


def test_scenarios_exist_for_the_verbs_the_series_found_dead() -> None:
    """The spec's §1.6 backfill list. A verb with no canonical scenario is
    a verb whose situation nobody has ever written down, which is a
    finding in itself."""
    scenarios = load_scenarios(SCENARIOS)
    covered = {s.tool for s in scenarios}
    assert {"trade", "skillify", "buy_energy"} <= covered, (
        "no canonical situation written for: "
        "{'trade', 'skillify', 'buy_energy'} - covered}"
    )


def test_the_system_prompt_is_byte_identical_to_persona_dot_txt() -> None:
    """The harness's whole claim is that the model sees what a resident
    sees. Composed in the other order it scored eight fewer scenarios and
    took `trade` from 80% reach to 40%, because a prompt ending on "what
    you hold to" leaves a model composing a character and one ending on
    "only a tool call reaches the world" leaves it acting.

    So the composition is pinned against the runner's own line rather than
    described in a comment.
    CORE-16 added a third part to the resident's seed - the town roster -
    and EVAL-06 found the harness had not been given it, so from that
    change onward the harness measured a resident who did not know anybody
    else in the town existed.

    That is why both sides are now read as LISTS and compared whole. This
    test used to pin the first and last part only, which is what the
    ordering finding was about - and a first-and-last check cannot see a
    middle part go missing, which is exactly how one did.
    """
    import re
    from pathlib import Path

    def parts_of(source: str, assignment: str) -> list[str]:
        block = re.search(assignment + r'\s*"[^"]*"\.join\(\[(.*?)\]\)', source, re.S)
        if block is None:
            return []
        return [
            line.strip().rstrip(",")
            for line in block.group(1).strip().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def name_of(entry: str) -> str:
        """`build_soul_seed(agent)` and `self.town_roster_text(agent_id)`
        are the same part written two ways - one is a module function, the
        other a runner method. Compared by the bare name."""
        return entry.split("(")[0].replace("self.", "").strip("_")

    runner = (Path(__file__).resolve().parent.parent / "run_genesis.py").read_text()
    resident_gets = parts_of(runner, "persona_text =")
    assert resident_gets, "run_genesis no longer composes persona.txt the way this pins"

    from world.evals import verb_reach

    harness = verb_reach.soul_seed_for.__doc__ or ""
    source = Path(verb_reach.__file__).read_text()
    harness_gets = parts_of(source, "return")
    assert harness_gets, "the harness no longer composes a system prompt"

    assert [name_of(p) for p in harness_gets] == [name_of(p) for p in resident_gets], (
        f"EVAL-06: the harness composes {harness_gets} and a resident is "
        f"given {resident_gets} - so the harness is scoring a prompt "
        "nobody in the town ever receives"
    )
    assert "soul_seed" in name_of(resident_gets[-1]), (
        f"the resident's prompt now ends on {resident_gets[-1]} instead of "
        "the soul seed - a prompt ending on anything but the law of the "
        "town leaves a model composing a character rather than acting"
    )
    assert harness


# ── TOOL-31: the harness can put a resident in front of its own library ──
#
# Every authoring scenario until now began with a resident that had never
# written anything, because the harness had no way to say otherwise. That
# is the easy half of the question. The hard half - the one the operator
# asked for - is whether a resident that ALREADY wrote a tool reaches for
# the verb again to build on it, and whether it uses the projection rather
# than reading a log. Neither was expressible.


def test_a_scenario_can_give_a_resident_tools_it_already_wrote() -> None:
    from world.evals.verb_reach import Scenario, load_scenarios

    assert "already_wrote" in Scenario.__dataclass_fields__, (
        "a scenario cannot say what the resident has already written, so the "
        "hard authoring question - does it build on its own library - is unaskable"
    )
    for scenario in load_scenarios():
        assert isinstance(scenario.already_wrote, list)


@pytest.mark.asyncio
async def test_a_tool_the_resident_wrote_is_served_back_to_it() -> None:
    """Seeded into the real registry, so the model meets it exactly as a
    resident would: as one more tool in the payload, with its own schema."""
    from world.evals.verb_reach import Scenario, render_situation

    scenario = Scenario(
        name="test/layered", tool="toolify", kind="positive", resident="n09",
        location="mill", inventory={}, sim=1000, people_here=[], recent_speech=[],
        open_question_to_me=None, expect={"verb": "toolify"},
        already_wrote=[{
            "name": "mill_a_sack",
            "description": "mill one sack of wheat into flour",
            "steps": [{"tool_name": "craft", "params": {"recipe_id": "mill_wheat_to_flour"}}],
        }],
    )

    _text, schemas = await render_situation(scenario)

    served = {row["name"] for row in schemas}
    assert "mill_a_sack" in served, "the resident's own tool was not served back to it"


def test_an_eval_can_require_that_a_call_used_a_power_at_all() -> None:
    """`params_include` compared values exactly, so "it passed a `returns`
    block" - any block - could not be asked for. `"*"` means present and
    not empty, which is the difference between reaching for the verb and
    using what the verb is for."""
    from world.evals.verb_reach import Scenario, score_samples

    scenario = Scenario(
        name="test/returns", tool="toolify", kind="positive", resident="n09",
        location="mill", inventory={}, sim=1000, people_here=[], recent_speech=[],
        open_question_to_me=None,
        expect={"verb": "toolify", "params_include": {"returns": "*"}},
        already_wrote=[],
    )

    with_projection = score_samples(scenario, [
        {"calls": [{"tool": "toolify", "params": {"returns": {"food": "@here.prices_here"}}}]},
    ])
    without = score_samples(scenario, [
        {"calls": [{"tool": "toolify", "params": {"returns": {}}}]},
    ])

    assert with_projection.params_right == 1.0
    assert without.params_right == 0.0


def test_no_authoring_scenario_can_pass_by_construction() -> None:
    """`toolify/negative_a_one_off_errand.json` expected `"verb": null`, and
    a negative is scored as "reach stayed below 10%" - so with no verb to
    reach for, reach was 0 and the scenario passed whatever the model did.
    An eval that cannot fail measures nothing."""
    from world.evals.verb_reach import load_scenarios

    for scenario in load_scenarios():
        assert scenario.expect.get("verb", scenario.tool) is not None, (
            f"{scenario.name} expects no verb, so it passes by construction"
        )


@pytest.mark.asyncio
async def test_a_scenario_can_show_the_hint_a_live_resident_would_have_read() -> None:
    """The L1 run of 2026-09-12 scored the authoring verbs against their
    descriptions alone and got 0% reach on four of five repetition
    scenarios. A resident that has repeated a job in a live run has also
    read the authoring hint, so a scenario has to be able to include it -
    in the world's own words, not a paraphrase of them."""
    from world.evals.verb_reach import Scenario, render_situation
    from world.tools.state import authoring_hint_text

    scenario = Scenario(
        name="test/hinted", tool="toolify", kind="positive", resident="n09",
        location="mill", inventory={}, sim=1000, people_here=[], recent_speech=[],
        open_question_to_me=None, expect={"verb": "toolify"}, already_wrote=[],
        shows_authoring_hint={"what": "'harvest' then 'craft'", "times": 4},
    )

    text, _schemas = await render_situation(scenario)

    assert authoring_hint_text("'harvest' then 'craft'", 4) in text


# ── EVAL-07: an L1 score that is not written down is not a measurement ──


def test_a_scorecard_can_be_recorded(tmp_path) -> None:
    """The one L1 run this project has ever done is prose in a run report:
    "21 of 37 scenarios pass", five samples, no rows anywhere. So "was this
    verb ever scored, and at what" is answerable only by reading, which is
    exactly what `eval_metrics` exists to end (EVAL-07) - and the programs
    eval has written rows since PROG-03 while verb reach wrote none."""
    import sqlite3
    from pathlib import Path

    from world.evals.verb_reach import ScoreCard, record_scorecard

    schema = Path(__file__).resolve().parents[2] / "supabase" / "sqlite_schema.sql"
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.executescript(schema.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    card = ScoreCard(samples=20, reach=0.6, params_right=1.0, misfire=0.1, refusal_recovery=None)
    assert record_scorecard(db, "toolify/canonical", "positive", card, passed=True)

    rows = sqlite3.connect(db).execute(
        "select metric_name, metric_value, metric_payload from eval_metrics"
    ).fetchall()
    assert len(rows) == 1, rows
    name, value, payload = rows[0]
    assert "toolify/canonical" in name
    assert value == pytest.approx(0.6)
    # The sample size above all: a reach with no n behind it is a number two
    # runs cannot be compared on.
    assert '"samples": 20' in payload and '"kind": "positive"' in payload
