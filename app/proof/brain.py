"""A brain the town can be driven by, and a brain that behaves like
somebody else's model.

WHERE THIS SITS. `broker/bedrock.py:84` defines `BedrockGateway` as a
Protocol, and `lanes.py` injects one per lane. That is the only seam in
this repository where the model can be replaced and *nothing else is*:
below it sit `translate.py`'s event synthesis, `kimi_tool_calls.py`'s
markup recovery, herald's stream parser, the seam's three wrappers, the
registry, the ledger, the charter and the trace. A fake placed any higher
would skip exactly the machinery most likely to break when the model
changes.

So: only the brain is fake. The ledger is the real ledger, the trace is a
real database on disk, and the assertions are made against rows those
wrote.

WHY NOT `MockBedrockGateway`. Two reasons, both load-bearing.

  1. It is reached through `BrokerSettings.mock_mode`, which also swaps in
     `InMemoryLedgerClient` and `InMemoryTraceSink`. An evaluation whose
     ledger and trace are fakes can prove only that the fakes agree with
     each other. That is the shape the review named as cause 3 - "an
     in-memory database that logs success".
  2. It is a *friendly* wire. Its own comment says so: it used to always
     send `contentBlockStart`, real Bedrock often omits it for text, and
     "that is how defect D1 survived fifteen runs: 938 calls recorded with
     zero content and 360,250 output tokens billed against them, while the
     suite was green throughout."

This brain therefore defaults to the *unfriendly* shape - no
`contentBlockStart` for text - because that is what production receives.

WHAT THE ADVERSARIAL HALF IS FOR. The town is about to be driven by a
different, more capable model. Every behavioural fix on record was
measured on Kimi K2.5, and the call path is full of single-provider bets:

  - `herald/provider.py:680` parses accumulated tool JSON with an
    unguarded `json.loads`; 41 turns across 33 of 50 residents died of it.
  - `provider.py:624-626` keeps the open tool call in three *scalars* and
    never reads `event["index"]`. A provider that interleaves content
    blocks concatenates two arguments into one corrupted string; a
    provider that omits `contentBlockStart` for a tool block has the call
    silently dropped, with no error and no trace.
  - `STOP_MAP` maps three stop reasons and sends everything else to
    `END_TURN`. `StopReason.REFUSAL` exists and is unreachable, so a
    content-filtered turn arrives looking like an ordinary finished one.

None of those can be found by running Kimi again. They are found by
handing the same real system a stream that a different model would
plausibly produce, and asking whether the town survives it.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# One act the brain intends: a verb and its arguments.
Act = tuple[str, dict[str, Any]]

# What a plan may return for a turn: acts, a line of speech-free text, or
# nothing at all (a turn that calls no tool - 34% of real cognition turns).
Intent = Sequence[Act] | str | None

_WHO = re.compile(r"You are ([^,.\n]+)")


@dataclass
class Turn:
    """What the brain can see, which is exactly what the model can see."""

    request: dict
    index: int

    @property
    def system_text(self) -> str:
        return "\n".join(
            b.get("text", "") for b in self.request.get("system", []) if "text" in b
        )

    @property
    def perception(self) -> str:
        """The last thing the resident was told."""
        for message in reversed(self.request.get("messages", [])):
            if message.get("role") != "user":
                continue
            text = "\n".join(
                b.get("text", "") for b in message.get("content", []) if "text" in b
            )
            if text.strip():
                return text
        return ""

    @property
    def who(self) -> str:
        """The resident's name, read the way the model reads it."""
        for source in (self.perception, self.system_text):
            m = _WHO.search(source)
            if m:
                return m.group(1).strip()
        return ""

    @property
    def tools(self) -> tuple[str, ...]:
        cfg = self.request.get("toolConfig") or {}
        return tuple(
            t["toolSpec"]["name"]
            for t in cfg.get("tools", [])
            if isinstance(t, dict) and "toolSpec" in t
        )

    def offers(self, name: str) -> bool:
        """Was this verb actually on the wire this turn?

        The difference between "the resident did not call it" and "the
        resident was never given it" is the whole GOV-3 finding, and a
        story that asserts the first while the second is true proves
        nothing.
        """
        return any(t == name or t.endswith(f"_{name}") or t.endswith(f":{name}") for t in self.tools)


# ---------------------------------------------------------------------------
# the well-formed stream
# ---------------------------------------------------------------------------


def events_for(intent: Intent, *, text_block_start: bool = False) -> list[dict]:
    """Botocore-shaped `converse_stream` events for one turn.

    `text_block_start` defaults to False on purpose - see the module
    docstring. Real Bedrock usually omits it for text, and the friendly
    shape is a wire production never sees.
    """
    events: list[dict] = [{"messageStart": {"role": "assistant"}}]
    index = 0

    def text_block(body: str) -> None:
        nonlocal index
        if text_block_start:
            events.append(
                {"contentBlockStart": {"contentBlockIndex": index, "start": {}}}
            )
        events.append(
            {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"text": body}}}
        )
        events.append({"contentBlockStop": {"contentBlockIndex": index}})
        index += 1

    if isinstance(intent, str):
        text_block(intent)
        stop = "end_turn"
    elif not intent:
        stop = "end_turn"
    else:
        for n, (tool, args) in enumerate(intent):
            events.append(
                {
                    "contentBlockStart": {
                        "contentBlockIndex": index,
                        "start": {
                            "toolUse": {
                                "toolUseId": f"tooluse_proof_{n}_{index}",
                                "name": tool,
                            }
                        },
                    }
                }
            )
            events.append(
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": index,
                        "delta": {"toolUse": {"input": json.dumps(args)}},
                    }
                }
            )
            events.append({"contentBlockStop": {"contentBlockIndex": index}})
            index += 1
        stop = "tool_use"

    events.append({"messageStop": {"stopReason": stop}})
    events.append(
        {
            "metadata": {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 40,
                    "totalTokens": 1040,
                },
                "metrics": {"latencyMs": 1},
            }
        }
    )
    return events


def response_for(intent: Intent) -> dict:
    """The non-streaming shape, for the few calls that use `converse`."""
    content: list[dict] = []
    stop = "end_turn"
    if isinstance(intent, str):
        content.append({"text": intent})
    elif intent:
        stop = "tool_use"
        for n, (tool, args) in enumerate(intent):
            content.append(
                {
                    "toolUse": {
                        "toolUseId": f"tooluse_proof_{n}",
                        "name": tool,
                        "input": args,
                    }
                }
            )
    if not content:
        content.append({"text": ""})
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 1000, "outputTokens": 40, "totalTokens": 1040},
        "metrics": {"latencyMs": 1},
    }


# ---------------------------------------------------------------------------
# the brain
# ---------------------------------------------------------------------------


@dataclass
class ScriptedBrain:
    """A `BedrockGateway` whose answers come from a plan instead of a model.

    `plan(turn) -> Intent`. The plan sees what the model sees, so a story
    can say "when this resident is told there is a buyer here, post a
    price" without the harness having to know anything the resident does
    not.

    `mutate`, if given, rewrites the event list on its way out. That is
    where a different model's stream shape comes from - see `hazards`.
    """

    plan: Callable[[Turn], Intent]
    mutate: Callable[[list[dict]], list[dict]] | None = None
    text_block_start: bool = False
    turns: list[Turn] = field(default_factory=list)
    intents: list[Intent] = field(default_factory=list)

    def _next(self, request: dict) -> tuple[Turn, Intent]:
        turn = Turn(request=request, index=len(self.turns))
        self.turns.append(turn)
        intent = self.plan(turn)
        self.intents.append(intent)
        return turn, intent

    async def converse(self, request: dict) -> dict:
        _turn, intent = self._next(request)
        await asyncio.sleep(0)
        return response_for(intent)

    async def converse_stream(self, request: dict) -> AsyncIterator[dict]:
        _turn, intent = self._next(request)
        await asyncio.sleep(0)
        events = events_for(intent, text_block_start=self.text_block_start)
        if self.mutate is not None:
            events = self.mutate(events)
        for event in events:
            yield event

    # -- what a story asks afterwards ------------------------------------

    def acts(self) -> list[Act]:
        out: list[Act] = []
        for intent in self.intents:
            if intent and not isinstance(intent, str):
                out.extend(intent)
        return out

    def asked_for(self, tool: str) -> int:
        return sum(1 for name, _ in self.acts() if name.endswith(tool))


def always(intent: Intent) -> Callable[[Turn], Intent]:
    """The simplest plan: the same thing every turn."""
    return lambda _turn: intent


def in_order(intents: Iterable[Intent]) -> Callable[[Turn], Intent]:
    """One intent per turn, then silence."""
    script = list(intents)
    return lambda turn: script[turn.index] if turn.index < len(script) else None
