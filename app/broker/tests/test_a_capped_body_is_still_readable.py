"""A trace row too big to store whole must still be a row you can read.

MEASURED, data/loop5.db: 342 of 566 successful calls - 60% - stored no
request at all. Each holds `{"_truncated": true, "_original_bytes": ...,
"_head": "<the first 65,536 characters of the JSON>"}`. The head is a
STRING, cut mid-token, so it is not JSON and nothing can parse it. Every
analysis that reads `request_body` therefore sees nothing for six calls in
ten - and they are the six that matter, because they are the big ones.

The cap itself is right and must stay: an uncapped trace filled the disk
and took a run down with it, losing the record it existed to keep. What
was wrong is the SHAPE of what it leaves behind, and the comment beside
it, which claims "64 KB holds every honest prompt this system produces
with room to spare". Measured against the same run: the median request is
76 KB and the largest is 154 KB. The cap fires on the ordinary case.

WHAT THIS CHANGES. A capped body stays a valid object. The system prompt,
the model, max_tokens, the tool names and the newest messages survive
whole; the middle of the conversation is replaced by one marker recording
how many messages went and how large they were. So `json.loads` works,
`body["system"]` works, `body["tools"]` works, and a reader can still tell
what the call WAS - which is what every question anyone has asked of this
store actually needs.

WHAT IT DOES NOT CHANGE. Truncation is still announced. Build Spec v2
section 1's corollary - "a metric read from a truncated or partial source
is not evidence" - depends on `_truncated` being findable, and it is, at
the top level where it always was.
"""

from __future__ import annotations

import json

from broker.trace import TRACE_BODY_LIMIT, capped_body


def big_request(messages: int = 40, chars: int = 4_000) -> dict:
    return {
        "model": "moonshotai.kimi-k2.5",
        "max_tokens": 4096,
        "system": [{"type": "text", "text": "You are Marta Thorne, the town's baker."}],
        "tools": [{"name": f"world_tool_{i}", "input_schema": {}} for i in range(50)],
        "messages": [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * chars}
            for i in range(messages)
        ],
    }


def test_a_small_body_is_stored_exactly_as_it_came() -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
    assert capped_body(body) is body


def test_a_capped_body_is_valid_json() -> None:
    """The whole point. 342 of 566 rows in loop5 are an unparseable string
    prefix, so every reader of that store sees nothing for the majority of
    its calls."""
    stored = capped_body(big_request())
    round_tripped = json.loads(json.dumps(stored, default=str))
    assert isinstance(round_tripped, dict)


def test_the_cap_is_actually_respected() -> None:
    stored = capped_body(big_request())
    assert len(json.dumps(stored, default=str)) <= TRACE_BODY_LIMIT


def test_it_still_announces_that_it_was_truncated() -> None:
    stored = capped_body(big_request())
    assert stored["_truncated"] is True
    assert stored["_original_bytes"] > TRACE_BODY_LIMIT


def test_the_system_prompt_survives_whole() -> None:
    """It is the single most-read field in this store: every question
    about who an agent thought it was, and every purpose classification,
    reads it."""
    body = big_request()
    stored = capped_body(body)
    assert stored["system"] == body["system"]


def test_the_model_and_the_ceiling_survive() -> None:
    body = big_request()
    stored = capped_body(body)
    assert stored["model"] == body["model"]
    assert stored["max_tokens"] == body["max_tokens"]


def test_the_tools_survive_as_names() -> None:
    """Fifty schemas are most of the bytes and almost none of the meaning.
    WHICH tools were offered is a real question - it is how the seven
    assistant tools were found - and the schemas themselves are identical
    on every call in a run."""
    body = big_request()
    stored = capped_body(body)
    assert len(stored["tools"]) == len(body["tools"])
    assert "world_tool_0" in json.dumps(stored["tools"])


def test_the_newest_messages_survive() -> None:
    """The tail is the turn. The old cap kept the HEAD, which on a long
    conversation is a resident's first morning and nothing about what it
    was actually doing when this call was made."""
    body = big_request()
    body["messages"][-1]["content"] = "THE NEWEST THING SAID"
    stored = capped_body(body)
    assert "THE NEWEST THING SAID" in json.dumps(stored["messages"])


def test_the_dropped_middle_is_counted_not_silently_gone() -> None:
    body = big_request()
    stored = capped_body(body)
    assert len(stored["messages"]) < len(body["messages"])
    marker = json.dumps(stored["messages"])
    assert "_dropped" in marker


def test_a_body_whose_system_prompt_alone_is_too_big_still_parses() -> None:
    """The pathological case the old head-slice handled by accident. A
    150 KB system prompt is real - one was measured - and the answer has
    to still be an object."""
    body = {"model": "m", "system": "x" * (TRACE_BODY_LIMIT * 3), "messages": []}
    stored = capped_body(body)
    assert isinstance(stored, dict)
    assert stored["_truncated"] is True
    assert len(json.dumps(stored, default=str)) <= TRACE_BODY_LIMIT


def test_the_cap_can_still_be_lifted_for_a_deep_trace_run() -> None:
    body = big_request()
    assert capped_body(body, limit=0) is body


def test_a_body_that_is_none_stays_none() -> None:
    assert capped_body(None) is None
