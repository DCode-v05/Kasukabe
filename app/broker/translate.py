"""Anthropic Messages wire <-> Bedrock converse wire, both directions,
both streaming and non-streaming.

This is the module that makes "the engine's herald can point base_url at
it unmodified" (TASK spec) true. herald speaks exactly the Anthropic
Messages wire shape (engine/src/herald/provider.py's blocks_to_wire /
to_wire_tools / tool_result_to_wire is the exhaustive source of what it
sends; engine/src/herald/provider.py's stream_with_tools is the exhaustive
source of what it reads back from an SSE stream). Every function here is
written against those two functions, not against the general Anthropic
API surface - broker translates exactly what herald can produce, and
raises InvalidRequestError (aggregated, one message per bad block) for
anything else.

Bedrock's converse/converse_stream API is documented at
https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
Event-stream shape (one dict, one top-level key per event) comes from
botocore's own EventStream decoding, not from this module.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from broker.kimi_tool_calls import MarkupBuffer, synthetic_tool_use_id
from broker.bedrock import new_bedrock_message_id
from broker.errors import InvalidRequestError

# Anthropic image media_type -> Bedrock image format. Bedrock's converse
# image block wants a bare format string, Anthropic's wants a MIME type;
# this is the complete set Bedrock's converse API accepts for the
# "image" content block type.
MEDIA_TYPE_TO_BEDROCK_FORMAT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

STOP_REASON_BEDROCK_TO_ANTHROPIC = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    # Bedrock-specific outcomes with no exact Anthropic equivalent;
    # "refusal" is the closest member of Anthropic's stop_reason enum
    # (end_turn/max_tokens/stop_sequence/tool_use/pause_turn/refusal).
    "content_filtered": "refusal",
    "guardrail_intervened": "refusal",
}

BEDROCK_TOOL_NAME_MAX_LEN = 64


# ── Request: Anthropic -> Bedrock converse ──────────────────────────────


def image_source_to_bedrock(source: dict[str, Any]) -> dict[str, Any]:
    if source.get("type") != "base64":
        # ImageBlock's other documented shape is {"type": "url", "url": ...}
        # (engine/src/herald/types.py). Bedrock converse's image block only
        # accepts inline bytes (or an S3 location broker has no reason to
        # construct) - fetching an arbitrary URL server-side is both a
        # feature this system has no current use for and a real SSRF
        # surface, so it's an explicit reject, not a silent drop.
        raise InvalidRequestError(
            f"image source type {source.get('type')!r} is not supported - "
            "only base64-encoded images can be translated to Bedrock converse"
        )
    media_type = source.get("media_type", "")
    fmt = MEDIA_TYPE_TO_BEDROCK_FORMAT.get(media_type)
    if fmt is None:
        allowed = ", ".join(sorted(MEDIA_TYPE_TO_BEDROCK_FORMAT))
        raise InvalidRequestError(f"image media_type {media_type!r} is not one of: {allowed}")
    try:
        raw_bytes = base64.b64decode(source["data"], validate=True)
    except Exception as e:
        raise InvalidRequestError(f"image data is not valid base64: {e}") from None
    return {"format": fmt, "source": {"bytes": raw_bytes}}


def tool_result_content_to_bedrock(content: "str | list[dict[str, Any]]") -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    out: list[dict[str, Any]] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            out.append({"text": block.get("text", "")})
        elif btype == "image":
            out.append({"image": image_source_to_bedrock(block.get("source", {}))})
        else:
            raise InvalidRequestError(
                f"tool_result content block type {btype!r} is not supported"
            )
    return out


def bedrock_tool_use_id(tool_use_id: Any) -> str:
    """A tool-call id in the shape Bedrock accepts: `[a-zA-Z0-9_.:-]+`.

    Run 28b: Kimi handed back `functions world_rest:8` - its native id with a
    space where the dot belongs - and the history carrying it was refused
    whole ("Member must satisfy regular expression pattern"), costing the
    resident its turn.

    A pure function of the id, so a `toolUse` and the `toolResult` answering
    it still match after cleaning. A valid id comes back untouched; a
    cleaned one carries a short digest of the original, so two ids that
    differ only in a character Bedrock rejects do not collapse into one.
    """
    import hashlib
    import re

    raw = str(tool_use_id)
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]", "_", raw)
    if cleaned == raw and raw:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned or 'tool_use'}-{digest}"


def content_block_to_bedrock(block: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one Anthropic content block. Returns None for blocks that
    are valid but carry no Bedrock equivalent (thinking/redacted_thinking -
    see module docstring: broker never emits these itself, so they can
    only appear via a replayed cross-provider history, and dropping them
    is strictly safer than failing the whole request over stale
    reasoning trace the model never needed as literal input)."""
    btype = block.get("type")
    if btype == "text":
        return {"text": block.get("text", "")}
    if btype == "image":
        return {"image": image_source_to_bedrock(block.get("source", {}))}
    if btype == "tool_use":
        return {
            "toolUse": {
                "toolUseId": bedrock_tool_use_id(block["id"]),
                "name": block["name"],
                "input": block.get("input", {}),
            }
        }
    if btype == "tool_result":
        result: dict[str, Any] = {
            "toolUseId": bedrock_tool_use_id(block["tool_use_id"]),
            "content": tool_result_content_to_bedrock(block.get("content", "")),
        }
        if block.get("is_error"):
            result["status"] = "error"
        else:
            result["status"] = "success"
        return {"toolResult": result}
    if btype in ("thinking", "redacted_thinking"):
        return None
    raise InvalidRequestError(f"message content block type {btype!r} is not supported")


def message_to_bedrock(message: dict[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if role not in ("user", "assistant"):
        raise InvalidRequestError(f"message role {role!r} must be 'user' or 'assistant'")
    content = message.get("content", "")
    if isinstance(content, str):
        blocks = [{"text": content}] if content else []
    else:
        blocks = [b for b in (content_block_to_bedrock(item) for item in content) if b is not None]
    return {"role": role, "content": blocks}


# Prefix on a tool result that has been rewritten as text because nothing
# it could answer is in the transcript. Marked rather than silent: a
# reader of the request must be able to tell a rewritten block from one
# the engine actually sent.
logger = logging.getLogger("kasukabe.broker.translate")

# ENG-10. How many orphaned tool results this broker has rewritten.
#
# The repair itself is the right one and is kept: it PRESERVES the text,
# where the engine's own `repair_tool_use_result_pairing` strips it. What
# was missing is that it happened silently, so an engine invariant break -
# a MAX_TOKENS tombstone, a cancellation, an emergency compaction that
# split a round - looked exactly like a healthy run.
#
# A rising count is the signal the finding asks for: not an error, because
# the turn survives, but a number somebody can watch.
orphans_rewritten = 0

ORPHAN_RESULT_PREFIX = "[result of an earlier action] "


def tool_use_ids(message: "dict[str, Any] | None") -> set[str]:
    """Every tool_use id in this message. Empty for anything else."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return set()
    content = message.get("content")
    if not isinstance(content, list):
        return set()
    return {
        str(block.get("id"))
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
    }


def result_as_text(block: dict[str, Any]) -> dict[str, Any]:
    """An orphan tool_result, carried across as text.

    Rewritten and not dropped, deliberately. The block says what the
    resident's own action did, and a transcript that loses it leaves an
    agent that harvested three wheat with no idea whether it worked.
    """
    body = block.get("content")
    if isinstance(body, list):
        body = " ".join(
            str(part.get("text", part)) if isinstance(part, dict) else str(part)
            for part in body
        )
    return {"type": "text", "text": f"{ORPHAN_RESULT_PREFIX}{body}"}


def legalise_transcript(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript, in a shape Bedrock's Converse API will accept.

    TWO FAULTS, both seen in one real request (run3, one failure per run
    since the series began, refused entire so the resident lost the turn):

        [1] user      ['tool_result']
        [2] assistant ['text']        <- no tool_use anywhere in it
        [3] user      ['tool_result']  <- answers nothing
        [4] user      ['text']
        [5] user      ['text']
        [6] user      ['text']

    An ORPHAN RESULT is a tool_result whose immediately preceding
    assistant turn holds no tool_use with that id. Bedrock counts them and
    refuses: "The number of toolResult blocks ... exceeds the number of
    toolUse blocks of previous turn." Matched on ID rather than on count,
    because one call and one result that are about different things pass a
    count check and are still wrong.

    CONSECUTIVE SAME-ROLE TURNS are the second: Converse requires the
    roles to alternate and the engine builds these without checking.

    NOTHING IS DELETED. An orphan becomes a text block with the same
    words; merged turns keep every block in order. The model sees what it
    would have seen. This is a wire-format repair, which is the broker's
    job - the transcript itself is built in frozen engine_core, and what a
    conversation should CONTAIN is not this function's business.
    """
    repaired: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            repaired.append(message)
            continue
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, list):
            allowed = tool_use_ids(repaired[-1] if repaired else None)
            rebuilt = [
                block
                if not (isinstance(block, dict) and block.get("type") == "tool_result")
                or str(block.get("tool_use_id")) in allowed
                else result_as_text(block)
                for block in content
            ]
            if rebuilt != content:
                # ENG-10: count and say so. Silent repair hides an engine
                # invariant break behind a healthy-looking run.
                global orphans_rewritten
                how_many = sum(
                    1 for before, after in zip(content, rebuilt) if before is not after
                )
                orphans_rewritten += how_many
                logger.warning(
                    "legalised %d orphaned tool result(s) into text; %d since "
                    "start. These arise from MAX_TOKENS tombstones, "
                    "cancellations, or an emergency compaction that split a "
                    "round - a rising rate is an engine invariant breaking, "
                    "not a broker problem.",
                    how_many, orphans_rewritten,
                )
                message = {**message, "content": rebuilt}
        repaired.append(message)

    merged: list[dict[str, Any]] = []
    for message in repaired:
        previous = merged[-1] if merged else None
        if (
            isinstance(message, dict)
            and isinstance(previous, dict)
            and previous.get("role") == message.get("role")
            and isinstance(previous.get("content"), list)
            and isinstance(message.get("content"), list)
        ):
            merged[-1] = {
                **previous,
                "content": [*previous["content"], *message["content"]],
            }
            continue
        merged.append(message)
    return merged


def messages_to_bedrock(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = legalise_transcript(messages)
    errors: list[str] = []
    out: list[dict[str, Any]] = []
    for i, message in enumerate(messages):
        try:
            out.append(message_to_bedrock(message))
        except InvalidRequestError as e:
            errors.append(f"messages[{i}]: {e.message}")
    if errors:
        # Aggregated per app/CONVENTIONS.md ("aggregate errors instead of
        # failing on the first one where the caller benefits from seeing
        # everything wrong at once") - a caller building a bad request
        # from a bug wants every offending message index in one 400, not
        # one restart-and-resubmit per block.
        raise InvalidRequestError("; ".join(errors))
    return out


def system_to_bedrock(system: "str | list[dict[str, Any]] | None") -> list[dict[str, Any]]:
    if system is None:
        return []
    if isinstance(system, str):
        return [{"text": system}] if system else []
    return [{"text": block.get("text", "")} for block in system if block.get("text")]


def bedrock_tool_name(name: str) -> str:
    # herald already sanitizes to [a-zA-Z0-9_-] for Anthropic's 128-char
    # limit (engine/src/herald/types.py's sanitize_tool_name); Bedrock's
    # limit is shorter (64), so only truncation is needed here, not
    # character substitution. Every tool name in this system's registry
    # (DESIGN.md section 2's primitives, plus patent-office-registered
    # invented tools) is well under 64 characters in practice - this is a
    # defensive bound, not a normal path.
    return name[:BEDROCK_TOOL_NAME_MAX_LEN]


def tools_to_bedrock(tools: "list[dict[str, Any]] | None") -> list[dict[str, Any]]:
    if not tools:
        return []
    out = []
    for tool in tools:
        out.append(
            {
                "toolSpec": {
                    "name": bedrock_tool_name(tool["name"]),
                    "description": tool.get("description", ""),
                    "inputSchema": {"json": tool.get("input_schema", {})},
                }
            }
        )
    return out


def tool_choice_to_bedrock(tool_choice: "dict[str, Any] | None") -> "dict[str, Any] | None":
    if tool_choice is None:
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        return {"auto": {}}
    if choice_type == "any":
        return {"any": {}}
    if choice_type == "tool":
        return {"tool": {"name": bedrock_tool_name(tool_choice["name"])}}
    if choice_type == "none":
        # No Bedrock toolChoice member means "don't force tool use"; the
        # caller (build_converse_request) drops toolConfig entirely for
        # this case, so this branch is only reached defensively.
        return None
    raise InvalidRequestError(f"tool_choice type {choice_type!r} is not supported")


# Anthropic's own default when a thinking block is enabled without a budget.
# Large enough to be a real reasoning pass, small enough that it cannot eat a
# short turn's whole output allowance.
DEFAULT_REASONING_BUDGET_TOKENS = 1024

# Bedrock rejects a reasoning budget at or above maxTokens - the model would
# have no room left to answer. Clamped to this share of the turn's ceiling
# rather than refused, because losing the turn is worse than losing the
# setting.
REASONING_BUDGET_MAX_SHARE = 0.5


def reasoning_config_from_thinking(
    thinking: "dict[str, Any] | None", max_tokens: int
) -> "dict[str, Any] | None":
    """Anthropic's `thinking` parameter as Bedrock's `reasoning_config`.

    Returns None for absent, disabled, or malformed input. Junk is dropped
    rather than forwarded on purpose: Bedrock rejects the entire call on an
    unrecognised model field, so passing a bad value through would turn a
    wrong parameter into a dead turn and a wasted round trip. One
    un-reasoned turn is the cheaper failure.

    Nothing in the engine sends this field today - herald sets `thinking`
    only on tiers this sim never resolves - so this path is dormant until a
    caller asks. It exists because until now it could not have been asked
    for at all, which is what made "the model did not reason" and "we never
    requested reasoning" indistinguishable in every run.
    """
    if not isinstance(thinking, dict):
        return None
    if thinking.get("type") != "enabled":
        return None

    budget = thinking.get("budget_tokens", DEFAULT_REASONING_BUDGET_TOKENS)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        budget = DEFAULT_REASONING_BUDGET_TOKENS

    ceiling = int(max_tokens * REASONING_BUDGET_MAX_SHARE)
    if ceiling < 1:
        return None
    return {"type": "enabled", "budget_tokens": min(budget, ceiling)}


def build_converse_request(
    model: str,
    messages: list[dict[str, Any]],
    system: "str | list[dict[str, Any]] | None",
    max_tokens: int,
    temperature: "float | None" = None,
    top_p: "float | None" = None,
    top_k: "int | None" = None,
    stop_sequences: "list[str] | None" = None,
    tools: "list[dict[str, Any]] | None" = None,
    tool_choice: "dict[str, Any] | None" = None,
    thinking: "dict[str, Any] | None" = None,
    service_tier: "str | None" = None,
) -> dict[str, Any]:
    """Assemble the full bedrock-runtime `converse` / `converse_stream`
    request kwargs from an already-parsed Anthropic Messages request."""
    request: dict[str, Any] = {
        "modelId": model,
        "messages": messages_to_bedrock(messages),
    }

    # `default` is what Bedrock does with no field at all, so ask for
    # nothing: a model that has never seen the parameter keeps working
    # exactly as it did, and the wire stays identical to every call this
    # project has already made.
    if service_tier and service_tier != "default":
        request["serviceTier"] = {"type": service_tier}

    sys_blocks = system_to_bedrock(system)
    if sys_blocks:
        request["system"] = sys_blocks

    inference_config: dict[str, Any] = {"maxTokens": max_tokens}
    if temperature is not None:
        inference_config["temperature"] = temperature
    if top_p is not None:
        inference_config["topP"] = top_p
    if stop_sequences:
        inference_config["stopSequences"] = stop_sequences
    request["inferenceConfig"] = inference_config

    # One dict, two writers. top_k used to assign this key outright; adding
    # reasoning_config as a second assignment would have made whichever ran
    # last delete the other, and the loss would be invisible - a request
    # missing top_k looks exactly like one that never asked for it.
    model_fields: dict[str, Any] = {}
    if top_k is not None:
        # No cross-model InferenceConfig slot for top_k; passed through
        # as a best-effort model-specific field the way Bedrock's own
        # console does for models that support it. Silently ignored by
        # any model that doesn't - not a translation failure either way.
        model_fields["top_k"] = top_k

    reasoning = reasoning_config_from_thinking(thinking, max_tokens)
    if reasoning is not None:
        model_fields["reasoning_config"] = reasoning

    if model_fields:
        request["additionalModelRequestFields"] = model_fields

    if tool_choice is not None and tool_choice.get("type") == "none":
        return request  # tools deliberately omitted: "none" means don't offer any

    bedrock_tools = tools_to_bedrock(tools)
    if not bedrock_tools:
        # Bedrock rejects a request whose MESSAGES carry toolUse/toolResult
        # blocks unless toolConfig is present, even when this turn offers no
        # tools of its own:
        #
        #   ValidationException: The toolConfig field must be defined when
        #   using toolUse and toolResult content blocks.
        #
        # That is exactly what a continuation turn looks like - the assistant
        # called a tool last turn, the result is being handed back, and the
        # caller sends no `tools` because it is not offering a fresh choice.
        # It accounted for EVERY failure in a 1,559-call run: 150 of them, one
        # cause, ~10% of all traffic.
        #
        # The names are recovered from the transcript itself, so this repairs
        # the request rather than inventing capability: a tool Bedrock is told
        # about here is one the model already called, and the schema is left
        # open because the call it describes has already happened and its
        # arguments are in the message.
        bedrock_tools = tools_referenced_in(messages)

    if bedrock_tools:
        tool_config: dict[str, Any] = {"tools": bedrock_tools}
        choice = tool_choice_to_bedrock(tool_choice)
        if choice is not None:
            tool_config["toolChoice"] = choice
        request["toolConfig"] = tool_config

    return request


def tools_referenced_in(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Minimal Bedrock tool specs for every tool named by a `tool_use` block.

    Only used to satisfy Bedrock's validation on a continuation turn - see
    build_converse_request. Returns [] when the transcript names none, which
    leaves the request exactly as it was.
    """
    names: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if name and name not in names:
                    names.append(name)
    return [
        {
            "toolSpec": {
                "name": bedrock_tool_name(name),
                "description": "previously offered; replayed for toolConfig validation",
                "inputSchema": {"json": {"type": "object", "properties": {}}},
            }
        }
        for name in names
    ]


# ── Response: Bedrock converse -> Anthropic (non-streaming) ────────────


def content_block_from_bedrock(block: dict[str, Any]) -> "dict[str, Any] | None":
    if "text" in block:
        return {"type": "text", "text": block["text"]}
    if "toolUse" in block:
        tool_use = block["toolUse"]
        return {
            "type": "tool_use",
            "id": tool_use["toolUseId"],
            "name": tool_use["name"],
            "input": tool_use.get("input", {}),
        }
    if "reasoningContent" in block:
        # The model's own reasoning, carried through instead of dropped.
        #
        # This used to return None on the grounds that nothing requested a
        # reasoning config, so the block would never arrive. That reasoning
        # is circular: the engine's session log subscribes to ThinkingDelta
        # and every session in every run recorded an empty "reasoning" field,
        # which is indistinguishable from a model that did not think and a
        # block that was thrown away here.
        #
        # Bedrock nests the text one level down and names it differently per
        # model, so both shapes are read before giving up.
        reasoning = block["reasoningContent"]
        text = ""
        signature = ""
        if isinstance(reasoning, dict):
            inner = reasoning.get("reasoningText")
            if isinstance(inner, dict):
                text = inner.get("text", "")
                signature = inner.get("signature", "")
            elif isinstance(inner, str):
                text = inner
            else:
                text = reasoning.get("text", "")
        elif isinstance(reasoning, str):
            text = reasoning
        if not text:
            # A redacted or empty block. Nothing to hand on, and inventing
            # an empty thinking block would make "did not think" and "was
            # not allowed to show it" look the same again.
            return None
        return {"type": "thinking", "thinking": text, "signature": signature}

    # Any future Bedrock block kind with no Anthropic equivalent broker's
    # callers read today. Dropped, not failed - a response block herald
    # cannot interpret is worse than a response missing content it never
    # asked for.
    return None


def converse_response_to_anthropic(response: dict[str, Any], model: str) -> dict[str, Any]:
    message = response["output"]["message"]
    content = [
        b for b in (content_block_from_bedrock(item) for item in message.get("content", [])) if b
    ]
    usage = response.get("usage", {})
    return {
        "id": new_bedrock_message_id(),
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": STOP_REASON_BEDROCK_TO_ANTHROPIC.get(response.get("stopReason"), "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def exact_usage_from_converse_response(response: dict[str, Any]) -> tuple[int, int]:
    """(input_tokens, output_tokens), for pricing.py - the one place in
    this module that returns raw ints instead of an Anthropic-shaped
    dict, because broker/service.py needs them for cost math, not for a
    wire response."""
    usage = response.get("usage", {})
    return usage.get("inputTokens", 0), usage.get("outputTokens", 0)


# ── Response: Bedrock converse_stream -> Anthropic SSE (streaming) ─────


def opening_block_for_delta(delta: dict[str, Any]) -> "dict[str, Any] | None":
    """The content_block a bare delta implies, for the synthetic start.

    Returns None for a delta shape with no Anthropic equivalent, in which
    case no start is emitted and the delta itself is skipped: inventing a
    block for something we cannot name would put a shape on the wire that
    no consumer has a rule for.

    A toolUse delta arriving with no start carries neither id nor name
    (Bedrock puts both on contentBlockStart, which is why it always sends
    one for tools). The empty strings here are honest placeholders rather
    than invented values, and this branch exists for completeness: in
    practice Bedrock has never been observed omitting a tool block's start.
    """
    if "text" in delta:
        return {"type": "text", "text": ""}
    if "toolUse" in delta:
        return {"type": "tool_use", "id": "", "name": "", "input": {}}
    if "reasoningContent" in delta:
        reasoning = delta.get("reasoningContent") or {}
        if "redactedContent" in reasoning:
            return {"type": "redacted_thinking", "data": ""}
        return {"type": "thinking", "thinking": ""}
    return None


def reasoning_deltas(reasoning: dict[str, Any]) -> list[dict[str, Any]]:
    """One Bedrock reasoningContent delta, in Anthropic delta shape.

    Three shapes arrive on this field and they are not interchangeable:
    the reasoning text itself, the signature that attests it, and a
    redacted blob standing in for reasoning the provider will not show.
    Collapsing them would make "was not allowed to show it" and "did not
    think" look identical, which is the exact distinction
    content_block_from_bedrock already refuses to blur.

    The spelling matters as much as the content: herald's stream parser
    (engine/src/herald/provider.py) yields a ThinkingDelta only for a delta
    typed `thinking_delta`, and silently skips anything it does not
    recognise. A correctly carried reasoning trace under the wrong type
    name reaches the engine as nothing at all.
    """
    if "redactedContent" in reasoning:
        blob = reasoning["redactedContent"]
        if isinstance(blob, (bytes, bytearray)):
            blob = base64.b64encode(bytes(blob)).decode()
        return [{"delta": {"type": "redacted_thinking_delta", "data": str(blob)}}]
    if "signature" in reasoning:
        return [{"delta": {"type": "signature_delta", "signature": reasoning["signature"]}}]
    text = reasoning.get("text", "")
    if not text:
        return []
    return [{"delta": {"type": "thinking_delta", "thinking": text}}]


async def anthropic_sse_events_from_converse_stream(
    bedrock_events: AsyncIterator[dict[str, Any]],
    model: str,
    estimated_input_tokens: int,
    on_usage: "Callable[[int, int], None] | None" = None,
) -> AsyncIterator[dict[str, Any]]:
    """Translate a Bedrock converse_stream event iterator into the exact
    sequence of Anthropic Messages SSE event dicts herald's
    stream_with_tools reads (engine/src/herald/provider.py lines ~639-723:
    message_start, content_block_start, content_block_delta,
    content_block_stop, message_delta - message_stop is included for
    fidelity with the real API even though herald ignores it, since
    unrecognized event types are silently skipped there).

    `estimated_input_tokens` becomes message_start's usage.input_tokens.
    Bedrock's converse_stream does not report the real input token count
    until the terminal `metadata` event (see tokens.py's module docstring
    for why this is a display estimate, never a billing one); the exact
    count IS available by the time this generator returns - `on_usage`,
    if given, is called exactly once with the real (input_tokens,
    output_tokens) straight off that `metadata` event, which is what
    broker/service.py uses for the ledger debit. It never gets threaded
    back into the already-sent message_start.
    """
    message_id = new_bedrock_message_id()
    block_kind: dict[int, str] = {}
    # INFRA-07. Kimi sometimes writes its own tool-call syntax as plain
    # text instead of sending a toolUse block. herald only acts on
    # tool_use, so those turns were billed in full and changed nothing:
    # Rosalind Vance's nine sessions in run 23 answered the flour question
    # seven times into a void.
    #
    # Buffered across deltas because the markers arrive split - a parser
    # that looked at one delta at a time would see nothing.
    markup = MarkupBuffer()
    repaired_calls = 0
    # Indices this generator invented, so they cannot collide with
    # Bedrock's own. Bedrock numbers from 0 upward; these count down.
    synthetic_index = -1

    yield {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": estimated_input_tokens,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }

    async for event in bedrock_events:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"]
            index = start["contentBlockIndex"]
            tool_use = start.get("start", {}).get("toolUse")
            if tool_use is not None:
                block_kind[index] = "tool_use"
                yield {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_use["toolUseId"],
                        "name": tool_use["name"],
                        "input": {},
                    },
                }
            else:
                block_kind[index] = "text"
                yield {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                }

        elif "contentBlockDelta" in event:
            delta_event = event["contentBlockDelta"]
            index = delta_event["contentBlockIndex"]
            delta = delta_event.get("delta", {})

            # THE ANTHROPIC WIRE GUARANTEES A START BEFORE ANY DELTA, AND
            # BEDROCK DOES NOT.
            #
            # Bedrock sends contentBlockStart for toolUse blocks and
            # frequently omits it for plain text: a text block arrives as a
            # bare contentBlockDelta. This used to `setdefault` the kind and
            # yield the delta alone, producing a stream no Anthropic consumer
            # is obliged to understand. Both of broker's own consumers key
            # off the start event, so text vanished: service.py's
            # StreamAccumulator looked up an index it had never seen and
            # returned, and the trace recorded {"content": [], ...} for a
            # call that had really spoken. Measured before this fix: 938
            # such rows in dryrun.db carrying 360,250 billed output tokens,
            # and 46% of every successful call in kasukabe_l3.db.
            #
            # Emitting the start here rather than making each consumer
            # tolerant is deliberate: the defect was one consumer quietly
            # coping and another quietly not, and a wire contract that holds
            # only when the reader is generous is not a contract.
            if index not in block_kind:
                opening = opening_block_for_delta(delta)
                if opening is not None:
                    block_kind[index] = opening["type"]
                    yield {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": opening,
                    }

            if "text" in delta:
                # INFRA-07: everything that is really text still goes
                # through untouched; only a completed marker span is
                # lifted out of it.
                passthrough, calls = markup.feed(delta["text"])
                if passthrough:
                    yield {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": passthrough},
                    }
                for call in calls:
                    repaired_calls += 1
                    # A real tool_use block, in the three-event shape
                    # herald reads. The id is synthetic because the model
                    # sent none - see synthetic_tool_use_id.
                    yield {
                        "type": "content_block_start",
                        "index": synthetic_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": synthetic_tool_use_id(),
                            "name": call.name,
                            "input": {},
                        },
                    }
                    yield {
                        "type": "content_block_delta",
                        "index": synthetic_index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": call.arguments,
                        },
                    }
                    yield {"type": "content_block_stop", "index": synthetic_index}
                    block_kind[synthetic_index] = "tool_use"
                    synthetic_index -= 1
            elif "toolUse" in delta:
                yield {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": delta["toolUse"].get("input", ""),
                    },
                }
            elif "reasoningContent" in delta:
                # The streaming half of what content_block_from_bedrock has
                # done on the non-streaming path all along. Its absence here
                # is why session_log's `reasoning` field has been empty in
                # every run this project has ever made: the sim streams, so
                # this was the only path that mattered and it had no branch.
                reasoning = delta.get("reasoningContent") or {}
                for translated in reasoning_deltas(reasoning):
                    yield {"type": "content_block_delta", "index": index, **translated}

        elif "contentBlockStop" in event:
            index = event["contentBlockStop"]["contentBlockIndex"]
            # Suppressed for an index that never opened. A stop for a block
            # nobody was told about is the same contract violation as a
            # delta for one, and forwarding it only gives a consumer
            # something else to guess at.
            if index in block_kind:
                yield {"type": "content_block_stop", "index": index}

        elif "messageStop" in event:
            stop_reason = STOP_REASON_BEDROCK_TO_ANTHROPIC.get(
                event["messageStop"].get("stopReason"), "end_turn"
            )
            # INFRA-07: anything left in the buffer was never a complete
            # call, so it goes back as the text it arrived as. Emitted
            # before the stop, because after it no consumer is listening.
            leftover = markup.flush()
            if leftover:
                yield {
                    "type": "content_block_delta",
                    "index": max(block_kind, default=0),
                    "delta": {"type": "text_delta", "text": leftover},
                }
            # A turn that made a tool call ended because it made one, and
            # herald routes on this field. Bedrock says "end_turn" because
            # as far as IT is concerned the model only wrote prose.
            if repaired_calls and stop_reason == "end_turn":
                stop_reason = "tool_use"
            yield {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 0},
            }

        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            if on_usage is not None:
                on_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0))
            yield {
                "type": "message_delta",
                "delta": {},
                "usage": {"output_tokens": usage.get("outputTokens", 0)},
            }

    yield {"type": "message_stop"}


def sse_line(event: dict[str, Any]) -> str:
    """One SSE frame: `event: <type>` + `data: <json>`, blank-line
    terminated - the shape engine/src/herald/transport.py's iter_sse
    expects (it reads the `data:` line and ignores `event:`, matching
    Anthropic's own real wire format, which pairs both lines)."""
    event_type = event["type"]
    return f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
