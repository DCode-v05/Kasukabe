"""What kind of call is this, when nothing bothered to say?

DESIGN.md section 4 splits the town's traffic in two: agent cognition
prefers Kimi lanes, and background work (learning centre, dream probes,
importance scoring, summariser) runs on DeepSeek. broker/lanes.py routes on
exactly that split, reading `X-Kasukabe-Purpose`.

Nothing sends that header. Measured across four trace stores covering
fifteen runs: **every call in every run is classified cognition, and the
background share is zero**. ROADMAP.md calls this RC-5 and it has been open
since round 1.

The header cannot be fixed where it is missing. It would have to be set by
`herald`, which is frozen under ROADMAP Law 1, and the learning centre has
no route to the adapter's headers without a change under `engine/src/`
outside `sim/` - which Law 1 says is a finding to write up rather than a
thing to patch around. So the classification happens here, on the request
the broker is already holding, which is legal, testable, and needs nothing
from the engine.

Two rules govern everything below:

**The header wins.** Inference only runs when nothing tagged the call. A
caller that identifies itself is believed, so this module can never
override a deliberate decision made upstream.

**Ambiguity resolves to cognition.** Misrouting real thinking onto the
background lane would quietly degrade the experiment - an agent's turn
served by the wrong model is a research-validity problem, not a billing
one. Paying Kimi prices for one unrecognised housekeeping call is merely
expensive. The costs are not symmetric, so the default is not neutral.

WHAT THIS RECOVERS. In kasukabe_l3.db, 336 of 779 successful calls are the
post-turn summariser and 364 are real agent turns: close to one
housekeeping call per thought, 30% of the tokens, every one of them drawn
on a Kimi lane an agent needed and billed to that agent's own wallet.
"""

from __future__ import annotations

from typing import Any

from broker.lanes import derive_purpose

BACKGROUND_SYSTEM_MARKERS: tuple[tuple[str, str], ...] = (
    ("knowledge extraction", "learning_centre.knowledge_extraction"),
    ("episode creation engine", "learning_centre.episode_creation"),
    ("importance", "importance_scoring.inferred"),
    ("social graph", "learning_centre.social_graph"),
)

BACKGROUND_MESSAGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("wm_flush", "summarizer.working_memory"),
    ("summarize this exchange", "summarizer.post_turn"),
    ("summarise this exchange", "summarizer.post_turn"),
    ("summarize the following conversation", "summarizer.conversation"),
    ("produce a short progress summary", "summarizer.agent_progress"),
)

UNSPECIFIED = "unspecified"

DERIVED = "auto:"

SOURCE_HEADER = "header"
SOURCE_MARKER = "inferred"
SOURCE_SHAPE = "shape"

def system_text(body: dict[str, Any]) -> str:
    """The system prompt as one lowercased string, whatever shape it came in.

    herald sends a list of cache-controlled blocks; other callers send a
    bare string. A classifier that understands only one shape silently
    misses every call that used the other.
    """
    system = body.get("system")
    if isinstance(system, str):
        return system.lower()
    if isinstance(system, list):
        return " ".join(
            block.get("text", "")
            for block in system
            if isinstance(block, dict)
        ).lower()
    return ""


def last_user_text(body: dict[str, Any]) -> str:
    """Text of the final user message, lowercased. Empty when the last
    turn carried no text - a tool_result continuation, for instance."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return ""
    content = messages[-1].get("content") if isinstance(messages[-1], dict) else None
    if isinstance(content, str):
        return content.lower()
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).lower()
    return ""


def carries_the_towns_tools(body: dict[str, Any]) -> bool:
    """Does this request ship the world's tool schemas?

    The veto that makes a rerouting inference safe. A resident's turn
    always carries the town's tools; the learning centre's housekeeping
    never does. Measured on data/loop5.db: 118 of 118 summariser calls
    carried none, 106 of 106 agent turns carried them, no overlap.

    Without it there is one way the marker rules could misfire - an agent
    SAYS "summarize this exchange", it lands in a neighbour's perception,
    and that neighbour's turn is served on the background lane. With it,
    that request is a resident thinking and is left alone.
    """
    return bool(body.get("tools"))


def purpose_and_source(header: str, body: dict[str, Any]) -> tuple[str, str]:
    """`infer_purpose`'s answer, and where it came from.

    CORE-30. Same classification, one extra fact: whether the engine
    ASSERTED this purpose or the broker worked it out. `infer_purpose`
    stays as the one-value form every existing caller uses.
    """
    tagged = (header or "").strip()
    if tagged and tagged != UNSPECIFIED:
        return tagged, SOURCE_HEADER
    purpose = infer_purpose(header, body)
    if purpose.startswith(DERIVED) and any(
        purpose == DERIVED + tag
        for _marker, tag in BACKGROUND_SYSTEM_MARKERS + BACKGROUND_MESSAGE_MARKERS
    ):
        return purpose, SOURCE_MARKER
    return purpose, SOURCE_SHAPE


def infer_purpose(header: str, body: dict[str, Any]) -> str:
    """The purpose tag this call should carry.

    Returns `header` untouched whenever it says anything at all, so a
    caller that tags itself is never second-guessed. Otherwise, two
    layers, and the difference between them is the whole design:

      * a NARROW match on a verbatim marker from frozen learning-centre
        code, vetoed by the presence of the town's tools, returns an
        `auto:` tag that lanes.py routes to the background pool. This one
        moves the call;
      * anything else falls through to `lanes.derive_purpose`, which names
        the call by shape and is deliberately not entitled to move it.

    Never raises. A malformed body is a reason to stop classifying, never a
    reason to fail a call: the broker's bookkeeping must not be able to
    take down an agent's turn.
    """
    tagged = (header or "").strip()
    if tagged and tagged != UNSPECIFIED:
        return tagged
    if not isinstance(body, dict):
        return DERIVED + "other"

    try:
        if not carries_the_towns_tools(body):
            system = system_text(body)
            for marker, purpose in BACKGROUND_SYSTEM_MARKERS:
                if marker in system:
                    return DERIVED + purpose

            message = last_user_text(body)
            for marker, purpose in BACKGROUND_MESSAGE_MARKERS:
                if marker in message:
                    return DERIVED + purpose
    except (AttributeError, TypeError):
        return DERIVED + "other"

    try:
        return derive_purpose(UNSPECIFIED, body)
    except (AttributeError, TypeError):
        return DERIVED + "other"
