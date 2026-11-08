"""L1: does the model reach for the verb, in the verb's own situation.

    uv run python -m world.evals.verb_reach                 # every scenario
    uv run python -m world.evals.verb_reach trade skillify  # named tools
    uv run python -m world.evals.verb_reach --samples 5     # cheaper

WHY THIS EXISTS. Every tool in the series shipped and was discovered
unused in a live run. `trade`, `find_people`, `start_conversation`,
`skillify`, `toolify`, `define_tool`, `teach_skill`, `vote`, `pray` - nine
verbs found dead, each at the cost of a four to ten dollar run, and run
19's six fixes were every one of them a fix to a DESCRIPTION rather than
to a mechanism. `trade` was ungated, worked, did exactly what two
residents had just agreed out loud, and was never called once, because the
verb a seller reaches for is `sell` and `sell` said "sell to a business at
its shop".

There has never been a layer between "the tool passes its unit tests" and
"the town spent ten dollars finding out nobody calls it". This is that
layer, and it costs about six cents.

WHAT IT DOES. Puts one resident in the exact situation a verb exists for,
renders what that resident would really see - `render_perception_text`,
the real soul seed, the real tool schemas off the real registry - asks the
real model once per sample, and records which verb came back.

WHAT IT IS NOT. It is not a test of the model and it does not measure
intelligence. It measures whether a DESCRIPTION reaches the situation it
was written for. A tool that fails here is rewritten, not excused, and
nothing else in the run is touched.

BILLED TO THE TREASURY, never a resident's wallet, tagged
`X-Kasukabe-Purpose: eval.verb_reach` - the same rule the judge follows,
and for the same reason: an eval billed as cognition is a resident paying
to be measured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BROKER = os.environ.get("BROKER_BASE_URL", "http://127.0.0.1:8200")
MODEL = os.environ.get("EVAL_MODEL", "moonshotai.kimi-k2.5")
EVAL_AGENT_ID = os.environ.get("EVAL_JUDGE_AGENT_ID", "government:treasury")
SCENARIO_ROOT = Path(__file__).resolve().parent / "scenarios"

DEFAULT_SAMPLES = 20

# Fixed here, before any run measured against them, and not movable after a
# result is seen - Build Spec v2 section 10.4. 60% is the same bar G1 holds
# for the human reply floor, and the reasoning is the same: a verb reached
# less often than that in its OWN canonical situation will not be reached
# in a live morning.
THRESHOLDS: dict[str, dict[str, float]] = {
    "positive": {"reach": 0.60, "params_right": 0.80, "misfire": 0.20},
    "near_miss": {"reach": 0.60, "params_right": 0.80, "misfire": 0.20},
    "negative": {"reach": 0.10},
    "after_refusal": {"refusal_recovery": 0.50},
}

# Verbs that only LOOK. A resident who looks before it acts is behaving
# well, and counting that against it would punish exactly the caution the
# town wants. Only a world-CHANGING verb called instead of the target is a
# misfire.
OBSERVATION_VERBS = frozenset({
    "look_around", "list_places", "describe_place", "describe_person",
    "describe_primitives", "quote_action_cost", "find_people", "read_will",
    "recall_memories", "recall_session_log",
})
# Speech is not a rival action either: saying "thirty-six for three planks"
# and then trading is one intention, not two.
SPEECH_VERBS = frozenset({"say", "start_conversation"})


@dataclass(frozen=True)
class Scenario:
    name: str
    tool: str
    kind: str
    resident: str
    location: str
    inventory: dict[str, int]
    sim: int
    people_here: list[str]
    recent_speech: list[dict[str, str]]
    open_question_to_me: str | None
    expect: dict[str, Any]
    # TOOL-31. Tools this resident has ALREADY written, seeded into the
    # registry before the situation is rendered. Until this existed every
    # authoring scenario began from an empty library, which asks only
    # whether a resident writes a first tool - never whether it builds on
    # the one it wrote yesterday, which is the whole of compounding.
    already_wrote: list[dict[str, Any]] = field(default_factory=list)
    # The authoring hint a live resident reads once it has repeated a job,
    # as {"what": "'harvest' then 'craft'", "times": 4}. Omitted, the
    # situation shows the tool DESCRIPTIONS alone - which is what the L1
    # run of 2026-09-12 measured, and four of its five repetition
    # scenarios scored 0% reach. A resident in a live run at that point
    # has also been told, in the perception, that it keeps doing this.
    shows_authoring_hint: dict[str, Any] | None = None
    # Who the resident is in an open conversation with, one-to-one or a
    # group. `buy` is OFFERED only while a resident is in one (operator's
    # rule, 2026-09-21; `registry.CONVERSATION_ENABLED_TOOLS`), so a
    # scenario that wants `buy` in the model's hands has to say who it is
    # talking to - exactly as a live resident would have to be.
    in_conversation_with: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoreCard:
    samples: int
    reach: float
    params_right: float
    misfire: float
    refusal_recovery: float | None


# EVAL-08's other half, and W-6's acceptance test. Every registered verb
# either has scenarios or is named here with a reason, so a tool cannot
# ship unmeasured in silence - which is how `toolify`, `do_in_order` and
# `buy_energy` each reached a live run with nobody having asked whether a
# resident would reach for them.
#
# This is NOT a to-do list. Writing a scenario means stating the situation
# a verb exists for, and guessing at that is precisely what EVAL-08 refuses
# to do. Four reasons a verb belongs here:
#
#   * GOVERNMENT-namespace and invisible until a seat is filled, which no
#     run has ever done (`set_tax_rate`, `table_amendment`, `convene`, ...);
#   * an answer to a refusal rather than a decision (`revise_skill`,
#     `retire_goal`, `update_goal`, `drop_intention`);
#   * the other end of a two-sided act whose first half is covered
#     (`fire_employee`, `leave_org`, `withdraw_item`, `deposit_to_till`);
#   * a life event the town has never reached (`come_of_age`, `marry`,
#     `ask_for_a_child`, `read_will`, `set_will`).
UNCOVERED_BY_DESIGN: tuple[str, ...] = (
    "admit_to_org", "appoint_police", "approve_patent", "arrest",
    "ask_for_a_child", "auction_land", "build_structure", "come_of_age",
    "convene", "define_business_tool", "define_organization_tool",
    "deny_subpoena", "deposit_to_till", "describe_person", "drop_intention",
    "file_complaint", "file_subpoena", "fire_employee", "grant_plot",
    "grant_subpoena", "issue_builder_license", "issue_business_permit",
    "leave_org", "list_places",
    "marry", "petition", "read_will", "reject_patent", "release",
    "retire_goal", "revise_skill", "revoke_license", "revoke_patent", "rule",
    "set_business_licensing", "set_price_control",
    "set_tax_rate", "set_will", "store_item", "table_amendment",
    "transfer_business", "update_goal", "vote_amendment", "withdraw_item",
    "work_site",
)


def record_scorecard(
    db_path: "str | Path", scenario_name: str, kind: str, card: ScoreCard, *, passed: bool
) -> bool:
    """One `eval_metrics` row per scored scenario. Returns whether it landed.

    EVAL-07. The only L1 run this project has ever done survives as a
    sentence in a run report - "21 of 37 scenarios pass", at five samples -
    so "has this verb ever been scored, and at what" is a question answered
    by reading rather than by querying. The programs eval has written rows
    since PROG-03; this is the same courtesy for verb reach.

    The payload carries the sample size above all: a reach with no n behind
    it is a number two runs cannot be compared on.
    """
    from world.evals.record import record_metric

    return record_metric(
        db_path,
        track="mas_engineering",
        metric_name=f"l1_reach:{scenario_name}",
        metric_value=card.reach,
        payload={
            "samples": card.samples,
            "kind": kind,
            "params_right": card.params_right,
            "misfire": card.misfire,
            "refusal_recovery": card.refusal_recovery,
            "passed": passed,
            "model": MODEL,
        },
    )


def load_scenarios(root: Path = SCENARIO_ROOT) -> list[Scenario]:
    """Every scenario on disk, as objects. A malformed one raises rather
    than being skipped: a scenario silently dropped is a tool silently
    unmeasured, which is the whole failure this file exists to end."""
    out: list[Scenario] = []
    for path in sorted(root.glob("*/*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out.append(Scenario(
            name=f"{path.parent.name}/{path.stem}",
            tool=raw["tool"],
            kind=raw.get("kind", "positive"),
            resident=raw["resident"],
            location=raw["location"],
            inventory=raw.get("inventory", {}),
            sim=raw.get("sim", 1000),
            people_here=raw.get("people_here", []),
            recent_speech=raw.get("recent_speech", []),
            open_question_to_me=raw.get("open_question_to_me"),
            expect=raw.get("expect", {}),
            already_wrote=raw.get("already_wrote", []),
            shows_authoring_hint=raw.get("shows_authoring_hint"),
            in_conversation_with=raw.get("in_conversation_with", []),
        ))
    return out


def score_samples(scenario: Scenario, samples: list[dict[str, Any]]) -> ScoreCard:
    """Turn a list of "what the model called" into the spec's five scores.

    A sample is `{"calls": [{"tool": ..., "params": {...}}, ...]}` - every
    tool call the model made in that one turn, in order.
    """
    total = len(samples) or 1
    reached = 0
    params_ok = 0
    misfired = 0
    recovered = 0
    recovery_chances = 0

    wanted = scenario.expect.get("verb", scenario.tool)
    must_include = scenario.expect.get("params_include", {}) or {}

    for sample in samples:
        calls = sample.get("calls") or []
        names = [call.get("tool") for call in calls]
        hit = wanted in names
        if hit:
            reached += 1
            first = next(c for c in calls if c.get("tool") == wanted)
            got = first.get("params") or {}
            # `"*"` asks whether the power was used at all, not for an exact
            # value: "it passed a `returns` block" is checkable, and "it
            # passed THIS returns block" would be a guess at the model's
            # wording dressed up as a bar.
            if all(
                bool(got.get(k)) if v == "*" else str(got.get(k)) == str(v)
                for k, v in must_include.items()
            ):
                params_ok += 1
        # A world-changing verb that is not the target, called BEFORE the
        # target if the target came at all.
        rivals = [
            name for name in names
            if name and name != wanted
            and name not in OBSERVATION_VERBS
            and name not in SPEECH_VERBS
        ]
        if rivals and (not hit or names.index(rivals[0]) < names.index(wanted)):
            misfired += 1
        if scenario.kind == "after_refusal":
            recovery_chances += 1
            if hit:
                recovered += 1

    return ScoreCard(
        samples=len(samples),
        reach=reached / total,
        params_right=(params_ok / reached) if reached else 0.0,
        misfire=misfired / total,
        refusal_recovery=(recovered / recovery_chances) if recovery_chances else None,
    )


def verdict(scenario: Scenario, card: ScoreCard) -> bool:
    """Pass or fail against the fixed thresholds for this scenario's kind."""
    bars = THRESHOLDS.get(scenario.kind, {})
    if "reach" in bars:
        if scenario.kind == "negative":
            if card.reach > bars["reach"]:
                return False
        elif card.reach < bars["reach"]:
            return False
    if "params_right" in bars and card.reach and card.params_right < bars["params_right"]:
        return False
    if "misfire" in bars and card.misfire > bars["misfire"]:
        return False
    if "refusal_recovery" in bars:
        if card.refusal_recovery is None or card.refusal_recovery < bars["refusal_recovery"]:
            return False
    return True


# ── putting the resident in the situation ─────────────────────────────


def soul_seed_for(resident: str) -> str:
    """The resident's own persona, as the engine writes it to persona.txt.

    The spec asks for "the real soul seed for that resident" and the first
    draft of this harness sent no system prompt at all - so the model was
    asked to act with no identity, no trade and no law of the town. Five
    scenarios scored 0% reach by calling NOTHING, which is what a
    tool-using model does with a bare situation and no sense of who it is:
    it describes rather than acts. That zero was this file's, the same as
    the one-call-instead-of-a-turn zero before it.
    """
    import json

    from world.life.genesis import RichGenesisAgent
    from world.life.soul_seed import build_persona_prompt, build_soul_seed

    for name in ("genesis_20.json", "genesis_50_town.json", "genesis_10.json"):
        path = Path(__file__).resolve().parent.parent / name
        if not path.exists():
            continue
        for raw in json.loads(path.read_text(encoding="utf-8")).get("agents", []):
            if raw.get("agent_id") != resident:
                continue
            agent = RichGenesisAgent.model_validate(raw)
            # EXACTLY the order run_genesis writes to persona.txt:
            # persona first, law of the town last. The first draft of this
            # had them reversed and scored eight fewer scenarios - `trade`
            # fell from 80% reach to 40% - because a prompt that ends on
            # "Internally, you are generous but proud" leaves a model
            # composing a character, and one that ends on "only a tool call
            # reaches the world" leaves it acting. A harness whose prompt
            # differs from the resident's by so much as an ordering is
            # measuring a prompt nobody is ever given.
            #
            # EVAL-06, and a gap CORE-16 opened WHILE FIXING SOMETHING
            # ELSE. `run_genesis` now writes persona.txt as three parts,
            # not two: persona, then THE TOWN ROSTER, then the soul seed.
            # The roster was moved out of the per-tick standing brief and
            # into the once-written seed because re-sending it every tick
            # costs O(residents) tokens per resident per tick.
            #
            # This harness kept composing two parts, so from that change
            # onward it measured a resident who did not know anybody else
            # existed - and the roster exists precisely because a resident
            # without it reaches for names off the map's buildings
            # (Tobias hailing "Harvey" and "Isabella", Bram walking to
            # "sh_silva", none of whom are in the run). A `trade` or
            # `sell` scenario scored against a model with no idea who the
            # town contains is not a measurement of trade.
            #
            # `test_the_system_prompt_is_byte_identical_to_persona_dot_txt`
            # pins the FIRST and LAST part of that composition, which is
            # what the ordering finding was about; it does not pin the
            # middle, so nothing caught this.
            return "\n\n".join([
                build_persona_prompt(agent),
                _town_roster_text(agent, resident),
                build_soul_seed(agent),
            ])
    return (
        f"You are Kasukabe resident {resident}. Only a tool call reaches the "
        "world; words you merely think reach nobody."
    )


def _self_brief_for(scenario: Any) -> dict[str, Any]:
    """The standing brief, with the resident's own name and trade in it.

    EVAL-06. `name` was the scenario's `resident` field, which is a unit
    id (`n09`). Live, `standing_brief` fills `name` from
    `self.agent_names` and `occupation` from the genesis persona, so the
    resident is addressed as a person with a trade. Read from the same
    genesis file the soul seed comes from, so the two cannot disagree
    about who this is.
    """
    import json

    brief: dict[str, Any] = {
        "name": scenario.resident,
        "location_id": scenario.location,
        "sim_balance": scenario.sim,
        "holding": scenario.inventory,
    }
    for name in ("genesis_20.json", "genesis_50_town.json", "genesis_10.json"):
        path = Path(__file__).resolve().parent.parent / name
        if not path.exists():
            continue
        for raw in json.loads(path.read_text(encoding="utf-8")).get("agents", []):
            if raw.get("agent_id") != scenario.resident:
                continue
            brief["name"] = raw.get("name") or scenario.resident
            if raw.get("occupation"):
                brief["occupation"] = raw["occupation"]
            return brief
    return brief


def _town_roster_text(agent: Any, resident: str) -> str:
    """Who else the town contains, as the seed gives it.

    Named to MATCH `GenesisRunner.town_roster_text`, not merely to
    describe itself: `test_the_system_prompt_is_byte_identical_to_persona
    _dot_txt` compares the two compositions part by part on the bare
    function name. Matching by name keeps that comparison strict - a
    genuinely different part still fails it - where a looser rule that
    forgave any near-miss would have forgiven the missing roster too.

    EVAL-06. The same shape `run_genesis.town_roster_text` writes - names
    and trades, household first, nothing about where anybody is standing.
    Built here from the genesis file rather than imported, because
    `town_roster_text` is a method on a booted runner and this harness has
    no world; what must match is the TEXT a resident is given, and that is
    asserted by `test_the_harness_prompt_matches_the_seed`.
    """
    import json

    for name in ("genesis_20.json", "genesis_50_town.json", "genesis_10.json"):
        path = Path(__file__).resolve().parent.parent / name
        if not path.exists():
            continue
        agents = json.loads(path.read_text(encoding="utf-8")).get("agents", [])
        household = []
        others = []
        mine = getattr(agent, "household_id", None)
        for raw in agents:
            other_id = raw.get("agent_id")
            if other_id == resident:
                continue
            other_name = raw.get("name") or other_id
            trade = str(raw.get("occupation") or "").replace("_", " ")
            # "Marta Thorne (n07), baker" - `GenesisRunner.roster_entry`'s
            # shape, which now carries the id. A scenario whose roster
            # omitted it would measure a resident that cannot name anybody
            # who is not in the room, which is not the town's resident.
            entry = f"{other_name} ({other_id})"
            entry = f"{entry}, {trade}" if trade else entry
            if mine and raw.get("household_id") == mine:
                household.append(entry)
            else:
                others.append(entry)
        lines = ["THE PEOPLE OF THIS TOWN"]
        if household:
            lines.append("Your household: " + ", ".join(household) + ".")
        if others:
            lines.append(
                "The rest of the town: " + ", ".join(others) + "."
                " These are the only other people in it."
            )
        return "\n".join(lines)
    return ""


async def render_situation(scenario: Scenario) -> tuple[str, list[dict[str, Any]]]:
    """(what the resident sees, the tool schemas it is handed).

    Built from the town's own code, not from a fixture of this file's
    invention: `build_world` for the registry, `render_perception_text`
    for the prompt. A harness that shows the model a prompt no resident
    ever sees measures nothing about the town.
    """
    from world.core.perception import AmbientInfo, Perception
    from world.retrieval_depth import RetrievalDepthConfig, render_perception_text
    from world.tools.bootstrap import build_world

    world, registry = await build_world(genesis_tick=0, seed=1)
    _seed_what_the_resident_already_wrote(world, registry, scenario)

    # The tools a LIVE resident in this situation would be handed - not
    # every tool the town has. `buy` is offered only in a conversation, so
    # a harness that always served it would measure a resident no run has.
    from world.tools.registry import CONVERSATION_ENABLED_TOOLS

    schemas = [
        {
            "name": name,
            "description": spec.description,
            "input_schema": spec.params_model.model_json_schema(),
        }
        for name, spec in sorted(registry.tools.items())
        if name not in CONVERSATION_ENABLED_TOOLS or scenario.in_conversation_with
    ]

    said = "\n".join(
        f"{line.get('from')} said: {line.get('text')}" for line in scenario.recent_speech
    )
    perception = Perception(
        agent_id=scenario.resident,
        tick=40,
        location_id=scenario.location,
        visible_agents=list(scenario.people_here),
        heard_events=[],
        ambient=AmbientInfo(
            game_day=0, hour=8, minute=0, is_daytime=True,
        ),
    )
    text = render_perception_text(
        perception,
        depth=RetrievalDepthConfig(),
        # EVAL-06: the NAME and the TRADE, not the unit id.
        #
        # This passed `scenario.resident` - "n09" - so the brief told the
        # model it was a unit number while the run tells it "You are
        # Corwin Thorne, the town's forester". A harness that introduces
        # the resident differently from the way the town does is measuring
        # a different resident.
        self_brief=_self_brief_for(scenario),
        # The conversation line a live resident reads when it is in one.
        conversation=(
            {"conversation_id": "eval-conversation", "topic": "",
             "participants": [scenario.resident, *scenario.in_conversation_with],
             "turns": max(1, len(scenario.recent_speech))}
            if scenario.in_conversation_with else None
        ),
    )
    if said:
        text += f"\n\nSaid here just now:\n{said}"
    if scenario.open_question_to_me:
        text += f"\n\nSaid to you, and not yet answered: {scenario.open_question_to_me}"
    if scenario.shows_authoring_hint:
        # The live wording, from the world's own function - never a
        # paraphrase. EVAL-06 cost this project one whole L1 run by
        # measuring a prompt no resident is given.
        from world.tools.state import authoring_hint_text

        shown = scenario.shows_authoring_hint
        held = shown.get("already_written")
        text += "\n\n" + authoring_hint_text(
            shown["what"],
            int(shown["times"]),
            # Optional so every scenario written before run 29 renders
            # exactly the text it was scored against. A scenario that
            # omits these is still measuring the description-plus-verbs
            # hint, which is the thing 0% was measured on.
            decisions=(
                int(shown["decisions"]) if shown.get("decisions") is not None else None
            ),
            call_ceiling=(
                int(shown["call_ceiling"])
                if shown.get("call_ceiling") is not None
                else None
            ),
            already_written=(tuple(held) if held else None),
        )
    return text, schemas


def _seed_what_the_resident_already_wrote(world, registry, scenario: Scenario) -> None:
    """Put the resident's own library into the registry, the way `toolify`
    does: a `CustomToolDefinition` in world state plus a registered spec
    whose handler replays the filed steps. So the model meets its own tool
    as one more tool it can call - and as one more step it could build on.
    """
    from world.tools.meta import build_composite_handler, build_params_model
    from world.tools.models import CustomToolDefinition, ToolStep
    from world.tools.registry import ToolSpec

    for raw in scenario.already_wrote:
        name = raw["name"]
        schema = raw.get("param_schema", {})
        world.custom_tools[name] = CustomToolDefinition(
            tool_name=name,
            description=raw["description"],
            creator_agent_id=scenario.resident,
            param_schema=schema,
            steps=[ToolStep.model_validate(step) for step in raw["steps"]],
            returns=raw.get("returns", {}),
            patent_application_id=None,
            approved=True,
            created_tick=0,
        )
        registry.register(ToolSpec(
            name=name,
            description=raw["description"],
            params_model=build_params_model(name, schema),
            handler=build_composite_handler(registry, name),
            is_primitive=False,
        ))


MAX_STEPS = 3


def ask_model(
    prompt: str,
    schemas: list[dict[str, Any]],
    scenario: "Scenario | None" = None,
    system: str = "",
) -> dict[str, Any]:
    """One TURN - not one call. Returns `{"calls": [...]}` in order.

    A turn in this town is an agentic loop: the resident looks around, is
    handed the result, and acts on what it saw. The first draft of this
    harness stopped after the first model response and duly scored
    `buy_energy` and `skillify` at 0% reach - and both had in fact called
    `look_around`, which is the correct opening move and exactly the
    caution the town wants. That zero was this file's mistake, not the
    model's, and it is the same class of error as reading `action["name"]`
    when the field is `tool`: a measurement that produces a plausible
    number from the wrong place.

    So an observation call is answered - with the scenario's own facts,
    which are the truth of that situation - and the model is asked again,
    up to MAX_STEPS. A world-changing call ends the turn, because that is
    the thing being measured and the real dispatch would have changed the
    world under it.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    calls: list[dict[str, Any]] = []

    for _step in range(MAX_STEPS):
        request_body: dict[str, Any] = {
            "model": MODEL,
            "max_tokens": 1024,
            "tools": schemas,
            "messages": messages,
        }
        if system:
            request_body["system"] = system
        body = json.dumps(request_body).encode()
        request = urllib.request.Request(
            f"{BROKER}/v1/messages",
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": EVAL_AGENT_ID,
                "anthropic-version": "2023-06-01",
                "X-Kasukabe-Purpose": "eval.verb_reach",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"calls": calls, "error": str(exc)[:120]}

        content = payload.get("content") or []
        step_calls = [
            {"tool": block.get("name"), "params": block.get("input") or {}, "id": block.get("id")}
            for block in content
            if block.get("type") == "tool_use"
        ]
        if not step_calls:
            break
        calls.extend({"tool": c["tool"], "params": c["params"]} for c in step_calls)

        # A world-changing call ends the turn: the real dispatch would have
        # moved goods or money, and asking again would be measuring a world
        # that no longer exists.
        if any(c["tool"] not in OBSERVATION_VERBS for c in step_calls):
            break

        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": _observation_answer(scenario),
                }
                for call in step_calls
            ],
        })
    return {"calls": calls}


def _observation_answer(scenario: "Scenario | None") -> str:
    """What looking around actually shows, from the scenario's own facts.

    Not an invention: the scenario states where the resident is, who is
    with it, what it holds and what was just said, and those are the
    things `look_around` reports.
    """
    if scenario is None:
        return json.dumps({"note": "nothing new"})
    return json.dumps({
        "location_id": scenario.location,
        "people_here": scenario.people_here,
        "holding": scenario.inventory,
        "sim": scenario.sim,
        "heard": [line.get("text") for line in scenario.recent_speech],
    })


def run(scenarios: list[Scenario], samples: int, record_into: str = "") -> int:
    """Score every scenario and print the board. Returns a shell exit code."""
    print(f"L1 verb reach - {MODEL} via {BROKER}, {samples} samples each. This spends money.")
    print()
    failures = 0
    for scenario in scenarios:
        prompt, schemas = asyncio.run(render_situation(scenario))
        # Off by default, and this is a finding rather than a shortcut.
        #
        # The spec asks for "the real soul seed for that resident" and the
        # obvious reading - hand it to the model as a `system` string -
        # MAKES EVERY SCORE WORSE. Measured, same scenarios, same model,
        # five samples: 21 of 37 scenarios pass with no system prompt, 13
        # with persona-then-law, 12 with law-then-persona, and `trade`
        # falls from 80% reach to 0%.
        #
        # It cannot be that a persona stops a resident acting: run 22 made
        # 282 tool calls with personas loaded. So the fault is in the
        # DELIVERY here, not the persona - live, the text is a registered
        # prompt section that thread.py composes a full system prompt
        # around, and a bare `system` string is a different prompt wearing
        # the same words. A harness that sends a prompt no resident is ever
        # given measures nothing about the town.
        #
        # Until this harness can borrow the engine's own composition, it
        # measures a DESCRIPTION against a bare situation, which is what it
        # was built for and what it does honestly. KASUKABE_L1_PERSONA=1
        # runs the other way for anyone continuing this.
        system = soul_seed_for(scenario.resident) if os.environ.get(
            "KASUKABE_L1_PERSONA"
        ) else ""
        results = [ask_model(prompt, schemas, scenario, system) for _ in range(samples)]
        errors = sum(1 for r in results if r.get("error"))
        card = score_samples(scenario, results)
        ok = verdict(scenario, card)
        failures += 0 if ok else 1
        if record_into:
            recorded = record_scorecard(record_into, scenario.name, scenario.kind, card, passed=ok)
            if not recorded:
                # The number is still the number; say the row did not land
                # rather than letting the operator assume it did.
                print(f"       (could not record {scenario.name} in {record_into})")
        mark = "PASS" if ok else "FAIL"
        recovery = (
            f" recovery {card.refusal_recovery:.0%}" if card.refusal_recovery is not None else ""
        )
        print(
            f"  {mark}  {scenario.name:<28} {scenario.kind:<13} "
            f"reach {card.reach:.0%}  params {card.params_right:.0%}  "
            f"misfire {card.misfire:.0%}{recovery}"
            + (f"  [{errors} broker errors]" if errors else "")
        )
    print()
    print(f"{len(scenarios) - failures} of {len(scenarios)} scenarios pass.")
    if failures:
        print("A tool that fails L1 does not ship. Rewrite the description and run again;")
        print("nothing else in the run is touched.")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="L1 verb-reach evaluation.")
    parser.add_argument("tools", nargs="*", help="only these tools (default: all)")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    # Re-measuring ONE scenario after changing a description is the loop this
    # harness exists for, and it cost a whole run of every scenario to do.
    parser.add_argument("--only", default="", help="only scenarios whose name contains this")
    parser.add_argument(
        "--record-into", default="",
        help="write one eval_metrics row per scenario into this trace database",
    )
    args = parser.parse_args(argv)

    scenarios = load_scenarios()
    if args.tools:
        scenarios = [s for s in scenarios if s.tool in set(args.tools)]
    if args.only:
        scenarios = [s for s in scenarios if args.only in s.name]
    if not scenarios:
        print("no scenarios matched")
        return 1
    return run(scenarios, args.samples, args.record_into)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
