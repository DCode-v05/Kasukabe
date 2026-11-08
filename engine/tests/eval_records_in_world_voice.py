"""EVAL - does a record in the world's voice stop a resident copying it?

`resident_voice.py` moves every stored turn record out of the resident's slot
and cuts the frozen engine's request_plan nudge. The unit tests prove what is
sent. Only the model can say whether it changes what comes back, so this
replays REAL requests: run 28b's first calls of a turn that followed a record
- the calls where 43% wrote record-shaped text and 47% called no tool - each
sent twice through the broker to the model the town uses:

  * as recorded (arm "as_recorded");
  * through the same rewrite a live turn now gets (arm "world_voice").

Same system prompt, same tools, same perception; only the rewrite differs.
The trace clipped every one of these bodies (`_dropped` rows), so both arms
see the same shortened history - a fair comparison, not a full replay.

Run (costs real money; cap the broker first):

    SPEND_CAP_USD=1.50 TRACE_SQLITE_PATH=data/replay.db \\
        python -m uvicorn broker.app:app --port 8300        # in app/
    KASUKABE_REPLAY_DB=../app/data/kasukabe-run28b-20260910.db \\
    KASUKABE_REPLAY_BROKER=http://127.0.0.1:8300 \\
    KASUKABE_REPLAY_TOOLS=world_tools.json \\
        python tests/eval_records_in_world_voice.py          # in engine/

`world_tools.json` is `tool_registry_rows(registry, world, agent)` for one
resident, each name prefixed `world_` - see `served_tools` below for why the
recorded tools cannot be used.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from src.herald.types import Message, TextBlock
from src.sim.resident_voice import speak_as_resident
from src.sim.session_log import RECORD_HEADING

DB = os.environ.get("KASUKABE_REPLAY_DB", "")
BROKER = os.environ.get("KASUKABE_REPLAY_BROKER", "")
SAMPLES = int(os.environ.get("KASUKABE_REPLAY_N", "30"))
CONCURRENCY = 4

COPIED_RECORD = re.compile(r"(^|\n)\s*-\s*world:\w+\(")

# The engine homes of the run. The trace cap cut ~19 messages from every one
# of these bodies, leaving three records to copy where the live call had
# about twelve, and the clipped replay did not reproduce the fault (1 copy in
# 30 against 12 live). With the homes, each call's history is rebuilt from
# the resident's own conversation store, exactly as `load_messages` served it.
HOMES = os.environ.get("KASUKABE_REPLAY_HOMES", "")
KEEP = int(os.environ.get("KASUKABE_HISTORY_MESSAGES", "24"))


def history_before(agent_id: str, recorded: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """The stored rows a call was handed, or None if they cannot be found.

    Aligned by CONTENT, not by time. The trace kept the last few messages
    that were sent; their place in the resident's conversation store is
    where the call's history ends, and the KEEP rows ending there are what
    `load_messages` served. A first attempt cut the store at the call's
    timestamp and proved only 123 of 235 calls - and the 112 it could not
    prove were the ones that copied a record live (98 of them), so a replay
    of the provable ones tested the wrong population.
    """
    brain = Path(HOMES) / agent_id / "brain.sqlite"
    kept = [{"role": m["role"], "content": m["content"]} for m in recorded[:-1] if m.get("role")]
    if not brain.is_file() or not kept:
        return None
    conn = sqlite3.connect(f"file:{brain}?mode=ro", uri=True)
    rows = [
        {"role": role, "content": json.loads(content)}
        for role, content in conn.execute(
            "select role, content_json from conversation_messages order by seq"
        )
    ]
    for end in range(len(rows), len(kept) - 1, -1):
        if rows[end - len(kept):end] == kept:
            history = rows[:end]
            return history[-KEEP:] if KEEP > 0 else history
    return None


def _tail_matches(rebuilt: list[dict[str, Any]], recorded: list[dict[str, Any]]) -> bool:
    kept = [m for m in recorded[:-1] if m.get("role")]
    return bool(kept) and rebuilt[-len(kept):] == [
        {"role": m["role"], "content": m["content"]} for m in kept
    ]

pytestmark = pytest.mark.skipif(
    not (DB and BROKER),
    reason="set KASUKABE_REPLAY_DB and KASUKABE_REPLAY_BROKER to run the live replay",
)


def _is_first_call_after_a_record(body: dict[str, Any]) -> bool:
    messages = [m for m in body.get("messages") or [] if m.get("role")]
    if not messages or messages[-1]["role"] != "user":
        return False
    if "tool_result" in json.dumps(messages[-1].get("content")):
        return False
    earlier = [m for m in messages if m["role"] == "assistant"]
    return bool(earlier) and RECORD_HEADING in json.dumps(earlier[-1].get("content"))


def replayable_calls(db: str, samples: int) -> list[dict[str, Any]]:
    """Evenly spaced across the run, so no one hour or resident dominates."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    bodies = []
    for agent_id, created_at, raw, live in conn.execute(
        "select agent_id, created_at, request_body, response_body from llm_calls "
        # created_at, not id: a trace id is a random `trc_<uuid>`.
        "where purpose='auto:agent_turn' and success=1 order by created_at"
    ):
        try:
            body = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not _is_first_call_after_a_record(body):
            continue
        if HOMES:
            history = history_before(agent_id, body["messages"])
            if history is None or not _tail_matches(history, body["messages"]):
                continue
            body = {**body, "messages": history + [body["messages"][-1]]}
        try:
            # What this very call did in the run, so the baseline is the same
            # sample. Underscored, so `arms` never sends it.
            body["_live"] = verdict(json.loads(live))
        except (TypeError, ValueError):
            continue
        bodies.append(body)
    step = max(1, len(bodies) // samples)
    return bodies[::step][:samples]


def _to_engine(message: dict[str, Any]) -> Message:
    content = message.get("content")
    if isinstance(content, list):
        content = [
            TextBlock(block["text"]) if block.get("type") == "text" else block
            for block in content
        ]
    return Message(message["role"], content)


def _to_wire(message: Message) -> dict[str, Any]:
    content = message.content
    if isinstance(content, list):
        content = [
            {"type": "text", "text": block.text} if isinstance(block, TextBlock) else block
            for block in content
        ]
    return {"role": message.role, "content": content}


def served_tools(world_tools_path: str) -> dict[str, dict[str, Any]]:
    """Full tool definitions, by name.

    The trace cap keeps only a tool's NAME once a body passes 64 KB, and
    Bedrock refuses a tool with an empty description - so the recorded
    tools cannot be sent back. The world's are rebuilt by the app's
    `tool_registry_rows` for one resident (written to a JSON file, since
    that code lives in app/) and the engine's three are built here.
    Both arms get the same ones.
    """
    import tempfile

    from src.sim.memory_query import build_search_memory_tool
    from src.sim.memory_stream import build_memory_stream_tool
    from src.sim.session_log import build_session_log_tool

    tools = {t["name"]: t for t in json.loads(Path(world_tools_path).read_text())}
    scratch = Path(tempfile.mkdtemp())
    for tool in (
        build_memory_stream_tool(scratch / "memories.jsonl"),
        build_session_log_tool(scratch / "session.jsonl"),
        build_search_memory_tool(None),
    ):
        tools[tool.name] = {
            "name": tool.name, "description": tool.description, "input_schema": tool.input_schema,
        }
    return tools


TOOLS = os.environ.get("KASUKABE_REPLAY_TOOLS", "")
_CATALOGUE: dict[str, dict[str, Any]] = {}


def _catalogue() -> dict[str, dict[str, Any]]:
    if not _CATALOGUE:
        _CATALOGUE.update(served_tools(TOOLS))
    return _CATALOGUE


def arms(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = {k: v for k, v in body.items() if not k.startswith("_")}
    base["stream"] = False
    if TOOLS:
        catalogue = _catalogue()
        base["tools"] = [catalogue[t["name"]] for t in body.get("tools") or [] if t["name"] in catalogue]
    recorded = [m for m in body["messages"] if m.get("role")]
    wrapped = speak_as_resident(object())
    rewritten = wrapped._for_the_wire([_to_engine(m) for m in recorded])
    return {
        "as_recorded": {**base, "messages": recorded},
        "world_voice": {**base, "messages": [_to_wire(m) for m in rewritten]},
    }


def verdict(response: dict[str, Any]) -> dict[str, bool]:
    blocks = response.get("content") or []
    text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return {
        "called_a_tool": any(b.get("type") == "tool_use" for b in blocks),
        "copied_a_record": bool(COPIED_RECORD.search(text)) or RECORD_HEADING in text,
    }


async def replay(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    import httpx

    tally = {arm: {"calls": 0, "called_a_tool": 0, "copied_a_record": 0, "errors": 0}
             for arm in ("live_in_the_run", "as_recorded", "world_voice")}
    for body in bodies:
        row = tally["live_in_the_run"]
        row["calls"] += 1
        for key, hit in body["_live"].items():
            row[key] += hit
    gate = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(base_url=BROKER, timeout=180) as client:
        async def one(arm: str, body: dict[str, Any]) -> None:
            async with gate:
                reply = await client.post(
                    "/v1/messages", json=body,
                    headers={"Authorization": "Bearer eval-record-voice"},
                )
            row = tally[arm]
            if reply.status_code != 200:
                row["errors"] += 1
                print(f"{arm}: HTTP {reply.status_code} {reply.text[:200]}", file=sys.stderr)
                return
            row["calls"] += 1
            for key, hit in verdict(reply.json()).items():
                row[key] += hit

        await asyncio.gather(*(
            one(arm, request)
            for body in bodies
            for arm, request in arms(body).items()
        ))
    return tally


def test_a_record_in_the_worlds_voice_is_copied_less() -> None:
    bodies = replayable_calls(DB, SAMPLES)
    assert bodies, f"no first-of-turn calls after a record in {DB}"
    tally = asyncio.run(replay(bodies))
    print(json.dumps(tally, indent=2))
    before, after = tally["as_recorded"], tally["world_voice"]
    assert after["calls"] and before["calls"], tally
    assert after["copied_a_record"] < before["copied_a_record"], tally
    assert after["called_a_tool"] >= before["called_a_tool"], tally


if __name__ == "__main__":
    chosen = replayable_calls(DB, SAMPLES)
    print(f"replaying {len(chosen)} calls x 2 arms against {BROKER}")
    result = asyncio.run(replay(chosen))
    print(json.dumps(result, indent=2))
    Path(os.environ.get("KASUKABE_REPLAY_OUT", "replay-records-in-world-voice.json")).write_text(
        json.dumps({"samples": len(chosen), "tally": result}, indent=2)
    )
