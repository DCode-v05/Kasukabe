"""Does this request fit in the window of the lane that will serve it?

THE INCIDENT this exists for, measured in data/loop4.db:

    Bedrock ValidationException: This model's maximum context length is
    163840 tokens. However, you requested 4096 output tokens and your
    prompt contains at least 159745 input tokens, for a total of at least
    163841 tokens.

Over by one token. Eighteen of that run's nineteen failures were this, all
from one resident, and every one of them was a working-memory flush. The
broker paid a full round trip to Bedrock to be told, and then recorded the
result as an upstream error - which reads, in every report since, as
"Bedrock was unhappy" rather than "we sent something impossible". The
arithmetic was available before dispatch the whole time.

TWO OUTCOMES, and the difference between them is the design.

  * `max_tokens` is in the way and nothing else. Clamped, and the clamp is
    named in the trace. A shorter reply is the same conversation, so this
    costs the run nothing except an answer that stops sooner.

  * The prompt alone will not fit. Refused, with the numbers. NOT trimmed:
    an answer computed from a conversation nobody chose is worse than no
    answer, because the run looks healthy and the record is fiction. This
    project has already shipped one metric read off a truncated body and
    Build Spec v2 section 1 has a corollary about it.

WHY THE SERVING MODEL AND NOT THE REQUESTED ONE. Cognition overflows from
Kimi onto DeepSeek when the Kimi lanes saturate (lanes.py's routing
policy), so the request may name one model and be served by another with a
different window. `PreparedCall` carries both; this takes the one that
will actually see it.

AN UNKNOWN MODEL GETS NO OPINION. A lane whose window nobody has recorded
is left alone rather than checked against somebody else's number, because
a wrong window here would refuse healthy calls, which is worse than the
round trip it was meant to save.
"""

from __future__ import annotations

import json
from typing import Any

from broker.config import DEEPSEEK_MODEL_ID, KIMI_MODEL_ID
from broker.errors import InvalidRequestError
from broker.tokens import estimate_tokens

CONTEXT_WINDOW_TOKENS: dict[str, int] = {
    KIMI_MODEL_ID: 262_144,
    DEEPSEEK_MODEL_ID: 163_840,
}

MIN_USABLE_OUTPUT_TOKENS = 256

ESTIMATE_MARGIN_TOKENS = 2_000


def prompt_tokens(body: dict[str, Any]) -> int:
    """A conservative estimate of everything that counts as input.

    Serialised whole rather than walked field by field: system prompts
    arrive as a string or as a list of blocks, message content as either,
    and tool schemas are input too. A walker that understood only some of
    those shapes would undercount exactly the requests that overflow.
    """
    try:
        return estimate_tokens(json.dumps(body, default=str))
    except (TypeError, ValueError):
        return 0


def fit_to_context_window(
    body: dict[str, Any], serving_model: str
) -> tuple[dict[str, Any], str | None]:
    """(the request to send, a note for the trace when anything changed).

    Returns the caller's own dict, unmodified and uncopied, when the
    request already fits - which is the overwhelmingly common case and
    should cost nothing.

    Raises InvalidRequestError when no clamp can save it.
    """
    window = CONTEXT_WINDOW_TOKENS.get(serving_model)
    if window is None:
        return body, None

    budget = window - ESTIMATE_MARGIN_TOKENS
    prompt = prompt_tokens(body)
    requested_output = body.get("max_tokens")
    if not isinstance(requested_output, int) or requested_output <= 0:
        requested_output = 0

    if prompt + requested_output <= budget:
        return body, None

    room = budget - prompt
    if room < MIN_USABLE_OUTPUT_TOKENS:
        raise InvalidRequestError(
            f"prompt is ~{prompt:,} tokens against a {window:,}-token window on "
            f"{serving_model}, leaving {room:,} for a reply. Nothing was sent. "
            "Measured cause in every observed case: an unbounded wm_flush from "
            "the engine's working memory, which packs every buffered tool call "
            "into one message. The prompt is NOT trimmed to fit - an answer "
            "computed from a conversation nobody chose would be worse than none."
        )

    clamped = dict(body)
    clamped["max_tokens"] = room
    return clamped, (
        f"max_tokens clamped {requested_output} -> {room} to fit "
        f"{serving_model}'s {window:,}-token window (prompt ~{prompt:,})"
    )
