"""Streams a different model would plausibly produce.

Each hazard is a rewrite of a well-formed `converse_stream` event list,
and each one names the line it is aimed at. None of them is invented for
sport: every one is a shape some real provider emits and this call path
assumes away.

The bar for all of them is the same, and it is deliberately low:

    The town may lose the turn. It may not lose the truth.

A dropped tool call is survivable - a resident wastes a turn. What is not
survivable is a dropped tool call that the trace records as a completed
turn, a ledger that moves without a row, or a resident that vanishes from
the run. So these do not assert "the call succeeded". They assert that
whatever happened is visible afterwards, and that no invariant broke.
"""
from __future__ import annotations

import json
from collections.abc import Callable

Mutator = Callable[[list[dict]], list[dict]]


def _is(event: dict, key: str) -> bool:
    return key in event


def truncate_tool_json(cut: int = 12) -> Mutator:
    """Cut a tool call's arguments off mid-JSON.

    AIMED AT `herald/provider.py:680` - `json.loads(current_tool_json)`,
    unguarded, at `content_block_stop`. The 50-resident run of 2026-09-16
    lost 41 turns across 33 of its 50 residents to this single exception,
    and the severity is the turn, not the call: a resident that harvested,
    walked, and then began a malformed third call loses the harvest and
    the walk with it.

    `engine/src/sim/half_written_tool_call.py` catches `JSONDecodeError`
    only. A model whose truncation produces *valid* JSON of the wrong
    shape walks straight past it - which is why the story that uses this
    also checks the world, not just the absence of a traceback.
    """

    def mutate(events: list[dict]) -> list[dict]:
        out = []
        for e in events:
            d = e.get("contentBlockDelta", {}).get("delta", {})
            if "toolUse" in d:
                body = d["toolUse"]["input"]
                e = {
                    "contentBlockDelta": {
                        **e["contentBlockDelta"],
                        "delta": {"toolUse": {"input": body[:cut]}},
                    }
                }
            out.append(e)
        return out

    return mutate


def drop_tool_block_start(events: list[dict]) -> list[dict]:
    """Omit `contentBlockStart` for a tool block.

    AIMED AT `herald/provider.py:653-657`, where `current_tool_id` is set
    ONLY by that event, and `:674`'s `if current_tool_id:` therefore
    silently discards the entire call. No exception, no log, no trace row.

    The engine is protected from this today only because the broker is
    generous: `translate.py:781-808` synthesises the starts Bedrock omits.
    That generosity is a text-block rule. If a provider omits the start
    for a *tool* block, nothing puts it back.
    """
    return [
        e
        for e in events
        if not (
            _is(e, "contentBlockStart")
            and "toolUse" in e["contentBlockStart"].get("start", {})
        )
    ]


def interleave_tool_blocks(events: list[dict]) -> list[dict]:
    """Open a second tool block before the first has stopped.

    AIMED AT `herald/provider.py:624-626`. The open call is held in three
    *scalars* - `current_tool_id`, `current_tool_name`, `current_tool_json`
    - and `event["index"]` is never read anywhere in `stream_with_tools`.
    Anthropic and Bedrock both happen to emit blocks sequentially, so the
    bug is invisible today. A provider that interleaves concatenates two
    arguments into one string and then parses it: either a crash, or -
    far worse - a tool called with somebody else's arguments.
    """
    starts = [e for e in events if _is(e, "contentBlockStart")]
    deltas = [e for e in events if _is(e, "contentBlockDelta")]
    stops = [e for e in events if _is(e, "contentBlockStop")]
    if len(starts) < 2:
        return events
    head = [e for e in events if _is(e, "messageStart")]
    tail = [e for e in events if _is(e, "messageStop") or _is(e, "metadata")]
    return [*head, starts[0], starts[1], *deltas, *stops, *tail]


def unknown_stop_reason(reason: str = "guardrail_intervened") -> Mutator:
    """Finish with a stop reason nobody mapped.

    AIMED AT `STOP_MAP` (`herald/provider.py:573-577` and `:617-621`).
    Three reasons are mapped; everything else becomes `END_TURN`.
    `StopReason.REFUSAL` is declared in `types.py:274` and is produced by
    neither map, so a content-filtered or guardrail-stopped turn arrives
    at the agent loop looking exactly like an ordinary finished one.

    GPT-class models refuse far more often than Kimi does. On a swap this
    stops being a curiosity and becomes a silent, recurring loss.
    """

    def mutate(events: list[dict]) -> list[dict]:
        return [
            {"messageStop": {"stopReason": reason}} if _is(e, "messageStop") else e
            for e in events
        ]

    return mutate


def tool_call_as_prose(events: list[dict]) -> list[dict]:
    """Write the call out as text instead of calling it.

    Kimi does this, which is why `broker/kimi_tool_calls.py` exists. This
    hazard uses a spelling that module does NOT recognise, standing in for
    the next model's private format. The correct outcome is not recovery -
    it is that the turn is recorded as having done nothing, rather than
    being billed as a success.
    """
    out: list[dict] = []
    index = 0
    for e in events:
        start = e.get("contentBlockStart", {}).get("start", {})
        if "toolUse" in start:
            index = e["contentBlockStart"]["contentBlockIndex"]
            continue
        d = e.get("contentBlockDelta", {}).get("delta", {})
        if "toolUse" in d:
            out.append(
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": index,
                        "delta": {
                            "text": "[[call:world_look_around "
                            f"{d['toolUse']['input']}]]"
                        },
                    }
                }
            )
            continue
        out.append(e)
    return [
        {"messageStop": {"stopReason": "end_turn"}} if _is(e, "messageStop") else e
        for e in out
    ]


def duplicate_tool_use_ids(events: list[dict]) -> list[dict]:
    """Give every call in the turn the same id.

    Tool results are matched back to calls by id. `translate.py:225-304`
    (`legalise_transcript`) rewrites orphaned `tool_result` blocks to text
    because Bedrock rejects them - so a collision does not crash, it
    quietly attaches one call's answer to another call's question.
    """
    return [
        {
            "contentBlockStart": {
                **e["contentBlockStart"],
                "start": {
                    "toolUse": {
                        **e["contentBlockStart"]["start"]["toolUse"],
                        "toolUseId": "tooluse_collision",
                    }
                },
            }
        }
        if _is(e, "contentBlockStart")
        and "toolUse" in e["contentBlockStart"].get("start", {})
        else e
        for e in events
    ]


def stream_ends_early(events: list[dict]) -> list[dict]:
    """Stop sending before `messageStop`.

    AIMED AT `provider.py:673-685` and `:713`. A tool call is emitted only
    at `content_block_stop`, and the terminal event is yielded after the
    loop ends rather than on `message_stop` - so a connection cut mid-block
    loses the call with no trace, and the turn still terminates normally.
    """
    cut = [e for e in events if not (_is(e, "messageStop") or _is(e, "metadata"))]
    return cut[:-1] if len(cut) > 1 else cut


def friendly_text_block_start(events: list[dict]) -> list[dict]:
    """Send `contentBlockStart` for text, which Bedrock usually omits.

    The inverse hazard, and the reason it is here: `translate.py:637-660`
    synthesises the opening block *because* it is absent. A provider that
    sends it may produce two openings for one block. `MockBedrockGateway`
    defaulted to this friendly shape and that is precisely how defect D1
    survived fifteen runs with a green suite.
    """
    out: list[dict] = []
    for e in events:
        d = e.get("contentBlockDelta", {}).get("delta", {})
        if "text" in d:
            out.append(
                {
                    "contentBlockStart": {
                        "contentBlockIndex": e["contentBlockDelta"][
                            "contentBlockIndex"
                        ],
                        "start": {},
                    }
                }
            )
        out.append(e)
    return out


def arguments_of_the_wrong_type(events: list[dict]) -> list[dict]:
    """Valid JSON, wrong types - numbers as strings, scalars as lists.

    Slips past `half_written_tool_call.py`, which catches only
    `JSONDecodeError`. What must hold is `registry.validate_params`'s
    contract: a refusal that names the real parameters, never a silent
    drop. TRADE-01 is the cost of getting that wrong - 6,424 calls settled
    nothing because pydantic dropped an unknown field and returned
    SUCCESS for an act nobody asked for.
    """

    def wrong(value: object) -> object:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return [value]
        return value

    out: list[dict] = []
    for e in events:
        d = e.get("contentBlockDelta", {}).get("delta", {})
        if "toolUse" in d:
            try:
                args = json.loads(d["toolUse"]["input"])
            except json.JSONDecodeError:
                out.append(e)
                continue
            if isinstance(args, dict):
                args = {k: wrong(v) for k, v in args.items()}
            e = {
                "contentBlockDelta": {
                    **e["contentBlockDelta"],
                    "delta": {"toolUse": {"input": json.dumps(args)}},
                }
            }
        out.append(e)
    return out


ALL: dict[str, Mutator] = {
    "tool json truncated mid-argument": truncate_tool_json(),
    "no contentBlockStart for a tool block": drop_tool_block_start,
    "two tool blocks interleaved": interleave_tool_blocks,
    "an unmapped stop reason": unknown_stop_reason(),
    "a refusal stop reason": unknown_stop_reason("content_filtered"),
    "the call written as prose": tool_call_as_prose,
    "colliding toolUse ids": duplicate_tool_use_ids,
    "the stream cut before messageStop": stream_ends_early,
    "contentBlockStart sent for text": friendly_text_block_start,
    "arguments of the wrong type": arguments_of_the_wrong_type,
}
