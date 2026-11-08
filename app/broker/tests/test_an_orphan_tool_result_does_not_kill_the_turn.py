"""A tool_result with no tool_use before it costs a resident a whole turn.

THE ERROR, once per run since the series began:

    Bedrock ValidationException: The number of toolResult blocks at
    messages.6.content exceeds the number of toolUse blocks of previous
    turn.

It went undiagnosed for two runs because the trace stored the request as
an unparseable 64 KB prefix. With `capped_body` storing a real object,
run3's failing request reads:

    [0] <4 earlier messages dropped by the trace cap>
    [1] user      ['tool_result']
    [2] assistant ['text']        <- no tool_use anywhere in it
    [3] user      ['tool_result']  <- so this one answers nothing
    [4] user      ['text']
    [5] user      ['text']
    [6] user      ['text']

Two things are wrong and Bedrock refuses the whole request for either.

ONE: message 3 is a tool_result whose preceding assistant turn contains
no tool_use. That is what the message quoted above is about. It happens
when the engine's own turn loop appends a result for a call that was
never committed to the transcript - a retried dispatch, a compaction that
took the assistant turn and left its answer, a tool that ran after the
model had already stopped.

TWO: messages 4, 5 and 6 are three consecutive user turns. Bedrock's
Converse API requires the roles to alternate, and the engine builds these
without checking.

WHAT THE COST IS. The request is refused entire, so the resident loses
the turn - not a degraded answer, no answer at all, and a warning line in
the runner log that says the tick failed. One per run.

WHY THE BROKER FIXES IT. The transcript is built in frozen engine_core
(ROADMAP Law 1) and this is a WIRE-FORMAT requirement of the API the
broker owns, not a decision about what a conversation should contain.
Nothing is deleted: an orphan result becomes a text block carrying the
same words, and merged turns keep every block in order. The model sees
everything it would have seen; what changes is only that the request is
one Bedrock will accept.
"""

from __future__ import annotations

import pytest

from broker.errors import InvalidRequestError
from broker.translate import legalise_transcript, messages_to_bedrock


def user_result(tool_use_id: str = "t1", text: str = "arrived at the mill") -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}],
    }


def assistant_call(tool_use_id: str = "t1", name: str = "world_move") -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}
        ],
    }


def said(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


# -- A healthy transcript is untouched --------------------------------


def test_a_well_formed_exchange_passes_through_unchanged() -> None:
    messages = [
        said("user", "It is morning."),
        assistant_call(),
        user_result(),
        said("assistant", "I am at the mill."),
    ]
    assert legalise_transcript(messages) == messages


def test_several_results_for_several_calls_are_kept() -> None:
    """A turn that made three calls gets three results, and all three are
    legitimate. A fix that kept only the first would quietly discard two."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "world_look_around", "input": {}}
                for i in range(3)
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}
                for i in range(3)
            ],
        },
    ]
    assert legalise_transcript(messages) == messages


# -- The orphan ------------------------------------------------------


def test_an_orphan_result_becomes_text_rather_than_killing_the_turn() -> None:
    """run3's message 3, exactly: a result whose assistant turn said only
    text."""
    messages = [
        said("assistant", "I will think about it."),
        user_result("t99", "you harvested 3 wheat"),
    ]
    fixed = legalise_transcript(messages)
    blocks = fixed[-1]["content"]
    assert all(b["type"] != "tool_result" for b in blocks)
    assert "3 wheat" in " ".join(b.get("text", "") for b in blocks), (
        "the result was deleted rather than carried across, so the resident "
        "no longer knows what its own action did"
    )


def test_an_orphan_result_at_the_very_start_is_handled() -> None:
    """There is no previous turn at all. The old code sent it anyway."""
    fixed = legalise_transcript([user_result("t1", "you arrived")])
    assert all(b["type"] != "tool_result" for b in fixed[0]["content"])


def test_a_result_whose_id_does_not_match_is_an_orphan_too() -> None:
    """Matching on COUNT alone would pass this: one call, one result, and
    they are about different things."""
    messages = [assistant_call("t1"), user_result("t2", "unrelated")]
    fixed = legalise_transcript(messages)
    assert all(b["type"] != "tool_result" for b in fixed[-1]["content"])


def test_a_real_result_beside_an_orphan_keeps_the_real_one() -> None:
    messages = [
        assistant_call("t1"),
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "real"},
                {"type": "tool_result", "tool_use_id": "t9", "content": "orphan"},
            ],
        },
    ]
    blocks = legalise_transcript(messages)[-1]["content"]
    kinds = [b["type"] for b in blocks]
    assert kinds.count("tool_result") == 1
    assert "orphan" in " ".join(b.get("text", "") for b in blocks)


# -- Alternation -----------------------------------------------------


def test_consecutive_user_turns_are_merged() -> None:
    """run3's messages 4, 5 and 6. Converse requires alternating roles."""
    messages = [said("user", "one"), said("user", "two"), said("user", "three")]
    fixed = legalise_transcript(messages)
    assert len(fixed) == 1
    text = " ".join(b.get("text", "") for b in fixed[0]["content"])
    assert "one" in text and "two" in text and "three" in text


def test_merging_preserves_order() -> None:
    messages = [said("user", "first"), said("user", "second")]
    blocks = legalise_transcript(messages)[0]["content"]
    joined = " ".join(b.get("text", "") for b in blocks)
    assert joined.index("first") < joined.index("second")


def test_consecutive_assistant_turns_are_merged_too() -> None:
    messages = [said("assistant", "a"), said("assistant", "b")]
    assert len(legalise_transcript(messages)) == 1


def test_the_result_always_alternates() -> None:
    messages = [said("user", "a"), said("user", "b"), said("assistant", "c"),
                said("assistant", "d"), said("user", "e")]
    roles = [m["role"] for m in legalise_transcript(messages)]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles


# -- Wired to the path that builds the request ------------------------


def test_messages_to_bedrock_accepts_the_shape_that_used_to_be_refused() -> None:
    """Gate 1. The whole point is that this reaches the real request
    builder: a legaliser nothing calls would leave the turn dying exactly
    as before."""
    messages = [
        said("assistant", "thinking"),
        user_result("t99", "you harvested 3 wheat"),
        said("user", "then"),
        said("user", "and then"),
    ]
    out = messages_to_bedrock(messages)
    roles = [m["role"] for m in out]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles
    for message in out:
        for block in message["content"]:
            assert "toolResult" not in block or block.get("toolResult")


def test_a_genuinely_invalid_block_is_still_refused() -> None:
    """The legaliser must not become a way for real errors to pass. It
    fixes two known wire-format faults and nothing else."""
    with pytest.raises(InvalidRequestError):
        messages_to_bedrock([{"role": "user", "content": [{"type": "nonsense"}]}])


# ── ENG-10: the repair is counted, not silent ───────────────────────────

def test_a_rewrite_is_counted_and_logged(caplog) -> None:
    """ENG-10. The repair itself is right and is kept - it PRESERVES the
    text where the engine's own `repair_tool_use_result_pairing` strips
    it. What was missing is that it happened silently, so an engine
    invariant break (a MAX_TOKENS tombstone, a cancellation, an emergency
    compaction that split a round) looked exactly like a healthy run.

    A rising count is the signal: not an error, because the turn
    survives, but a number somebody can watch.
    """
    import logging

    from broker import translate

    before = translate.orphans_rewritten
    with caplog.at_level(logging.WARNING, logger="kasukabe.broker.translate"):
        fixed = translate.legalise_transcript([user_result("t1", "you arrived")])

    assert fixed != [user_result("t1", "you arrived")], "premise: it repaired"
    assert translate.orphans_rewritten == before + 1, (
        "ENG-10: the orphan was rewritten and nothing counted it, so a "
        "rising rate of engine invariant breaks stays invisible"
    )
    assert "orphaned tool result" in caplog.text


def test_a_clean_transcript_counts_nothing(caplog) -> None:
    """The control. A counter that ticks on healthy traffic is a counter
    nobody can read a trend from."""
    from broker import translate

    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "move", "input": {}},
        ]},
        user_result("t1", "you arrived"),
    ]
    before = translate.orphans_rewritten
    translate.legalise_transcript(messages)
    assert translate.orphans_rewritten == before
