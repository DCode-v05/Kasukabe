"""EVAL - the voice-channel narration rubric (spec D20).

Functional tests prove the voice machinery works; THIS harness measures how the
product SOUNDS. It exists because a model or router swap can silently change
narration cadence and persona while every deterministic test stays green - run
it after any model, router, or guide change, and on demand.

Three layers per scripted scenario:
  1. Deterministic ear checks (spec D19): no URLs/markup/citation glyphs in
     narration, plain punctuation, no verbatim duplication of the visible text.
  2. Deterministic behaviour checks: narration present, budget band
     (complementary vs voice_primary), provenance holds (no unproven media).
  3. An LLM rubric judge scoring the five speech acts (orient / progress /
     handoff / ask / wrap), colleague-on-a-call feel, and no reading-the-screen.

Prereqs / run (same as the sandbox evals):

    set -a; . ./.env; set +a
    docker compose run --rm -e ANTHROPIC_API_KEY test pytest tests/eval_voice_narration.py -s -v
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import pytest

from src.bootstrap import bootstrap, shutdown
from src.engine_core.events import (
    AudioAuthored,
    ImageAuthored,
    TextDelta,
    VideoAuthored,
    VoiceDelta,
)
from tests.eval_sandbox_agent import (
    API_KEY,
    AGENT_MODEL,
    auto_approve,
    eval_settings,
    final_text,
)

pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="ANTHROPIC_API_KEY not set - set it to run the live narration eval",
)

PER_TASK_TIMEOUT_S = 240
RUBRIC_PASS_SCORE = 3.0  # of 5, averaged over the judged axes

SCENARIOS = [
    {
        "id": "complement_overview",
        "prompt": (
            "Give me a short comparison of SQLite and Postgres for an early-stage "
            "startup, and end with your recommendation."
        ),
        "voice_primary": False,
        # Complementary narration: a fraction of the visible text, not a copy.
        "budget_band": (0.02, 0.8),
    },
    {
        "id": "primary_overview",
        "prompt": (
            "What should I check before signing a lease for a small office? "
            "Keep it practical."
        ),
        "voice_primary": True,
        # Ears-first: narration carries the response (spec D18).
        "budget_band": (0.3, 100.0),
    },
    {
        "id": "media_provenance_holds",
        "prompt": (
            "Show me a photo of the Eiffel Tower as an image attachment."
        ),
        "voice_primary": False,
        "budget_band": (0.0, 100.0),
        # No tool ran, no URL exists in this thread: the provenance registry
        # must block any invented URL (spec D12). Narration should still handle
        # the miss gracefully.
        "expect_no_media": True,
    },
]

# Spec D19 ear rules, checked mechanically on the narration text.
URL_RE = re.compile(r"https?://|www\.")
MARKUP_RE = re.compile(r"[<>{}]|```|\*\*|⟦|⟧")
FORBIDDEN_PUNCT_RE = re.compile(r"[()/\\;*_#~^|\[\]]|\.\.\.\.")
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")


def ear_violations(narration: str) -> list[str]:
    out = []
    if URL_RE.search(narration):
        out.append("spoke a URL")
    if MARKUP_RE.search(narration):
        out.append("spoke markup or citation glyphs")
    if FORBIDDEN_PUNCT_RE.search(narration):
        out.append("non-plain punctuation for the ear")
    if EMOJI_RE.search(narration):
        out.append("emoji in narration")
    return out


def duplication_violation(narration: str, visible: str) -> bool:
    """A >=80-char run of narration appearing verbatim in the visible text is
    reading the screen, not narrating."""
    n = narration.strip()
    for i in range(0, max(len(n) - 80, 0), 40):
        if n[i : i + 80] and n[i : i + 80] in visible:
            return True
    return False


async def judge(narration: str, visible: str, primary: bool) -> dict:
    """LLM rubric judge. Returns {scores: {axis: 1-5}, notes: str}."""
    from src.herald import create_llm
    from src.herald.types import Message as LLMMessage

    grader = create_llm(eval_settings(), AGENT_MODEL)
    mode = (
        "voice_primary (ears-first: the narration must carry the full response)"
        if primary
        else "complementary (the narration accompanies the visible reply)"
    )
    prompt = (
        "You are grading the VOICE NARRATION of an AI assistant that talks like a "
        f"colleague on a call while it works. Mode: {mode}.\n\n"
        f"NARRATION (what was spoken):\n---\n{narration or '(none)'}\n---\n\n"
        f"VISIBLE REPLY (what was shown on screen):\n---\n{visible[:4000]}\n---\n\n"
        "Score each axis 1-5 (5 = excellent):\n"
        "- human: sounds like a real colleague (first person, contractions, natural)\n"
        "- orient: opens by saying what it is doing\n"
        "- wrap: closes the loop at the end\n"
        "- no_reading: never reads the visible content verbatim; introduces and points\n"
        "- ear: short spoken sentences, numbers rounded, nothing that only works on screen\n\n"
        'Reply with STRICT JSON only: {"scores": {"human": n, "orient": n, "wrap": n, '
        '"no_reading": n, "ear": n}, "notes": "one sentence"}'
    )
    response = await grader.generate_with_tools(
        [LLMMessage(role="user", content=prompt, timestamp=time.time())],
        system=None,
        tools=[],
    )
    text = (response.text or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(match.group(0) if match else text)
    except (ValueError, AttributeError):
        return {"scores": {}, "notes": f"unparseable judge reply: {text[:120]}"}


async def run_scenarios() -> list[dict]:
    state, report = await bootstrap(settings=eval_settings())
    events: list = []

    async def collect(event) -> None:
        events.append(event)

    results: list[dict] = []
    try:
        state.coordinator.ask_user_callback = auto_approve
        state.coordinator.on(collect)

        for sc in SCENARIOS:
            start = len(events)
            t0 = time.monotonic()
            error, final = None, ""
            try:
                res = await asyncio.wait_for(
                    state.coordinator.handle_input(
                        sc["prompt"],
                        thread_id=f"voice-eval-{sc['id']}",
                        voice=True,
                        voice_primary=sc["voice_primary"],
                        image=True,
                        audio=True,
                        video=True,
                    ),
                    timeout=PER_TASK_TIMEOUT_S,
                )
                final = final_text(res)
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"

            window = events[start:]
            narration = "".join(e.text for e in window if isinstance(e, VoiceDelta))
            visible = "".join(
                e.text for e in window if isinstance(e, TextDelta) and e.text
            ) or final
            media = [
                e
                for e in window
                if isinstance(e, (ImageAuthored, AudioAuthored, VideoAuthored))
            ]

            checks: list[str] = []
            if error:
                checks.append(f"turn error: {error}")
            if not narration:
                checks.append("no narration was produced")
            checks.extend(ear_violations(narration))
            if duplication_violation(narration, visible):
                checks.append("narration duplicates the visible text verbatim")
            lo, hi = sc["budget_band"]
            ratio = (len(narration) / max(len(visible), 1)) if narration else 0.0
            if narration and not (lo <= ratio <= hi):
                checks.append(f"narration budget off band: ratio={ratio:.2f} band=({lo},{hi})")
            if sc.get("expect_no_media") and media:
                checks.append(
                    f"provenance breach: {len(media)} media block(s) emitted with no observed URL"
                )

            verdict = await judge(narration, visible, sc["voice_primary"]) if narration else {
                "scores": {},
                "notes": "not judged: no narration",
            }
            scores = [v for v in verdict.get("scores", {}).values() if isinstance(v, (int, float))]
            avg = sum(scores) / len(scores) if scores else 0.0
            passed = not checks and avg >= RUBRIC_PASS_SCORE
            results.append(
                {
                    "id": sc["id"],
                    "passed": passed,
                    "checks": checks,
                    "rubric_avg": round(avg, 2),
                    "scores": verdict.get("scores", {}),
                    "notes": verdict.get("notes", ""),
                    "narration_chars": len(narration),
                    "visible_chars": len(visible),
                    "ratio": round(ratio, 2),
                    "secs": round(time.monotonic() - t0, 1),
                    "narration_head": narration[:180].replace("\n", " "),
                }
            )
        return results
    finally:
        await shutdown(state)


@pytest.mark.asyncio
async def test_voice_narration_rubric():
    results = await run_scenarios()
    print("\n" + "=" * 78)
    print(f"  VOICE NARRATION RUBRIC EVAL  (model={AGENT_MODEL})")
    print("=" * 78)
    for r in results:
        print(
            f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['id']}  {r['secs']}s  "
            f"rubric={r['rubric_avg']} scores={r['scores']} "
            f"voice/visible={r['narration_chars']}/{r['visible_chars']} (r={r['ratio']})"
        )
        for c in r["checks"]:
            print(f"         check failed: {c}")
        if r["notes"]:
            print(f"         judge: {r['notes']}")
        print(f"         narration: {r['narration_head']}")
    passed = sum(1 for r in results if r["passed"])
    print("-" * 78)
    print(f"  scenarios passed: {passed}/{len(results)}")
    print("=" * 78 + "\n")

    # Provenance is a hard invariant, narration quality a threshold.
    assert not any(
        "provenance breach" in c for r in results for c in r["checks"]
    ), "an unproven media URL crossed the wire"
    assert passed / len(results) >= 0.6, "too many narration scenarios failed the rubric"
