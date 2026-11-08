"""Did they ANSWER, or just say something back? A real model reads and decides.

    uv run python -m world.evals.answered_or_deflected data/final1.db

THE GAP THIS CLOSES, and it is the central unexplained finding of the
whole series. From docs/runs/series/DYNAMIC_CHAT_CONSOLIDATED_REPORT.md
section 4.2, comparing the graded reply rate against a generous
reconstruction of "did the addressee speak again at all":

    run      official G1   addressee spoke again   gap
    final1   32%           ~74%                    wide
    final2   43%           ~68%                    wide
    final4   37%           ~71%                    wide

    "The gap IS the finding: residents remain present and keep talking,
    but the next thing they say is frequently their OWN AGENDA rather than
    an answer to the question they were just asked."

Neither number is the interesting one. `loop_report`'s G1 and this
package's B1 both ask "did the person spoken to later speak back to the
asker", which is the generous, mechanical test - it counts a deflection,
a change of subject and a flat refusal all as replies. The reconstruction
is looser still.

**What nobody has measured is whether the reply ANSWERED anything**, and
no regular expression can. It needs a reader. So this asks a real model,
one pair at a time, and reports the three outcomes conversation analysis
actually distinguishes.

THE CATEGORIES ARE NOT INVENTED HERE. Stivers et al. (PNAS 2009) split
responses into ANSWERS and NON-ANSWERS and found answers to be 64-87% of
responses across ten languages; the non-answer half is where a deflection
lives. See docs/HUMAN_BASELINES.md section 1.

    answered   - the question was addressed, including "no" and "I don't
                 know". A refusal is an answer. Conversation analysis
                 calls this a dispreferred response, not a non-response.
    deflected  - they spoke to the asker and did not address it: changed
                 the subject, issued an order, made a speech.
    silent     - they never spoke back to the asker at all.

WHAT IT COSTS, AND WHY IT IS SEPARATE FROM behavioural.py. This spends
real money on real calls - one per exchange, tens to low hundreds per run
- so it is a deliberate command rather than part of the standing eval.
`behavioural.py` is free and runs on every store; this runs when somebody
wants to know what the gap is made of.

THE JUDGE'S OWN LIMITS, stated rather than buried. It is one model
grading another's conversation with a three-way rubric, and it will get
some wrong. It is not a gate for that reason: it prints a distribution and
the pairs it judged, so a human can read the disagreements. A model
scoring a model is evidence, not a verdict.
"""

from __future__ import annotations

import collections
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

from world.evals.behavioural import names, selections, utterances

BROKER = os.environ.get("BROKER_BASE_URL", "http://127.0.0.1:8200")
MODEL = os.environ.get("EVAL_JUDGE_MODEL", "moonshotai.kimi-k2.5")

JUDGE_AGENT_ID = os.environ.get("EVAL_JUDGE_AGENT_ID", "government:treasury")

REPLY_WINDOW_TICKS = 12

VERDICTS = ("answered", "deflected", "silent")

RUBRIC = """You are reading one exchange from a transcript of a small town and \
deciding one thing: did the second person ANSWER the first?

Reply with exactly one word: answered, or deflected.

answered  - they addressed what was put to them. "No", "I don't know", "not \
now" and a refusal all count: a dispreferred answer is still an answer.
deflected - they spoke, but did not address it. They changed the subject, \
gave an order, made a speech, or answered a different question.

Judge only what is in front of you. Do not reward politeness or length."""


def ask(question: str, reply: str) -> str:
    """One judgement, or "error" if the broker could not be reached.

    Never raises. A judging run that dies halfway through leaves no
    distribution at all, and a partial distribution with its failures
    counted is worth more than nothing.
    """
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 8,
        "system": RUBRIC,
        "messages": [{
            "role": "user",
            "content": f"FIRST PERSON: {question}\n\nSECOND PERSON: {reply}",
        }],
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
    for verdict in ("answered", "deflected"):
        if verdict in text:
            return verdict
    return "error"


def exchanges(utts: list[dict]) -> list[tuple[dict, dict | None]]:
    """(a remark that selected somebody, their first reply to the asker).

    The reply is None when they never spoke back inside the window - the
    "silent" third of the distribution, which needs no model to see.
    """
    by_speaker: dict[str, list[dict]] = collections.defaultdict(list)
    for u in utts:
        by_speaker[u["speaker"]].append(u)

    pairs = []
    for question in selections(utts):
        reply = next(
            (
                r
                for r in by_speaker.get(question["listener"], [])
                if question["tick"] < r["tick"] <= question["tick"] + REPLY_WINDOW_TICKS
                and r["listener"] == question["speaker"]
            ),
            None,
        )
        pairs.append((question, reply))
    return pairs


def main(path: str) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    who = names(conn)
    pairs = exchanges(utterances(conn))
    if not pairs:
        print(f"{path}: nobody selected anybody. Nothing to judge.")
        return 1

    print(f"\n{'=' * 72}\nANSWERED OR DEFLECTED  {path}")
    print(f"judge {MODEL} via {BROKER}, {len(pairs)} exchanges - this spends money")
    print("=" * 72)

    tally: collections.Counter = collections.Counter()
    judged: list[tuple[dict, dict, str]] = []
    for question, reply in pairs:
        if reply is None:
            tally["silent"] += 1
            continue
        verdict = ask(question["content"], reply["content"])
        tally[verdict] += 1
        if verdict in VERDICTS:
            judged.append((question, reply, verdict))

    total = sum(tally[v] for v in VERDICTS)
    if not total:
        print("every judgement failed. This eval needs the SAME STACK a run needs:")
        print(f"   a broker on {BROKER}, and a world on 8100 behind it, because")
        print("   the broker checks a wallet before every call and cannot reach")
        print("   one on its own.")
        print(f"   The wallet it charges is {JUDGE_AGENT_ID}, never a resident's.")
        return 1

    print(f"\n{'OUTCOME':<14}{'COUNT':>7}{'SHARE':>9}")
    for verdict in VERDICTS:
        print(f"{verdict:<14}{tally[verdict]:>7}{tally[verdict] / total:>8.0%}")
    if tally["error"]:
        print(f"{'unjudged':<14}{tally['error']:>7}   (broker errors, excluded)")

    answered = tally["answered"] / total
    spoke_back = (tally["answered"] + tally["deflected"]) / total
    print(f"\nspoke back at all      {spoke_back:.0%}   <- what G1 and B1 measure")
    print(f"actually answered      {answered:.0%}   <- what a human reader sees")
    print(f"the gap                {spoke_back - answered:.0%}   <- deflection")
    print("\nhuman floor is 64% answers (Stivers 2009, ten languages).")
    print("See docs/HUMAN_BASELINES.md section 1. NOT a gate: one model")
    print("grading another's conversation is evidence, not a verdict.")

    deflections = [(q, r) for q, r, v in judged if v == "deflected"][:6]
    if deflections:
        print("\njudged DEFLECTED - read these and disagree if the judge is wrong:")
        for question, reply in deflections:
            asker = who.get(question["speaker"], question["speaker"])
            answerer = who.get(reply["speaker"], reply["speaker"])
            print(f'\n   {asker} asked: "{question["content"][:96]}"')
            print(f'   {answerer} said: "{reply["content"][:96]}"')
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/final1.db"))
