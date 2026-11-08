"""Every call is honestly classified, header or no header.

Build Spec v2 objective 3.7, defect D5, and ROADMAP RC-5, which has been
open since round 1 and is still open: measured across four trace stores,
**100% of calls in every run ever made are classified `cognition` and 0%
`background`**.

The cause is that `classify_purpose` reads only the `X-Kasukabe-Purpose`
header and the engine sends it nowhere, so everything falls to the
`unspecified` default, which classifies as cognition.

WHY THIS IS FIXED IN THE BROKER RATHER THAN THE ENGINE. The header would
have to be set by `herald`, which is frozen under ROADMAP Law 1, and
threading a purpose from the learning centre through the adapter would be
a change under `engine/src/` outside `sim/` - a finding to write up, not
to patch around. The broker already sees the whole request and is the
documented single source of truth for lane policy (broker/lanes.py's own
docstring: routing is "this module's decision alone", deliberately
independent of what any herald build happens to send). Inferring the
purpose from the request it is already holding needs no engine change and
no new wire field.

The header still wins when present. Inference is the fallback, never an
override, so a caller that does tag itself is believed.

WHAT IS AT STAKE. In kasukabe_l3.db, 336 of 779 successful calls are the
learning centre's post-turn summariser against 364 real agent turns: very
nearly one housekeeping call per thought, 30% of the token spend, all of
it drawn on Kimi lanes the agents needed and all of it billed to agent
wallets. That is a large part of why 132 of 250 agents went cognitively
bankrupt in round 1.
"""

from __future__ import annotations

from broker.lanes import CallClass, classify_purpose
from broker.purpose import infer_purpose

SUMMARISE_INSTRUCTION = (
    "Summarize this exchange in under 100 tokens total. Format: [user] what "
    "they said/asked (50 tokens), [engine] what it did (50 tokens). Facts and "
    "actions only, no pleasantries."
)

KNOWLEDGE_EXTRACTION_SYSTEM = (
    "You are a knowledge extraction agent for a personal AI assistant's memory. "
    "Given a set of learning signals, extract durable facts."
)

EPISODE_SYSTEM = (
    "You are an episode creation engine for a personal AI assistant's memory.\n\n"
    "Given a set of learning signals from a conversation"
)

PERCEPTION = (
    "It is game-day 0, 06:20 (day).\nYou are Ulrike Greenhow, the town's miner.\n"
    "You are at home:h0146. Your work is at Kasukabe Ore Outcrop.\n"
    "With you, and nobody else: Halcyon Quarshie (a0031)."
)


def request(system: str = "", last_user_text: str = "", tools: int = 0) -> dict:
    body: dict = {
        "model": "moonshotai.kimi-k2.5",
        "messages": [{"role": "user", "content": last_user_text}],
    }
    if system:
        body["system"] = [{"type": "text", "text": system}]
    if tools:
        body["tools"] = [{"name": f"world_tool_{i}"} for i in range(tools)]
    return body


# ── The header always wins ───────────────────────────────────────────


def test_an_explicit_header_is_believed_over_any_inference() -> None:
    """A caller that tags itself is trusted, even when the body looks
    like something else. Inference is a fallback for silence, not a
    second opinion."""
    purpose = infer_purpose(
        header="learning_centre.review",
        body=request(last_user_text=PERCEPTION, tools=49),
    )
    assert purpose == "learning_centre.review"
    assert classify_purpose(purpose) is CallClass.BACKGROUND


def test_an_explicit_cognition_header_is_not_downgraded() -> None:
    purpose = infer_purpose(header="agent.turn", body=request(system=EPISODE_SYSTEM))
    assert purpose == "agent.turn"
    assert classify_purpose(purpose) is CallClass.COGNITION


# ── Inference, when nothing tagged the call ──────────────────────────


def test_the_post_turn_summariser_is_inferred_as_background() -> None:
    """336 of 779 calls in kasukabe_l3.db. The single most expensive
    misclassification in the system."""
    purpose = infer_purpose(header="", body=request(last_user_text=SUMMARISE_INSTRUCTION))
    assert classify_purpose(purpose) is CallClass.BACKGROUND
    assert "summariz" in purpose or "summaris" in purpose


def test_knowledge_extraction_is_inferred_as_background() -> None:
    """3,632 of 6,096 successful calls in dryrun.db - 60% of the run."""
    purpose = infer_purpose(header="", body=request(system=KNOWLEDGE_EXTRACTION_SYSTEM))
    assert classify_purpose(purpose) is CallClass.BACKGROUND
    assert purpose == "auto:learning_centre.knowledge_extraction"


def test_episode_creation_is_inferred_as_background() -> None:
    purpose = infer_purpose(header="", body=request(system=EPISODE_SYSTEM))
    assert classify_purpose(purpose) is CallClass.BACKGROUND
    assert purpose == "auto:learning_centre.episode_creation"


def test_an_agent_turn_carrying_a_perception_stays_cognition() -> None:
    """The one thing that must never be misrouted. An agent's own
    thinking is the foreground workload and belongs on a Kimi lane."""
    purpose = infer_purpose(
        header="", body=request(last_user_text=PERCEPTION, tools=49)
    )
    assert classify_purpose(purpose) is CallClass.COGNITION


def test_an_unrecognised_call_defaults_to_cognition() -> None:
    """Fail safe, not fail cheap. Misrouting real cognition to the
    background lane would silently degrade the experiment; over-paying
    for one housekeeping call would not."""
    purpose = infer_purpose(header="", body=request(last_user_text="who knows"))
    assert classify_purpose(purpose) is CallClass.COGNITION


def test_inference_survives_a_malformed_body() -> None:
    """The broker must never fail a call over its own bookkeeping."""
    for body in ({}, {"messages": None}, {"messages": [{"content": None}]}, {"system": 7}):
        assert classify_purpose(infer_purpose(header="", body=body)) is CallClass.COGNITION


def test_string_system_prompts_are_read_as_well_as_block_lists() -> None:
    """herald sends a list of blocks; other callers send a bare string.
    A classifier that only understands one shape silently misses half."""
    body = {"model": "m", "system": KNOWLEDGE_EXTRACTION_SYSTEM, "messages": []}
    assert classify_purpose(infer_purpose(header="", body=body)) is CallClass.BACKGROUND


def test_a_tool_result_continuation_is_still_cognition() -> None:
    """Mid-loop turns carry no perception text - the last message is a
    tool result. Classifying those as background would push the second
    half of every agent turn onto the wrong lane."""
    body = {
        "model": "m",
        "tools": [{"name": "world_say"}],
        "messages": [
            {"role": "user", "content": PERCEPTION},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "world_move"}]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "content": "arrived at the mill"}],
            },
        ],
    }
    assert classify_purpose(infer_purpose(header="", body=body)) is CallClass.COGNITION


# ── Reconciling two inferences that were built in parallel ───────────
#
# `lanes.derive_purpose` landed on the Real branch at the same time as this
# module, answering the same question with a different and deliberately
# weaker claim: it labels a call by SHAPE (`auto:agent_turn`,
# `auto:compaction`, `auto:other`) and its own test asserts that a guess
# can never move traffic to another lane, because "rerouting real traffic
# on an inference would be a much larger claim than this function is
# entitled to make."
#
# That caution is right for a rule as broad as `"learning" in system`. It
# is not right for a verbatim match on a literal that lives in frozen
# learning-centre code. So the two are layered rather than one deleted:
# the NARROW marker match may reroute, the BROAD shape tag may not, and
# the tests below pin exactly which is which.
#
# The veto that makes the narrow rule safe is measured, not assumed. In
# data/loop5.db all 118 post-turn summariser calls carry no tools and all
# 106 agent turns carry the town's tool schemas, so "carries tools" and
# "is a resident thinking" were the same set in every call of that run.


def test_an_inferred_background_tag_says_it_was_inferred() -> None:
    """Upstream's honesty rule, kept: a reader must never mistake a guess
    for a statement. It applies to the rerouting inference too - more so,
    because that one also moved the call."""
    purpose = infer_purpose(header="", body=request(last_user_text=SUMMARISE_INSTRUCTION))
    assert purpose.startswith("auto:")
    assert classify_purpose(purpose) is CallClass.BACKGROUND


def test_a_declared_purpose_is_never_marked_auto() -> None:
    assert not infer_purpose("learning_centre.review", request()).startswith("auto:")


def test_the_towns_tools_veto_a_background_inference() -> None:
    """The one way the narrow rule could misfire: an agent SAYS the
    marker phrase, it lands in another resident's perception, and their
    turn is served on the wrong lane. Tools are the veto - a resident's
    turn always carries the town's schemas and a summariser call never
    does."""
    body = request(last_user_text=SUMMARISE_INSTRUCTION, tools=49)
    assert classify_purpose(infer_purpose(header="", body=body)) is CallClass.COGNITION


def test_a_system_marker_is_also_vetoed_by_tools() -> None:
    body = request(system=KNOWLEDGE_EXTRACTION_SYSTEM, tools=49)
    assert classify_purpose(infer_purpose(header="", body=body)) is CallClass.COGNITION


def test_an_unrecognised_call_still_gets_a_shape_tag() -> None:
    """Every call in every run logged `purpose="unspecified"`, so spend
    could be attributed to WHO burned it and never to WHAT FOR. Falling
    through to the shape tag is what fixes that, and it costs nothing
    because the shape tag cannot reroute."""
    tagged = infer_purpose(header="", body=request(last_user_text=PERCEPTION, tools=49))
    assert tagged == "auto:agent_turn"
    assert classify_purpose(tagged) is CallClass.COGNITION


def test_the_shape_tag_never_reroutes() -> None:
    """Upstream's guarantee, restated against the function that now
    calls it. Only a marker match may move a call."""
    for body in (request(), request(last_user_text=PERCEPTION), request(tools=49)):
        assert classify_purpose(infer_purpose(header="", body=body)) is CallClass.COGNITION


def test_a_malformed_body_still_yields_a_tag_and_never_reroutes() -> None:
    for body in ({}, {"messages": None}, {"system": 7}, None, 5):
        tag = infer_purpose(header="", body=body)
        assert isinstance(tag, str) and tag
        assert classify_purpose(tag) is CallClass.COGNITION
