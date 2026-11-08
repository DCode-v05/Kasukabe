"""EVAL — the agent uses the sandbox at its own will.

Boots the real engine against a real Claude model and gives it open-ended tasks
that REQUIRE the sandbox, but leaves the HOW entirely to the agent (which tool,
how many steps, what processes to spawn). Instruments the sandbox tools to record
exactly what the agent chose to run, then scores whether the sandbox is a smooth,
useful tool: did tasks succeed, did the agent actually reach for the sandbox, and
did tool calls run cleanly (no fail-closed surprises, low error rate).

No engine code changes: the engine already speaks the Anthropic Messages wire, so
we just point cdt_url at api.anthropic.com (see src/herald/transport.py).

PREREQS — gated on env; skips loudly when missing:
    ANTHROPIC_API_KEY   = sk-ant-...                    (required)
    ANTHROPIC_MODEL     = claude-sonnet-4-6  (optional, agent model)
    ANTHROPIC_LIGHT_MODEL = claude-haiku-4-5 (optional, router/light model)

RUN inside the engine test container (Linux + bwrap + network egress):

    set -a; . ./.env; set +a
    docker compose run --rm -e ANTHROPIC_API_KEY -e ANTHROPIC_MODEL -e ANTHROPIC_LIGHT_MODEL \\
        test pytest tests/eval_sandbox_agent.py -s -v
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from src.bootstrap import bootstrap, shutdown
from src.config import Settings

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AGENT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
LIGHT_MODEL = os.environ.get("ANTHROPIC_LIGHT_MODEL", "claude-haiku-4-5").strip()

pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="ANTHROPIC_API_KEY not set — set it to run the live sandbox-agent eval",
)

SANDBOX_TOOLS = [
    "bash",
    "python",
    "read_file",
    "write_file",
    "file_edit",
    "list_directory",
    "search_files",
    "process",
]

PER_TASK_TIMEOUT_S = 180
ONLY = {t.strip() for t in os.environ.get("EVAL_ONLY", "").split(",") if t.strip()}


async def auto_approve(params):
    """Headless stand-in for a cooperative user.

    Every permission prompt and ask_user funnels through the thread's
    ask_user_callback (coordinator.await_block_input). In a headless eval nobody
    answers, so the agent would block forever. This approves so the agent can use
    the sandbox at will — the bypass-immune deny gates (rm -rf /, curl|sh,
    dangerous paths) still block catastrophic actions regardless.
    """
    from src.sandbox.permissions import parse_ask_request

    q, choices, suggested = parse_ask_request(params)
    if bool(choices) or bool(suggested):
        return {"response": "allow_always", "feedback": "", "content_blocks": []}
    return "yes"


TASKS = [
    {
        "id": "fib30",
        "prompt": (
            "Use your sandbox to compute the 30th Fibonacci number where F(1)=1 and "
            "F(2)=1. Actually run code to be certain, then tell me the number."
        ),
        "expect_all": ["832040"],
        "core": True,
    },
    {
        "id": "sum_1_100",
        "prompt": (
            "In your sandbox workspace, create a file numbers.txt containing the "
            "integers 1 through 100, one per line. Then compute the sum of those "
            "numbers and tell me the total."
        ),
        "expect_all": ["5050"],
        "core": True,
    },
    {
        "id": "primes12",
        "prompt": "Use code to find the first 12 prime numbers, then list them for me.",
        "expect_all": ["31", "37"],
        "core": False,
    },
    {
        "id": "background_process",
        "prompt": (
            "Start a BACKGROUND process that appends one line to "
            "/workspace/ticker.log every 0.2 seconds. Let it run for about a second, "
            "then check how many lines it has written, and finally kill it. "
            "Report the line count and confirm the process is stopped."
        ),
        "expect_all": [],
        "require_tools": ["process"],
        "core": False,
    },
    {
        "id": "multifile_pipeline",
        "prompt": (
            "Write and run a Python program in your sandbox that creates three files "
            "a.txt, b.txt, c.txt containing 10, 20 and 30 respectively, then reads them "
            "back and prints their sum. Tell me the sum."
        ),
        "expect_all": ["60"],
        "core": False,
    },
]


def eval_settings() -> Settings:
    """Engine settings that route LLM calls straight to Anthropic (no citadel)."""
    return Settings(
        cdt_url="https://api.anthropic.com",
        cdt_key=API_KEY,
        llm_model=AGENT_MODEL,
        light_model=LIGHT_MODEL,
        sqlite_db_path=":memory:",
        catalog_dir=str(Path(__file__).parent.parent / "catalog"),
        ad_encryption_key="",
    )


def norm(s: str) -> str:
    return "".join(s.lower().split()).replace(",", "")


def final_text(result) -> str:
    return getattr(result, "final_text", None) or (
        result if isinstance(result, str) else str(result)
    )


def instrument(state, sink: list[dict]) -> None:
    """Wrap each sandbox tool handler to record every call the agent makes."""
    for name in SANDBOX_TOOLS:
        td = state.tools.get_tool(name) if state.tools else None
        if td is None:
            continue
        original = td.handler

        async def wrapped(params, name=name, orig=original):
            rec = {"tool": name, "ok": True, "infra_error": False, "output": ""}
            try:
                out = await orig(params)
                text = out if isinstance(out, str) else str(out)
                rec["output"] = text[:240]
                if "Refusing to run unsandboxed" in text:
                    rec["infra_error"] = True
                    rec["ok"] = False
                elif text.lstrip().startswith("Error"):
                    rec["ok"] = False
                return out
            except Exception as e:  # noqa: BLE001 — record then re-raise
                rec["ok"] = False
                rec["output"] = f"EXC:{type(e).__name__}:{e}"
                raise
            finally:
                sink.append(rec)

        td.handler = wrapped


async def run_eval() -> dict:
    state, report = await bootstrap(settings=eval_settings())
    results: list[dict] = []
    calls: list[dict] = []
    try:
        assert report.subsystems.get("sandbox") is True, "sandbox subsystem failed to boot"
        assert state.sandbox_session is not None, "no sandbox session provisioned"
        assert state.coordinator is not None, "no coordinator"
        state.coordinator.ask_user_callback = auto_approve
        instrument(state, calls)

        tasks = [t for t in TASKS if not ONLY or t["id"] in ONLY]
        for i, task in enumerate(tasks):
            start_len = len(calls)
            t0 = time.monotonic()
            final = ""
            error = None
            try:
                res = await asyncio.wait_for(
                    state.coordinator.handle_input(task["prompt"], thread_id=f"eval-{task['id']}"),
                    timeout=PER_TASK_TIMEOUT_S,
                )
                final = final_text(res)
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"

            task_calls = calls[start_len:]
            tools_used = sorted({c["tool"] for c in task_calls})
            infra_errs = sum(1 for c in task_calls if c["infra_error"])
            tool_errs = sum(1 for c in task_calls if not c["ok"] and not c["infra_error"])

            text_ok = (
                all(norm(x) in norm(final) for x in task["expect_all"])
                if task["expect_all"]
                else True
            )
            tools_ok = all(t in tools_used for t in task.get("require_tools", []))
            used_sandbox = len(task_calls) > 0
            passed = (error is None) and text_ok and tools_ok and used_sandbox and infra_errs == 0

            results.append(
                {
                    "id": task["id"],
                    "core": task["core"],
                    "passed": passed,
                    "used_sandbox": used_sandbox,
                    "tools_used": tools_used,
                    "calls": len(task_calls),
                    "tool_errors": tool_errs,
                    "infra_errors": infra_errs,
                    "secs": round(time.monotonic() - t0, 1),
                    "error": error,
                    "final": final[:200].replace("\n", " "),
                }
            )
        return {"results": results, "calls": calls, "report": report}
    finally:
        await shutdown(state)


def print_scorecard(data: dict) -> dict:
    results = data["results"]
    calls = data["calls"]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    total_calls = len(calls)
    total_tool_err = sum(1 for c in calls if not c["ok"] and not c["infra_error"])
    total_infra_err = sum(1 for c in calls if c["infra_error"])
    distinct = sorted({c["tool"] for c in calls})
    smoothness = 1.0 - (total_tool_err / total_calls) if total_calls else 0.0

    print("\n" + "=" * 78)
    print("  SANDBOX-AGENT EVAL — agent driving the sandbox at its own will")
    print("=" * 78)
    print(f"  model={AGENT_MODEL}  light={LIGHT_MODEL}")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        core = " (core)" if r["core"] else ""
        print(
            f"  [{mark}] {r['id']}{core}  {r['secs']}s  "
            f"calls={r['calls']} toolerr={r['tool_errors']} infraerr={r['infra_errors']}"
        )
        print(f"         tools={r['tools_used']}")
        if r["error"]:
            print(f"         error={r['error']}")
        print(f"         final: {r['final']}")
    print("-" * 78)
    print(f"  tasks passed     : {passed}/{total}  ({round(100 * passed / total)}%)")
    print(f"  sandbox tool calls: {total_calls}  | distinct tools used: {distinct}")
    print(
        f"  tool error rate  : {total_tool_err}/{total_calls}  | smoothness: {round(smoothness, 3)}"
    )
    print(f"  infra (fail-closed) errors: {total_infra_err}  (must be 0 in a bwrap container)")
    print("=" * 78 + "\n")

    return {
        "passed": passed,
        "total": total,
        "total_calls": total_calls,
        "infra_errors": total_infra_err,
        "distinct": distinct,
        "smoothness": smoothness,
        "core_results": {r["id"]: r["passed"] for r in results if r["core"]},
    }


@pytest.mark.asyncio
async def test_agent_uses_sandbox_at_will():
    data = await run_eval()
    score = print_scorecard(data)

    assert score["total_calls"] > 0, "agent never used the sandbox"
    assert score["infra_errors"] == 0, "sandbox refused to execute inside a bwrap container"
    for tid, ok in score["core_results"].items():
        assert ok, f"core task failed: {tid}"
    assert score["passed"] / score["total"] >= 0.6, "too many sandbox tasks failed"


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("set ANTHROPIC_API_KEY to run the eval")
    print_scorecard(asyncio.run(run_eval()))
