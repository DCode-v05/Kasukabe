"""Dispatch eval - does triage hold its rules on a given model?

This is the gate a model passes before it is allowed to run triage. It
exercises the PRODUCTION prompt, the PRODUCTION index builder and the
PRODUCTION parser - imported, never copied - so a passing score is a statement
about the engine rather than about a lookalike script.

It needs network and a key, so it is not part of the unit suite:

    OPENAI_API_KEY=...    python evals/dispatch_eval.py --model gpt-5-mini
    ANTHROPIC_API_KEY=... python evals/dispatch_eval.py --model claude-haiku-4-5
    GEMINI_API_KEY=...    python evals/dispatch_eval.py --model gemini-3.1-flash-lite

Exit code is non-zero when a case fails, so CI can gate on it.

The claimed rule is blocking. A model that continues into a claimed thread
takes work the product layer owns, and does it silently - which is precisely
why it is checked here rather than left to be discovered in use.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine_core.dispatch_parser import parse_dispatch  # noqa: E402
from src.engine_core.schema import ThreadIndexEntry  # noqa: E402
from src.engine_core.thread_router import (  # noqa: E402
    ROUTER_SYSTEM_PROMPT,
    build_thread_index,
)

INDEX_ENTRIES = {
    "thread-a41b": ThreadIndexEntry(
        thread_id="thread-a41b",
        summary="Vendor contract renegotiation for the Delhi office lease",
        current_state="awaiting revised SLA terms from vendor's legal team",
        recent_turns=["chase the vendor legal team", "compare clause 7 to the Mumbai lease"],
        turn_count=14,
        last_activity="2026-07-15T18:40:00Z",
        pinned=True,
    ),
    "thread-9f2c": ThreadIndexEntry(
        thread_id="thread-9f2c",
        summary="Q3 investor memo drafted from July board notes",
        current_state="draft v2 complete, awaiting financials section",
        recent_turns=["tighten the runway paragraph", "add the hiring plan table"],
        turn_count=8,
        last_activity="2026-07-16T09:10:00Z",
    ),
    "thread-c771": ThreadIndexEntry(
        thread_id="thread-c771",
        summary="SF offsite travel and logistics",
        current_state="hotel shortlist prepared, flights not yet booked",
        recent_turns=["prefer the Mission Bay hotel", "team arrives Aug 4"],
        turn_count=5,
        last_activity="2026-07-14T15:22:00Z",
    ),
    "thread-d802": ThreadIndexEntry(
        thread_id="thread-d802",
        summary="Recruiting pipeline for the founding designer role",
        current_state="3 candidates in final loop, references pending for one",
        recent_turns=["schedule the portfolio review", "draft the offer band memo"],
        turn_count=11,
        last_activity="2026-07-16T08:05:00Z",
    ),
    "thread-e913": ThreadIndexEntry(
        thread_id="thread-e913",
        summary="Migrate billing off legacy Stripe integration",
        current_state="webhook parity matrix half complete",
        recent_turns=["map the dunning webhooks", "check proration behaviour"],
        turn_count=9,
        last_activity="2026-07-15T21:30:00Z",
        pinned=True,
    ),
    "thread-f024": ThreadIndexEntry(
        thread_id="thread-f024",
        summary="Weekly customer support digest automation",
        current_state="digest template approved, cron schedule undecided",
        recent_turns=["group tickets by feature area", "send every Friday"],
        turn_count=4,
        last_activity="2026-07-11T10:00:00Z",
    ),
    "thread-1246": ThreadIndexEntry(
        thread_id="thread-1246",
        summary="GST filing and CA coordination for FY Q1",
        current_state="books reconciled, awaiting CA's draft filing",
        recent_turns=["send the June invoices", "deadline is Jul 20"],
        turn_count=7,
        last_activity="2026-07-16T07:50:00Z",
    ),
}

KNOWN_IDS = set(INDEX_ENTRIES)

# name, input, expected segments, expected verdicts, blocking
CASES = [
    ("smalltalk", "hi", 1, {"status"}, False),
    (
        "single continue",
        "also add the Q2 revenue numbers to that memo before you finish it",
        1,
        {"continue"},
        False,
    ),
    (
        "three enumerated asks",
        "Book my SF flights for the offsite, draft a job description for a senior "
        "backend engineer, and where are we on the GST filing?",
        3,
        {"continue", "open", "recall"},
        False,
    ),
    (
        "one task, several sentences",
        "I want to rethink the support digest. It should go out Tuesday and Friday "
        "instead of just Friday. Also make sure the sentiment counts are split by "
        "plan tier, and cc the whole leadership list.",
        1,
        {"continue"},
        True,
    ),
    (
        "action demand at a claimed thread",
        "push the vendor for the revised SLA terms, we cannot slip past this month",
        1,
        {"claimed"},
        True,
    ),
    ("status question", "what all is going on right now?", 1, {"status"}, False),
    (
        "new work plus a claimed thread",
        "Start planning the Diwali team gift boxes. And on the Stripe migration, "
        "make sure proration is covered before anything else.",
        2,
        {"open", "claimed"},
        True,
    ),
]


def call_openai(model, system, user, key):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": 2000,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def call_anthropic(model, system, user, key):
    body = {
        "model": model,
        "max_tokens": 1000,
        "system": [{"type": "text", "text": system}],
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        resp = json.loads(r.read())
    return "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")


def call_gemini(model, system, user, key):
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    # Two ways in, and they take different headers. API keys go in
    # x-goog-api-key and come in more than one shape: the classic AIza... and
    # the newer AI Studio AQ.... OAuth access tokens are bearers and start
    # ya29.. Assume an API key unless it looks like a bearer - the failure mode
    # of guessing wrong is a 401 that reads like a bad credential rather than a
    # misplaced one, which sends you looking in the wrong place entirely.
    headers = {"content-type": "application/json"}
    if key.startswith("ya29."):
        headers["authorization"] = f"Bearer {key}"
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        if project:
            headers["x-goog-user-project"] = project
    else:
        headers["x-goog-api-key"] = key
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        resp = json.loads(r.read())
    return "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"])


def pick_provider(model):
    if model.startswith("claude"):
        return call_anthropic, "ANTHROPIC_API_KEY"
    if model.startswith("gemini"):
        return call_gemini, "GEMINI_API_KEY"
    return call_openai, "OPENAI_API_KEY"


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    call, key_env = pick_provider(args.model)
    key = os.environ.get(key_env, "").strip()
    if not key:
        print(f"set {key_env} to run this eval against {args.model}")
        return 2

    index_text = build_thread_index(INDEX_ENTRIES)
    passes = 0
    blocking_failures = []
    not_verbatim = []

    print(f"model: {args.model}")
    print(f"index: ~{len(index_text) // 4} tokens, {len(INDEX_ENTRIES)} threads\n")

    for name, message, want_n, want_verdicts, blocking in CASES:
        started = time.time()
        try:
            raw = call(
                args.model,
                ROUTER_SYSTEM_PROMPT,
                f"{index_text}\n\nNew input: {message}",
                key,
            )
        except urllib.error.HTTPError as e:
            print(f"  [FAIL] {name}: HTTP {e.code} {e.read().decode()[:160]}")
            if blocking:
                blocking_failures.append(name)
            continue
        elapsed = time.time() - started

        dispatch = parse_dispatch(raw, message, KNOWN_IDS)
        verdicts = {s.verdict for s in dispatch.segments}
        verbatim = all(normalize(s.extract) in normalize(message) for s in dispatch.segments)
        if not verbatim:
            not_verbatim.append(name)

        ok = len(dispatch.segments) == want_n and verdicts == want_verdicts
        passes += ok
        if not ok and blocking:
            blocking_failures.append(name)

        detail = " ; ".join(
            f"{s.verdict}->{s.target or ','.join(s.related_ids) or '-'}"
            for s in dispatch.segments
        )
        print(
            f"  [{'pass' if ok else 'FAIL'}] {name} ({elapsed:.1f}s) "
            f"{len(dispatch.segments)} seg | {detail}"
            f"{'' if verbatim else '  [rewrote the user]'}"
        )
        if args.verbose and not ok:
            print(f"         wanted {want_n} segment(s) {sorted(want_verdicts)}")
            print(f"         raw: {raw[:300]}")

    print(f"\n{passes}/{len(CASES)} passed")
    if not_verbatim:
        # The parser catches this at runtime and falls back to the whole input,
        # so it is not fatal - but a model that paraphrases is one that will
        # keep costing segments their scope.
        print(f"paraphrased instead of cutting: {', '.join(not_verbatim)}")
    if blocking_failures:
        print(f"BLOCKING: {', '.join(blocking_failures)}")
        print("This model must not run triage.")
        return 1
    if passes < len(CASES):
        print("Non-blocking failures - review before promoting this model.")
        return 1
    print("Qualified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
