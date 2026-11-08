"""Kimi's native tool-call markup, recognised where it can still be acted on.

`INFRA-07`. Kimi K2.5 sometimes emits its own tool-call syntax as PLAIN
TEXT instead of as a Bedrock `toolUse` block:

    <|tool_call_begin|> functions.world_move:0 <|tool_call_argument_begin|>
    {"destination_location_id": "Kasukabe Bakery"} <|tool_call_end|>

`herald` only acts on `tool_use` blocks, so a turn shaped like this is
billed in full, produces a long deliberation, and changes nothing in the
world. Rosalind Vance's nine sessions in run 23 went this way: she
answered the flour question seven times into a void, and the one question
that stalled the town's bread supply was answered and never delivered.

WHY THE BROKER. The parsing cannot live in the engine: `herald/provider.py`
is frozen under ROADMAP Law 1, and by the time a `StreamEvent` reaches
`engine/src/sim/` herald has already classified these deltas as text. The
broker already rewrites this stream - it synthesises the
`content_block_start` events Bedrock omits (`translate.py`) - so it is
the one place that sees the raw deltas and is allowed to change them.

WHAT THIS IS NOT. It is not a general parser for model output, and it
does not guess. A marker sequence that does not complete, or whose
arguments are not valid JSON, is left exactly as the text it arrived as:
a turn that did nothing is bad, and a turn that did SOMETHING ELSE is
worse. The raw text is kept in the trace either way, so the repair is
always visible rather than silently rewriting history.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

# The markers, exactly as they appear in run 23's session logs. Both
# spellings are real: `tool_call_*` is the common one and
# `function_call_*` appears in the same corpus, so recognising only the
# first would leave the second unparsed for the same reason as before.
CALL_BEGIN = ("<|tool_call_begin|>", "<|function_call_begin|>")
ARGUMENT_BEGIN = ("<|tool_call_argument_begin|>",)
CALL_END = ("<|tool_call_end|>", "<|function_call_end|>")
SECTION = ("<|tool_calls_section_begin|>", "<|tool_calls_section_end|>")

ALL_MARKERS = CALL_BEGIN + ARGUMENT_BEGIN + CALL_END + SECTION

# "functions.world_move:0" -> world_move. The index after the colon is
# Kimi's own ordinal for the call within the turn; it is not a tool name
# and is deliberately dropped.
_NAME = re.compile(r"(?:functions\.)?([A-Za-z_][\w.-]*?)(?::\d+)?\s*$")


# The longest trailing token held back on the chance it is a tool name
# split across deltas (ENG-08). Long enough for any name the town has,
# short enough that a run-on sentence is never mistaken for one.
MAX_NAME_TAIL = 64


def looks_like_markup(text: str) -> bool:
    """Cheap enough to run on every text delta of every call."""
    return any(marker in text for marker in ALL_MARKERS)


@dataclass
class ParsedCall:
    name: str
    arguments: str          # raw JSON text, as the model wrote it
    raw: str                # the whole marker span, for the trace

    def input(self) -> dict:
        return json.loads(self.arguments)


@dataclass
class MarkupBuffer:
    """Accumulates text deltas and yields calls as they complete.

    Stateful because the markers arrive SPLIT ACROSS DELTAS - run 23 shows
    `<|tool_call_begin|>` and its arguments in different chunks - so a
    parser that examined one delta at a time would see nothing at all.

    Bounded: a stream that opens a marker and never closes it must not
    grow this buffer for the rest of the call. Past the limit the buffer
    gives up and hands its text back, which reproduces exactly the old
    behaviour for that one call rather than inventing anything.
    """

    limit_chars: int = 64_000
    pending: str = ""
    overflowed: bool = False
    # ENG-08. Whether this stream has shown Kimi markup at all.
    #
    # Holding back a trailing name-shaped token (see
    # `_partial_marker_tail`) is what lets a bare-marker call survive a
    # split mid-name. Doing it on EVERY stream defers the last word of
    # every ordinary answer to `flush`, which changed the delta shape of
    # seven unrelated broker tests - a large price for a narrow repair.
    #
    # A turn writing this markup always shows a marker somewhere, so the
    # holding starts the moment one is seen and ordinary prose streams
    # are untouched. The residue is honest and worth stating: a bare
    # name that splits mid-token AND is the first markup in the stream
    # is still missed. Every span ENG-08 quotes carries a
    # `<|tool_calls_section_*|>` marker, so none of them is that case.
    saw_markup: bool = False
    # Whether the last text this buffer handed on ended part-way through
    # a word. Used only to tell a name that sits at the front of
    # `pending` because the stream starts there from one that sits there
    # because the front of the token is already gone - see `_call_start`.
    released_mid_token: bool = False
    _seen: list[ParsedCall] = field(default_factory=list)

    def feed(self, text: str) -> tuple[str, list[ParsedCall]]:
        """Take one text delta. Returns (text to pass through, calls found).

        Text before an opening marker is passed straight through - a
        resident's actual speech must not be swallowed because a tool call
        followed it in the same block.
        """
        if self.overflowed:
            return text, []

        self.pending += text
        if not self.saw_markup and looks_like_markup(self.pending):
            self.saw_markup = True
        passthrough = ""
        found: list[ParsedCall] = []

        while True:
            start = self._call_start(self.pending)
            if start is None:
                # No marker open. Everything except a possible partial
                # marker at the tail can be released.
                keep = self._partial_marker_tail(self.pending)
                passthrough += self.pending[: len(self.pending) - keep]
                self.pending = self.pending[len(self.pending) - keep:]
                break

            passthrough += self.pending[:start]
            self.pending = self.pending[start:]

            call, consumed = self._take_one(self.pending)
            if call is None:
                if len(self.pending) > self.limit_chars:
                    # Never closed. Give the text back rather than hold it.
                    self.overflowed = True
                    passthrough += self.pending
                    self.pending = ""
                break
            found.append(call)
            self._seen.append(call)
            self.pending = self.pending[consumed:]

        if passthrough:
            self.released_mid_token = passthrough[-1] not in " \t\n>"
        return passthrough, found

    def flush(self) -> str:
        """Whatever is still held when the stream ends.

        An unterminated marker span is returned as text. It was text when
        it arrived and no call can be made from it, so passing it through
        keeps the resident's own words rather than deleting them.
        """
        remaining, self.pending = self.pending, ""
        return remaining

    # ── internals ──────────────────────────────────────────────────────

    def _call_start(self, text: str) -> "int | None":
        """Where the next call span begins, opening marker or not.

        ENG-08 - the same defect INFRA-07 found, with one shape INFRA-07
        did not cover. Its evidence from run 23 is:

            world_say:0 <|tool_call_argument_begin|> {"content": "..."}
            <|tool_call_end|> <|tool_calls_section_end|>

        There is NO `<|tool_call_begin|>`. Kimi drops it and writes the
        sanitised name bare, and a parser keyed on the opening marker
        saw nothing - so the exact turns ENG-08 quotes were still lost
        after INFRA-07 shipped, which is why two reviewers found the same
        symptom and only one of them was closed.

        The name is then whatever token sits immediately before
        `<|tool_call_argument_begin|>`, which is the only thing it can
        be: `_NAME` still has to match it, and a span whose arguments
        are not valid JSON is left as text either way.
        """
        opened = self._first_index(text, CALL_BEGIN)
        arg_at = self._first_index(text, ARGUMENT_BEGIN)
        if arg_at is None:
            return opened
        if opened is not None and opened < arg_at:
            return opened
        # A bare-name span. Walk back over the name to where it starts,
        # so `_take_one` receives the whole thing.
        head = text[:arg_at].rstrip()
        cut = max(head.rfind(" "), head.rfind("\n"), head.rfind("\t"), head.rfind(">"))
        start = cut + 1
        candidate = text[start:arg_at].strip()
        if not candidate or _NAME.match(candidate) is None:
            return opened
        if start == 0 and self.released_mid_token:
            # The name touches the front of the buffer AND text has
            # already been released, so the front of the token may be
            # gone: "world_" released, "say:0 <|...|>" left. Taking it
            # would call a tool named `say` when the model asked for
            # `world_say`, and a turn that did SOMETHING ELSE is worse
            # than a turn that did nothing - this module's own rule.
            return opened
        return start

    @staticmethod
    def _first_index(text: str, markers: tuple[str, ...]) -> "int | None":
        found = [text.index(m) for m in markers if m in text]
        return min(found) if found else None

    def _partial_marker_tail(self, text: str) -> int:
        """How many trailing characters must be held back for now.

        Two things can straddle a delta boundary and both would be lost
        if released:

        A MARKER. Releasing half of `<|tool_call_begin|>` emits it as
        speech and then fails to recognise the other half. This was the
        original reason the buffer exists.

        A BARE NAME (ENG-08). When Kimi omits the opening marker the tool
        name is ordinary-looking text - `world_say:0` - and run 23 shows
        it splitting mid-token: "world_" in one delta and "say:0
        <|tool_call_" in the next. Released, it becomes speech and the
        argument marker that follows has nothing in front of it.

        So a trailing token that could BE a name is held too. Bounded to
        `MAX_NAME_TAIL` characters, and it costs at most one word of
        latency in the passthrough stream - `flush` releases it when the
        stream ends, so nothing is ever dropped.
        """
        kept = 0
        for size in range(min(len(text), 30), 0, -1):
            if any(m.startswith(text[-size:]) for m in ALL_MARKERS):
                kept = size
                break

        if not self.saw_markup:
            return kept

        # And, in front of whatever that was, one trailing word that
        # could be the name of a bare-marker call.
        #
        # Both, not either: run 23's split is "world_" then "say:0
        # <|tool_call_", so at the second delta the tail IS a partial
        # marker and the name sits just before it. Holding only the
        # marker releases the name as speech, and the argument marker
        # that follows then has nothing in front of it.
        head = text[: len(text) - kept]
        stripped = head.rstrip(" \t\n")
        token_start = max(
            stripped.rfind(" "), stripped.rfind("\n"),
            stripped.rfind("\t"), stripped.rfind(">"),
        ) + 1
        candidate = stripped[token_start:]
        if (
            candidate
            and len(candidate) <= MAX_NAME_TAIL
            and _NAME.match(candidate) is not None
        ):
            kept = len(text) - token_start
        return kept

    def _take_one(self, text: str) -> "tuple[ParsedCall | None, int]":
        # ENG-08: the opening marker is optional. When it is absent the
        # span starts at the name itself, which `_call_start` has already
        # positioned us on.
        begin = next((m for m in CALL_BEGIN if text.startswith(m)), "")
        arg_at = self._first_index(text, ARGUMENT_BEGIN)
        end_at = self._first_index(text, CALL_END)
        if arg_at is None or end_at is None or end_at < arg_at:
            return None, 0

        arg_marker = next(m for m in ARGUMENT_BEGIN if text[arg_at:].startswith(m))
        end_marker = next(m for m in CALL_END if text[end_at:].startswith(m))

        name_part = text[len(begin):arg_at].strip()
        arguments = text[arg_at + len(arg_marker):end_at].strip()
        consumed = end_at + len(end_marker)

        match = _NAME.match(name_part)
        if match is None:
            return None, 0
        try:
            json.loads(arguments)
        except (TypeError, ValueError):
            # Arguments the world could not act on. Left as text on
            # purpose: a turn that did nothing is bad, a turn that did
            # something else is worse.
            return None, 0
        return ParsedCall(
            name=match.group(1),
            arguments=arguments,
            raw=text[:consumed],
        ), consumed


def synthetic_tool_use_id() -> str:
    """The id herald expects on a tool_use block. `toolu_` prefixed, like
    a real one, because every consumer downstream matches on that shape -
    and derived here rather than taken from the model, which did not send
    one."""
    return f"toolu_{uuid.uuid4().hex[:24]}"
