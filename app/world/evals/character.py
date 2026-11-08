"""P1 to P3: is the engine leaking through the resident.

Evaluation spec §2.5, and the only gates in the whole document that are
about whether these are PEOPLE rather than about whether the town works.

They matter because the leak is real and measured. `kasukabe-what-is-
missing.md` §2.2: the `request_plan` reminder appeared in **61 of 240
sampled rows**. Run 19 has Tobias Holloway writing into his own memory
that "user placed me at h_quarry", and Ainsley Holloway inventing a
pantry of "3 grain, 2 meat" in the genre of an engine reporting to a
user. Both were reasoned from for the rest of the run.

Two seams already exist to stop that - `tool_surface.py` takes the
assistant's seven tools off a resident's registry, `resident_voice.py`
rewrites the summariser's prompt - and neither had a measurement. These
are the measurement. They are DETERMINISTIC, not judged: a model asked
"does this sound like an assistant" would be grading its own genre.

P1 and P2 are gated at ZERO in 500 sampled turns. Not "rare": a resident
that refers to itself as an engine once has told the trace that the
persona is a costume, and one occurrence is the whole finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

ORCHESTRATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brequest_plan\b", "the request_plan reminder"),
    (r"\brequest_replan\b", "request_replan, called four times across three runs"),
    (r"\bsub[- ]?agent\b", "a sub-agent"),
    (r"\bspawn_sub_agent\b", "spawn_sub_agent"),
    (r"\bmission brief\b", "a mission brief"),
    (r"\breport_checkpoint\b", "report_checkpoint"),
    (r"\bNPC\b", "the word NPC"),
    (r'"tool_calls"\s*:', "a tool_calls JSON block"),
    (r'"subtasks"\s*:', "a subtasks JSON block"),
)

FIRST_PERSON_LEAKS: tuple[tuple[str, str], ...] = (
    (r"\b(?:I|I'm|I am|my|me)\b[^.!?]{0,40}\b(?:the )?user\b", "calls somebody 'the user'"),
    (r"\bthe user\b[^.!?]{0,40}\b(?:asked|told|wants|said) me\b", "takes an order from 'the user'"),
    (r"\bI am (?:an? )?(?:engine|assistant|AI|language model|agent)\b", "calls itself an engine"),
    (r"\bas (?:an? )?(?:engine|assistant|AI|language model)\b", "frames itself as an assistant"),
    (r"\bmy (?:task|mission|objective|assignment)\b", "has a 'task' rather than a life"),
    (r"\bI (?:have|was given) (?:a|the) task\b", "was 'given a task'"),
)


@dataclass
class CharacterReport:
    """P1 to P3, with the sentence that decided each one.

    The sentences are the point. A count of six with no examples cannot
    be argued with, and the fix for a leak is always a change to the text
    that produced it - which nobody can make from a number.
    """

    sampled: int
    orchestration: list[dict[str, Any]] = field(default_factory=list)
    first_person: list[dict[str, Any]] = field(default_factory=list)
    name_asked: int = 0
    name_answered: int = 0

    @property
    def p1_passes(self) -> bool:
        """Zero. Not "rare"."""
        return not self.orchestration

    @property
    def p2_passes(self) -> bool:
        return not self.first_person

    @property
    def p3_rate(self) -> float | None:
        """None when nobody was ever asked their name, which is a
        different fact from nobody answering."""
        return (self.name_answered / self.name_asked) if self.name_asked else None


def _hits(text: str, patterns: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for pattern, why in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            start = max(0, match.start() - 60)
            found.append((why, text[start:match.end() + 60].replace("\n", " ").strip()))
    return found


def scan_turns(turns: Iterable[Mapping[str, Any]]) -> CharacterReport:
    """P1 and P2 over a run's turns.

    A turn is `{"agent_id": ..., "tick": ..., "reasoning": ..., "output": ...}` -
    the shape `session_log.py` already writes, so this reads what every
    engine has been recording all along rather than needing a new one.
    """
    report = CharacterReport(sampled=0)
    for turn in turns:
        report.sampled += 1
        text = f"{turn.get('reasoning', '')}\n{turn.get('output', '')}"
        where = {"agent_id": turn.get("agent_id"), "tick": turn.get("tick")}
        for why, quote in _hits(text, ORCHESTRATION_PATTERNS):
            report.orchestration.append({**where, "why": why, "quote": quote})
        for why, quote in _hits(text, FIRST_PERSON_LEAKS):
            report.first_person.append({**where, "why": why, "quote": quote})
    return report


NAME_QUESTION = re.compile(
    r"(?:what(?:'s| is) your name|who are you|what do (?:they|people) call you|"
    r"your name\?|tell me your name)",
    re.IGNORECASE,
)


def name_exchanges(
    utterances: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
    *,
    window_ticks: int = 12,
) -> tuple[int, int]:
    """(times asked, times answered with the name). P3's numerator and
    denominator.

    Answered means the reply contains the answerer's own name. A resident
    that says "I am the baker" has not answered the question asked, and a
    check that accepted it would report a town of anonymous tradespeople
    as one that introduces itself.
    """
    asked = 0
    answered = 0
    for index, utterance in enumerate(utterances):
        if not NAME_QUESTION.search(str(utterance.get("content", ""))):
            continue
        targets = utterance.get("addressed_agent_ids") or (
            [utterance["listener_agent_id"]] if utterance.get("listener_agent_id") else []
        )
        for target in targets:
            own_name = names.get(str(target))
            if not own_name:
                continue
            asked += 1
            tick = int(utterance.get("tick", 0) or 0)
            for later in utterances[index + 1:]:
                if int(later.get("tick", 0) or 0) - tick > window_ticks:
                    break
                if later.get("speaker_agent_id") != target:
                    continue
                first_name = own_name.split()[0]
                if first_name.lower() in str(later.get("content", "")).lower():
                    answered += 1
                    break
    return asked, answered


def render(report: CharacterReport) -> str:
    """P1 to P3 as the report prints them.

    P1 and P2 ARE gates and do say pass or fail - unlike the judges, which
    never do. The difference is that these are deterministic: a regex that
    found `request_plan` in a resident's reasoning has not formed an
    opinion.
    """
    lines = [f"Character (P1-P3), {report.sampled} turns sampled"]
    lines.append(
        f"  P1 orchestration leak   {'PASS' if report.p1_passes else 'FAIL'}"
        f"  ({len(report.orchestration)} occurrence(s), bar is 0)"
    )
    for hit in report.orchestration[:5]:
        lines.append(f"      {hit['agent_id']} t{hit['tick']}: {hit['why']} - \"{hit['quote']}\"")
    lines.append(
        f"  P2 first-person leak    {'PASS' if report.p2_passes else 'FAIL'}"
        f"  ({len(report.first_person)} occurrence(s), bar is 0)"
    )
    for hit in report.first_person[:5]:
        lines.append(f"      {hit['agent_id']} t{hit['tick']}: {hit['why']} - \"{hit['quote']}\"")
    if report.p3_rate is None:
        lines.append("  P3 says its own name    - nobody was asked")
    else:
        lines.append(
            f"  P3 says its own name    {'PASS' if report.p3_rate == 1.0 else 'FAIL'}"
            f"  ({report.name_answered}/{report.name_asked}, bar is 100%)"
        )
    return "\n".join(lines)
