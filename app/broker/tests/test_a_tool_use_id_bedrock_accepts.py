"""A tool-call id reaches Bedrock in a shape Bedrock accepts.

Run 28b, 20:18:02, resident n13:

    Bedrock ValidationException: 2 validation errors detected: Value at
    'messages.42.member.content.2.member.toolUse.toolUseId' failed to satisfy
    constraint: Member must satisfy regular expression pattern:
    [a-zA-Z0-9_.:-]+; Value at 'messages.43.member.content.1.member.
    toolResult.toolUseId' failed to satisfy constraint: ...

The id in the history was `functions world_rest:8` - Kimi's native tool-call
id with a space where its dot belongs. `content_block_to_bedrock` passed
`block["id"]` and `block["tool_use_id"]` to Bedrock untouched, so one
malformed id cost the resident its turn, surfacing in the engine as
"peer closed connection without sending complete message body".

The fix has to keep the pair together: a `toolUse` and the `toolResult`
answering it must still carry the same id after cleaning, or Bedrock refuses
the history for a different reason. So the cleaning is a pure function of
the id.
"""

from __future__ import annotations

import re

from broker.translate import content_block_to_bedrock

BEDROCK_ID = re.compile(r"[a-zA-Z0-9_.:-]+")
RUN_28B_ID = "functions world_rest:8"


def _use(tool_use_id: str) -> str:
    block = {"type": "tool_use", "id": tool_use_id, "name": "world_rest", "input": {}}
    return content_block_to_bedrock(block)["toolUse"]["toolUseId"]


def _result(tool_use_id: str) -> str:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}
    return content_block_to_bedrock(block)["toolResult"]["toolUseId"]


def test_the_run_28b_id_is_one_bedrock_accepts():
    assert BEDROCK_ID.fullmatch(_use(RUN_28B_ID)), _use(RUN_28B_ID)
    assert BEDROCK_ID.fullmatch(_result(RUN_28B_ID)), _result(RUN_28B_ID)


def test_a_call_and_its_result_still_share_an_id():
    assert _use(RUN_28B_ID) == _result(RUN_28B_ID)


def test_a_valid_id_is_left_exactly_as_it_was():
    for valid in ("toolu_01AbCdEf", "functions.world_rest:8", "call-1.a:b"):
        assert _use(valid) == valid
        assert _result(valid) == valid


def test_two_ids_that_differ_stay_different():
    """Cleaning must not fold two distinct calls in one turn into one id."""
    assert _use("functions world_rest:8") != _use("functions world_rest:9")
