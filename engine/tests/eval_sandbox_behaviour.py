"""EVAL — sandbox behaviour under a real agent (operational UX).

Companion to eval_sandbox_agent.py. Where that one shows the agent CAN use the
sandbox, this probes how it BEHAVES under real use: recovering from a failing
command, editing files, handling large output, and persisting state across turns.

Reuses the proven harness (auto-approve + tool instrumentation) from
eval_sandbox_agent. Same prereqs / run command:

    set -a; . ./.env; set +a
    docker compose run --rm -e ANTHROPIC_API_KEY test pytest tests/eval_sandbox_behaviour.py -s -v
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.bootstrap import bootstrap, shutdown
from tests.eval_sandbox_agent import (
    API_KEY,
    AGENT_MODEL,
    eval_settings,
    auto_approve,
    instrument,
    final_text,
    norm,
)

pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="ANTHROPIC_API_KEY not set — set it to run the live behaviour eval",
)

PER_TASK_TIMEOUT_S = 180

# Each probe reveals a behaviour the deterministic spec can't show as the agent sees it.
PROBES = [
    {
        "id": "error_recovery",
        "prompt": (
            "Run the shell command `cat /workspace/missing.txt` — it will fail. "
            "Read the error, then create that file containing the single word "
            "'recovered', and read it back successfully. Tell me the final contents."
        ),
        "expect_all": ["recovered"],
    },
    {
        "id": "file_edit",
        "prompt": (
            "Create a file notes.txt with exactly three lines, each line the word "
            "TODO. Then change ONLY the second line to DONE, leaving lines 1 and 3 as "
            "TODO. Show me the final file content."
        ),
        "expect_all": ["done"],
    },
    {
        "id": "large_output",
        "prompt": (
            "Use python in the sandbox to print 5000 lines, each line being "
            "'line-<n>' for n from 1 to 5000. Then tell me exactly how many lines you "
            "printed."
        ),
        "expect_all": ["5000"],
    },
]


async def run_probes() -> dict:
    state, report = await bootstrap(settings=eval_settings())
    calls: list[dict] = []
    results: list[dict] = []
    try:
        assert report.subsystems.get("sandbox") is True
        state.coordinator.ask_user_callback = auto_approve
        instrument(state, calls)

        for task in PROBES:
            start = len(calls)
            t0 = time.monotonic()
            final, error = "", None
            try:
                res = await asyncio.wait_for(
                    state.coordinator.handle_input(task["prompt"], thread_id=f"beh-{task['id']}"),
                    timeout=PER_TASK_TIMEOUT_S,
                )
                final = final_text(res)
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"
            tc = calls[start:]
            tools = sorted({c["tool"] for c in tc})
            infra = sum(1 for c in tc if c["infra_error"])
            terr = sum(1 for c in tc if not c["ok"] and not c["infra_error"])
            text_ok = all(norm(x) in norm(final) for x in task["expect_all"])
            passed = error is None and text_ok and len(tc) > 0 and infra == 0
            results.append(
                {
                    "id": task["id"],
                    "passed": passed,
                    "tools": tools,
                    "calls": len(tc),
                    "tool_errors": terr,
                    "infra_errors": infra,
                    "secs": round(time.monotonic() - t0, 1),
                    "error": error,
                    "final": final[:200].replace("\n", " "),
                }
            )
        return {"results": results, "calls": calls}
    finally:
        await shutdown(state)


@pytest.mark.asyncio
async def test_sandbox_behaviour_under_agent():
    data = await run_probes()
    results, calls = data["results"], data["calls"]
    print("\n" + "=" * 78)
    print(f"  SANDBOX BEHAVIOUR EVAL  (model={AGENT_MODEL})")
    print("=" * 78)
    for r in results:
        print(
            f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['id']}  {r['secs']}s  "
            f"tools={r['tools']} calls={r['calls']} toolerr={r['tool_errors']} infraerr={r['infra_errors']}"
        )
        if r["error"]:
            print(f"         error={r['error']}")
        print(f"         final: {r['final']}")
    passed = sum(1 for r in results if r["passed"])
    infra = sum(1 for c in calls if c["infra_error"])
    print("-" * 78)
    print(f"  probes passed: {passed}/{len(results)}  | infra (fail-closed) errors: {infra}")
    print("=" * 78 + "\n")

    assert infra == 0, "sandbox refused to execute inside a bwrap container"
    assert passed / len(results) >= 0.6, "too many behaviour probes failed"


@pytest.mark.asyncio
async def test_workspace_persists_across_turns():
    """The 'long-lived per-user box': a file written in turn 1 is still there in
    turn 2 (same engine, same shared sandbox session)."""
    state, _ = await bootstrap(settings=eval_settings())
    try:
        state.coordinator.ask_user_callback = auto_approve
        tid = "persist-across-turns"
        await asyncio.wait_for(
            state.coordinator.handle_input(
                "Create a file secret.txt in your sandbox containing exactly the number 4242. Just create it.",
                thread_id=tid,
            ),
            timeout=PER_TASK_TIMEOUT_S,
        )
        res2 = await asyncio.wait_for(
            state.coordinator.handle_input(
                "Read the file secret.txt you created a moment ago and tell me the number inside it.",
                thread_id=tid,
            ),
            timeout=PER_TASK_TIMEOUT_S,
        )
        final2 = final_text(res2)
        print(f"\n[persist-across-turns] turn-2 final: {final2[:160]}\n")
        assert "4242" in norm(final2), f"workspace did not persist across turns: {final2[:200]}"
    finally:
        await shutdown(state)
