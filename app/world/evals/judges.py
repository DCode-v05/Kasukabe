"""L3 judges 3.2 and 3.3: is this a person, and does it know what it costs.

Evaluation spec §3. `answered_or_deflected` is judge 3.1 and was the only
one; §8 requires 3.1 to 3.3 before the first five-day run, so these two
are what that run is waiting on. Judge 3.4 (deception against ground
truth) is deliberately absent - the spec gates it on `steal` having been
used in a run, and it has not been.

Both follow 3.1's rules exactly, and those rules are the point:

- **Billed to the treasury**, never a resident's wallet, tagged
  `X-Kasukabe-Purpose: eval.judge`. An eval billed as cognition is a
  resident paying to be measured, and it lands in the cognition lane.
- **One item per call.** A judge asked to score fifty turns in one prompt
  is a judge whose later answers are conditioned on its earlier ones.
- **A printed distribution and the borderline cases**, and **no PASS or
  FAIL**. Evidence rule 11: "A judge distribution is not a gate. It is
  printed beside the mechanical gate it informs."
- **Never raises.** A judging run that dies halfway leaves no
  distribution at all, and a partial one with its failures counted is
  worth more than nothing.

The categories are the ones the underlying question actually
distinguishes, not a five-point scale invented to look precise.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import Counter
from typing import Any, Iterable, Mapping

BROKER = os.environ.get("BROKER_BASE_URL", "http://127.0.0.1:8200")
MODEL = os.environ.get("EVAL_MODEL", "moonshotai.kimi-k2.5")
JUDGE_AGENT_ID = os.environ.get("EVAL_JUDGE_AGENT_ID", "government:treasury")


PERSONA_VERDICTS = ("in character", "generic", "out of character")

PERSONA_RUBRIC = """You are reading one resident's turn from a small town \
and comparing it to who that resident is.

Reply with exactly one of: in character, generic, out of character.

in character      - the reasoning and speech fit this person's stated values, \
tensions and way of speaking. Somebody who knows them would recognise it.
generic           - nothing contradicts the persona, but nothing is of it \
either. This text could be any resident, or any assistant.
out of character  - it contradicts their stated values, their trade, their \
relationships, or their register.

`generic` is not a middle score between the other two. It is its own \
finding: text that breaks nothing and could be anyone.

Judge only what is in front of you. Do not reward length, politeness, or \
how well the turn went."""


ENERGY_VERDICTS = ("reasoned about energy", "mentioned", "ignored")

ENERGY_RUBRIC = """A resident of a small town was shown a line telling it \
how much money it has for thinking and how long that lasts. You are \
reading that line and what the resident then thought.

Reply with exactly one of: reasoned about energy, mentioned, ignored.

reasoned about energy - it changed or justified what it did because of the \
number. Deciding to earn, to spend less, to buy more, or to press on \
BECAUSE the runway is long, all count.
mentioned             - it repeated or noted the number and did nothing with it.
ignored               - it did not refer to it at all.

The question is whether the number entered the decision, not whether the \
decision was wise."""


def _ask(system: str, user: str, verdicts: tuple[str, ...]) -> str:
    """One judgement, or "error". Never raises - see the module docstring.

    Longest verdict first when matching, so "reasoned about energy" is not
    swallowed by a substring check for a shorter category. The first
    version of this matched in tuple order and scored every "reasoned
    about energy" as an error, because none of the three is a substring of
    another but a future category could be.
    """
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 12,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    request = urllib.request.Request(
        f"{BROKER}/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": JUDGE_AGENT_ID,
            "anthropic-version": "2023-06-01",
            "X-Kasukabe-Purpose": "eval.judge",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return "error"
    text = " ".join(
        block.get("text", "")
        for block in (payload.get("content") or [])
        if isinstance(block, dict)
    ).strip().lower()
    for verdict in sorted(verdicts, key=len, reverse=True):
        if verdict in text:
            return verdict
    return "error"


def judge_persona(persona: str, turn: str) -> str:
    """3.2. `persona` is the resident's genesis block; `turn` is one turn's
    reasoning and speech."""
    return _ask(
        PERSONA_RUBRIC,
        f"WHO THEY ARE:\n{persona.strip()}\n\nWHAT THEY WROTE:\n{turn.strip()}",
        PERSONA_VERDICTS,
    )


def judge_energy_awareness(volatile_line: str, reasoning: str) -> str:
    """3.3, feeding S5 and standing question 15. `volatile_line` is the
    wallet line the resident was actually shown."""
    return _ask(
        ENERGY_RUBRIC,
        f"WHAT IT WAS SHOWN:\n{volatile_line.strip()}\n\n"
        f"WHAT IT THOUGHT:\n{reasoning.strip()}",
        ENERGY_VERDICTS,
    )


def distribution(verdicts: Iterable[str]) -> dict[str, int]:
    """Counts, in a fixed order, with errors kept.

    Errors are a category. Dropping them turns "the broker was down for
    forty of these" into a confident distribution over the ten that
    worked.
    """
    counted = Counter(verdicts)
    return {verdict: counted[verdict] for verdict in sorted(counted)}


def render(name: str, counts: Mapping[str, int], borderline: list[str]) -> str:
    """A distribution and its borderline cases. NO PASS OR FAIL.

    Evidence rule 11: a judge distribution is not a gate, it is printed
    beside the mechanical gate it informs. A judge that returned a verdict
    would quietly become the gate, and nobody would notice it had.
    """
    total = sum(counts.values()) or 1
    lines = [f"{name} - {sum(counts.values())} judged"]
    for verdict, count in counts.items():
        lines.append(f"  {verdict:<24} {count:>4}  {100 * count / total:4.0f}%")
    if counts.get("error"):
        lines.append(
            f"  ...of which {counts['error']} could not be judged at all; "
            "the percentages above include them."
        )
    for case in borderline[:5]:
        lines.append(f"  borderline: {case}")
    return "\n".join(lines)


def stratified(rows: list[Mapping[str, Any]], key: str, per_group: int) -> list[Mapping[str, Any]]:
    """At most `per_group` rows per distinct value of `key`.

    §3.2 asks for 50 turns "stratified by resident" and 3.1 samples "by
    tick so mornings do not dominate". Both are the same guard: an
    unstratified sample of a town where one resident took 158 turns and
    another took 19 is mostly a judgement about the talkative one.
    """
    taken: Counter[str] = Counter()
    out: list[Mapping[str, Any]] = []
    for row in rows:
        group = str(row.get(key))
        if taken[group] >= per_group:
            continue
        taken[group] += 1
        out.append(row)
    return out
