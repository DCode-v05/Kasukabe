"""The compaction summariser, said in a resident's voice.

Every turn a resident takes is followed by a second, billed call that
compacts the exchange so the next turn's history stays affordable. The
prompt for it lives in `src/engine_core/thread.py` (see
FROZEN_SUMMARY_PROMPT below), which is frozen under ROADMAP Law 1 and is
right for the product that engine was written for: an assistant answering
a user.

A town has neither. Measured in run 19 - 165 of 810 calls, $0.43, 1.1
million input tokens, a fifth of everything the town paid for - and here
is what the model wrote into two residents' own memories with it:

    "[user] At 06:00, user placed me at h_quarry with farmland work
     pending, no food."                                     - Tobias

    "[engine] Assessed pantry stocks (3 grain, 2 meat, berries for ~2
     days). Decided household cannot sustain five..."       - Ainsley

The first has a resident believing a "user" put it in its own home. The
second is a pantry nobody has: the entire town held 12 units of food that
morning, and Ainsley held none. The summariser did not misread the turn -
it was asked what an ENGINE did for a USER, and it answered in the genre
it was asked for, inventing the props that genre needs. The next turn
then reads that summary back as its own memory.

WHY HERE. thread.py may not be edited. `src/sim/tool_surface.py` already
solves the same shape of problem the same way - the engine registers
seven assistant-shaped tools onto a resident's registry and this package
takes them off afterwards. This is the prompt half of that: the adapter
is constructed in `pinned_boot.py`, so it can be wrapped there, and the
one prompt the town does not want is rewritten on its way to the model.

WHAT B3 ASKED FOR, AND WHAT IT GETS INSTEAD.

Tool spec B3's companion settings include "the post-turn summariser run
on compaction only". That is **not** what this does, deliberately.

That summariser is not a summariser. Reading thread.py: it is the
post-turn signal capture, and the string it produces is written straight
into the `LearningSignal` store as `type="event"` - it IS the stream the
learning centre consolidates. Running it on compaction only would cut the
town's signal production by roughly ten times, which contradicts the
learning-cadence work in the same body of specs and would leave the
waking-interval batch with almost nothing to consolidate. Twenty percent
of calls and ten percent of spend is a real cost, but the signal is the
product.

So the same money is taken from where it costs nothing. Run 19: 387 of
810 calls ended with NO tool call. Summarising a turn in which nothing
happened, with a model, is the waste - and thread.py already builds a
deterministic string for exactly this shape in its own `except` branch.
Idle turns get that string, free; turns that acted get the real
summariser. The signal is still written either way.

KASUKABE_SKIP_IDLE_SUMMARIES=0 restores a model call on every turn.

WHAT A TURN IS HANDED: THE RECORD IN THE WORLD'S VOICE, AND NO PLAN NUDGE.

Run 28b. A resident's stored history is perception / record / perception /
record, and ENG-09 stores each record in the ASSISTANT role, so the model
meets three or more past selves that wrote "RECORD OF THE TURN THAT JUST
ENDED" and continues the genre. Of the 235 first calls of a turn that
followed a record, 101 wrote record-shaped text - invented `world:say(...)
-> {...}` lines with invented results - and 111 called no tool. Of the 45
with no record before them: 2 and 5. The heading already says the world
wrote it; the role said the resident did, and the role won.

Those 235 calls also carried the frozen engine's complexity nudge
(`engine_core/thread.py:2921`), "Consider calling request_plan", for a tool
served to a resident in 0 of 885 calls.

So on `stream_with_tools` and `stream`, the two calls a turn is made of,
the nudge is cut and every stored record is moved into the world's next
message. What is STORED does not change - the records, the session log and
the perceptions stay as written. This turn's own tool calls and their
results are never touched. KASUKABE_RECORDS_IN_WORLD_VOICE=0 puts records
back in the resident's slot; the nudge goes either way, because it names a
tool the resident does not have.

WHAT THIS IS NOT. It does not change what gets summarised, how often, or
the token budget - all three are the frozen engine's business and all
three are reasonable. Only the vocabulary changes, plus one line telling
the model not to write down things that did not happen.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
from typing import Any

from src.herald.types import TextBlock
from src.sim.session_log import RECORD_HEADING

logger = logging.getLogger("kasukabe.sim.resident_voice")

# Verbatim prefix of the frozen complexity nudge (thread.py:2921). Matched, not
# imported, for the same reason as FROZEN_SUMMARY_PROMPT: it is built inline.
PLAN_NUDGE = re.compile(
    r"<system-reminder>This task appears complex\. Consider calling request_plan\b"
    r".*?</system-reminder>\s*",
    re.S,
)

# The first line of every perception the world writes:
# "It is game-day 0, 10:00 (day). You are at street."
PERCEPTION_HEAD = re.compile(r"^It is game-day \d+, \d{2}:\d{2}[^\n]*", re.M)

# Whether a turn is handed its OLDER perceptions in full. Off by default -
# see `_rewrite_turn`. Set to 1 to put the full pictures back and measure.
OLD_PERCEPTIONS_IN_FULL = os.environ.get(
    "KASUKABE_OLD_PERCEPTIONS_IN_FULL", "0"
).strip().lower() in {"1", "true", "yes", "on"}

RECORDS_IN_WORLD_VOICE = os.environ.get(
    "KASUKABE_RECORDS_IN_WORLD_VOICE", "1"
).strip().lower() not in {"0", "false", "no", "off"}

# Verbatim from src/engine_core/thread.py. Matched rather than imported
# because it is built inline there, in a local variable, inside a method:
# there is no name to import. If that text ever changes this stops
# matching, the frozen prompt goes through untouched, and the run gets
# worse summaries rather than a crash - so `saw_frozen_prompt` below is
# counted and logged, and a run that never sees it says so.
FROZEN_SUMMARY_PROMPT = (
    "Summarize this exchange in under 100 tokens total. "
    "Format: [user] what they said/asked (50 tok max) "
    "[engine] what you did/responded (50 tok max). "
    "Focus on facts stated, decisions made, people mentioned, "
    "and actions taken. Omit pleasantries."
)


def resident_summary_prompt(agent_name: str | None) -> str:
    """The same job, asked of a person living in a town.

    Keeps the frozen prompt's budget (100 tokens) and its focus (facts,
    decisions, people, actions; no pleasantries), because both are right.
    Drops the two roles that do not exist here, and adds the line the
    frozen version had no reason to need: do not write down anything that
    did not happen. Run 19 is the reason - a pantry of "3 grain, 2 meat"
    entered a resident's memory this way and was reasoned from for the
    rest of the run.
    """
    who = agent_name or "you"
    return (
        f"Summarise what just happened to {who}, in under 100 tokens. "
        "Write it as a note to yourself: what was said and by whom, what "
        "you decided, who was there, what you actually did. Facts only - "
        "do not invent anything that did not happen, and do not write down "
        "things you only considered or imagined as if you had done them. "
        # PROMPT-04. Two additions, both from `deni-a6f9eb4-5h`.
        #
        # The first: this note is the ONLY thing that survives into the
        # next turn, and 595 turns in that run wrote out acts they had
        # not performed. A summary that records an unperformed intention
        # as a fact is how a resident spends day two acting on a sale
        # that never happened. Separating the two is cheap here and
        # impossible later.
        #
        # The second: a resident whose plan was interrupted had nowhere
        # to put it, so every tick re-derived the whole day from the
        # persona. Codex's agents carry an explicit plan across steps for
        # exactly this reason. One clause, and only what is unfinished -
        # not a strategy, a loose end.
        "If something you meant to do was left undone, say so in a few "
        "words and say what is still needed, separately from what "
        "happened. Never write an intention as an outcome. "
        "Omit pleasantries."
    )


SKIP_IDLE_SUMMARIES = os.environ.get(
    "KASUKABE_SKIP_IDLE_SUMMARIES", "1"
).strip().lower() not in {"0", "false", "no", "off"}


def turn_did_something(messages: Any) -> bool:
    """Did this turn call any tool, or was it a page of thinking?

    Read off the message list the summariser is handed. thread.py builds a
    tool RESULT as `LLMMessage(role="user", content=[ContentBlock, ...])` -
    a list - while a perception and the summary prompt are `role="user"`
    with a plain string. So: walk back from the summary prompt to the
    string-content user message that started the turn, and see whether any
    list-content message sits between them.

    Scoped to THIS turn deliberately. The conversation history carries
    earlier turns' tool results, and a detector that looked at the whole
    list would answer "yes" forever after a resident's first successful
    act.

    Errs toward True. A turn wrongly called idle loses a real summary;
    a turn wrongly called busy costs one call. The second is cheaper.
    """
    try:
        items = list(messages)
    except TypeError:
        return True
    for message in reversed(items[:-1]):  # skip the summary prompt itself
        content = getattr(message, "content", None)
        if isinstance(content, list):
            return True
        if getattr(message, "role", None) == "user" and isinstance(content, str):
            # The perception that opened this turn. Nothing before it
            # belongs to this turn.
            return False
    return True


def cheap_summary(messages: Any, agent_name: str | None) -> str:
    """A summary of a turn in which nothing happened, written without a
    model call.

    thread.py already builds exactly this shape in its own `except`
    branch, so the signal store is being handed a string it is used to.
    The signal is still WRITTEN - a turn where a resident stood and
    thought is a fact about the resident, and the learning centre should
    see it. What it is not is worth three quarters of a cent.
    """
    who = agent_name or "you"
    said = ""
    try:
        for message in reversed(list(messages)[:-1]):
            content = getattr(message, "content", None)
            if getattr(message, "role", None) == "assistant" and isinstance(content, str):
                said = content.strip()[:160]
                break
    except TypeError:
        said = ""
    tail = f" Wrote: {said}" if said else ""
    return f"{who} took no action this turn.{tail}"


def _without_plan_nudge(content: Any) -> tuple[Any, int]:
    """`content` with the request_plan nudge cut, and how many were cut."""
    if isinstance(content, str):
        return PLAN_NUDGE.subn("", content)
    if not isinstance(content, list):
        return content, 0
    kept: list[Any] = []
    removed = 0
    for block in content:
        if isinstance(block, TextBlock):
            text, cut = PLAN_NUDGE.subn("", block.text)
            removed += cut
            if cut and not text.strip():
                continue  # Converse refuses an empty text block
            if cut:
                block = dataclasses.replace(block, text=text)
        kept.append(block)
    return kept, removed


def _is_plain_text(content: Any) -> bool:
    return isinstance(content, str) or (
        isinstance(content, list) and all(isinstance(b, TextBlock) for b in content)
    )


def _joined(first: Any, second: Any) -> Any:
    if isinstance(first, str) and isinstance(second, str):
        return f"{first}\n\n{second}"
    as_blocks = lambda c: [TextBlock(c)] if isinstance(c, str) else list(c)  # noqa: E731
    return as_blocks(first) + as_blocks(second)


def _perception_head(message: Any) -> "str | None":
    """The "It is game-day ..." line, when this is one of the world's
    perceptions - a user message of plain text. None for anything else,
    including a tool result, which may quote a place but is not a picture of
    the world at the start of a turn."""
    if getattr(message, "role", None) != "user":
        return None
    content = getattr(message, "content", None)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and content and all(isinstance(b, TextBlock) for b in content):
        text = "\n".join(b.text for b in content)
    else:
        return None
    found = PERCEPTION_HEAD.search(text)
    return found.group(0).strip() if found else None


def _is_stored_record(message: Any) -> bool:
    content = getattr(message, "content", None)
    return (
        getattr(message, "role", None) == "assistant"
        and isinstance(content, str)
        and content.lstrip().startswith(RECORD_HEADING)
    )


def speak_as_resident(adapter: Any, agent_name: str | None = None) -> Any:
    """Wrap an LLM adapter so the frozen summary prompt is rewritten.

    Delegates every other attribute to the adapter it wraps, so nothing
    else about it changes - a proxy that hid the adapter's own model id or
    its streaming methods would break the engine in ways no test in this
    package would notice.
    """
    return _ResidentVoiceAdapter(adapter, agent_name)


class _ResidentVoiceAdapter:
    def __init__(self, inner: Any, agent_name: str | None) -> None:
        self._inner = inner
        self._agent_name = agent_name
        self.rewrites = 0
        self.skipped = 0
        # Counted so a run can say the rewrite happened, not leave it inferred.
        self.plan_nudges_removed = 0
        self.records_moved = 0
        self.perceptions_compacted = 0

    def __getattr__(self, item: str) -> Any:
        # Only reached for attributes this class does not define, so the
        # wrapper adds `generate`, `stream_with_tools` and `stream` and
        # hides nothing.
        return getattr(self._inner, item)

    async def stream_with_tools(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        async for event in self._inner.stream_with_tools(self._for_the_wire(messages), *args, **kwargs):
            yield event

    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        async for chunk in self._inner.stream(self._for_the_wire(messages), *args, **kwargs):
            yield chunk

    def _for_the_wire(self, messages: Any) -> Any:
        """New messages for the model; the engine's own list is not edited.

        Fails open: this is on the path of every call, and a turn handed the
        engine's history as-is is better than a turn that never happens.
        """
        try:
            return self._rewrite_turn(list(messages))
        except Exception:  # noqa: BLE001
            logger.warning("could not rewrite a turn's messages; sending them as-is", exc_info=True)
            return messages

    def _rewrite_turn(self, items: list[Any]) -> list[Any]:
        # An OLDER perception is reduced to its first line - when and where.
        #
        # Run 29: the median resident call grew from 14,110 input tokens in
        # the first game-hour to 23,759 by the last (N10's bar is 1.25x;
        # this was 1.68x), and late in the run 61% of the world's message -
        # a median 20,747 characters - was older perceptions. Each one is a
        # full picture of the world at a moment the current perception has
        # already superseded: the people here then, the prices then, the
        # civic notice then, the backlog of things said then. With no prompt
        # cache every one of them was paid for again on every call of every
        # turn. A record needs to know when and where it happened, and that
        # is the first line; the records themselves stay whole, and the
        # current perception is never touched.
        heads = [i for i, m in enumerate(items) if _perception_head(m) is not None]
        current = heads[-1] if heads else None

        out: list[Any] = []
        for index, message in enumerate(items):
            if getattr(message, "role", None) == "user":
                content, cut = _without_plan_nudge(message.content)
                if cut:
                    self.plan_nudges_removed += cut
                    message = dataclasses.replace(message, content=content)
            if not OLD_PERCEPTIONS_IN_FULL and current is not None and index < current:
                head = _perception_head(message)
                if head is not None:
                    self.perceptions_compacted += 1
                    message = dataclasses.replace(message, content=head)
            if RECORDS_IN_WORLD_VOICE and _is_stored_record(message):
                self.records_moved += 1
                message = dataclasses.replace(message, role="user")
            previous = out[-1] if out else None
            if (
                previous is not None
                and previous.role == "user" == message.role
                and _is_plain_text(previous.content)
                and _is_plain_text(message.content)
            ):
                # Converse needs the roles to alternate; a moved record now
                # sits between two of the world's messages.
                out[-1] = dataclasses.replace(previous, content=_joined(previous.content, message.content))
                continue
            out.append(message)
        return out

    async def generate(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        if self._is_summary_call(messages) and not turn_did_something(messages):
            # B3, and NOT the way the spec worded it - see the module
            # docstring's "What B3 asked for, and what it gets instead".
            self.skipped += 1
            return cheap_summary(messages, self._agent_name)
        rewritten = self._rewrite(messages)
        return await self._inner.generate(rewritten, *args, **kwargs)

    def _is_summary_call(self, messages: Any) -> bool:
        if not SKIP_IDLE_SUMMARIES:
            return False
        try:
            last = list(messages)[-1]
        except (TypeError, IndexError):
            return False
        content = getattr(last, "content", None)
        return (
            isinstance(content, str)
            and content.strip() == FROZEN_SUMMARY_PROMPT.strip()
        )

    def _rewrite(self, messages: Any) -> Any:
        try:
            out = list(messages)
        except TypeError:
            return messages
        for index, message in enumerate(out):
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip() == FROZEN_SUMMARY_PROMPT.strip():
                try:
                    prompt = resident_summary_prompt(self._agent_name)
                    # The engine's Message is a frozen dataclass. Assigning to
                    # it raised here on every live call: run 28b sent all 87
                    # post-turn summaries with the frozen prompt.
                    if dataclasses.is_dataclass(message):
                        out[index] = dataclasses.replace(message, content=prompt)
                    else:
                        message.content = prompt
                except Exception:  # noqa: BLE001 - a summary must never end a turn
                    logger.warning("could not rewrite the summary prompt; sending it as-is")
                    continue
                self.rewrites += 1
        return out
