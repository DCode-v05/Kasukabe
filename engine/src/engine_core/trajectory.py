"""Trajectory recording — audit trail for task executions.

Each task execution produces one trajectory row in SQLite.
Fire-and-forget: failures are silently swallowed so they never
block the execution pipeline.

Double-writes: submit() also inserts into lc_queue so the Learning
Centre can process trajectories in batch. lc_queue failure is non-fatal.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.engine_core.schema import Charter


async def flush_wm_to_lcq(
    memory: Any,
    thread_id: str,
    db: Any,
    *,
    charter: "Charter | None" = None,
    duration_ms: int = 0,
    status: str = "completed",
    final_output: dict | None = None,
) -> int | None:
    """Flush task-scoped Working Memory to LC queue as one atomic SQLite INSERT.

    Reads all WM items for thread_id, reassembles them into a JSON structure,
    and inserts a single row into lc_queue. Returns the queue row id, or
    None on failure. Failures are non-fatal.
    """
    if db is None:
        return None

    try:
        items = await memory.read(thread_id)
    except Exception as e:
        logger.warning("WM read failed for task %s: %s", thread_id, e)
        return None

    from src.memory.working_memory import EntryType

    user_inputs: list[dict] = []
    node_results: dict[str, str] = {}
    charter_json: dict = {}
    execution_trace: dict | None = None
    agent_outputs: list[str] = []

    for item in items:
        if item.type == EntryType.MESSAGE_USER:
            user_inputs.append({"text": item.content, "timestamp": item.timestamp})
        elif item.type == EntryType.CHECKPOINT:
            try:
                cp = json.loads(item.content)
                node_results[cp.get("node_id", "")] = cp.get("output", "")
            except (json.JSONDecodeError, TypeError):
                pass
        elif item.type == EntryType.SYSTEM_EVENT:
            try:
                parsed = json.loads(item.content)
                if isinstance(parsed, dict):
                    if "charter_id" in parsed and charter_json is None:
                        charter_json = parsed
                    elif "total_duration_ms" in parsed and execution_trace is None:
                        execution_trace = parsed
            except (json.JSONDecodeError, TypeError):
                pass
        elif item.type == EntryType.MESSAGE_ASSISTANT:
            agent_outputs.append(item.content)

    if charter is not None:
        effective_charter_json = json.loads(charter.model_dump_json())
        effective_charter_id = charter.charter_id
        effective_thread_id = charter.thread_id
        effective_user_query = charter.user_query
        effective_version = charter.charter_version
    else:
        effective_charter_json = charter_json
        effective_charter_id = thread_id
        effective_thread_id = thread_id
        effective_user_query = user_inputs[0]["text"] if user_inputs else ""
        effective_version = 0

    if not user_inputs:
        user_inputs = [{"text": effective_user_query, "timestamp": ""}]

    if final_output is None:
        if agent_outputs:
            final_output = {"type": "text", "value": agent_outputs[-1]}
        elif node_results:
            final_output = {"type": "text", "value": "\n".join(node_results.values())}
        else:
            final_output = {"type": "text", "value": ""}

    metadata: dict[str, Any] = {
        "total_nodes": len(node_results),
        "duration_ms": duration_ms,
        "task_completed": status == "completed",
        "s1_or_s2": "",
    }
    if execution_trace and execution_trace.get("nodes"):
        metadata["s1_or_s2"] = execution_trace.get("model_used", "")
        metadata["agents_used"] = list(
            {n["agent_name"] for n in execution_trace["nodes"] if n.get("agent_name")}
        )
        metadata["total_tool_calls"] = sum(
            len(n.get("tool_calls", [])) for n in execution_trace["nodes"]
        )
        metadata["total_input_tokens"] = sum(
            (n.get("agent_loop") or {}).get("total_input_tokens", 0)
            for n in execution_trace["nodes"]
        )
        metadata["total_output_tokens"] = sum(
            (n.get("agent_loop") or {}).get("total_output_tokens", 0)
            for n in execution_trace["nodes"]
        )

    wm_items = [
        {
            "type": item.type.value if hasattr(item.type, "value") else str(item.type),
            "content": item.content[:2000],
            "timestamp": item.timestamp,
        }
        for item in items
    ]

    lc_trajectory = {
        "charter_id": effective_charter_id,
        "thread_id": effective_thread_id,
        "user_query": effective_user_query,
        "charter_json": effective_charter_json,
        "node_results": node_results,
        "status": status,
        "duration_ms": duration_ms,
        "user_inputs": user_inputs,
        "charter": {
            "version": effective_version,
            "json": effective_charter_json,
        },
        "execution_trace": execution_trace,
        "final_output": final_output,
        "user_feedback": {"type": "none"},
        "metadata": metadata,
        "wm_items": wm_items,
    }

    try:
        row = await db.fetchrow(
            "INSERT INTO lc_queue (trajectory) VALUES (?) RETURNING id",
            (json.dumps(lc_trajectory),),
        )
        await db.commit()
        return row["id"] if row else None
    except Exception as e:
        logger.warning("LC queue insert failed for task %s: %s", thread_id, e)
        return None


async def incremental_flush(
    memory: Any,
    thread_id: str,
    db: Any,
    *,
    watermark: int = 0,
    conversation_summary: str = "",
    status: str = "active",
) -> int:
    """Flush WM entries since watermark to LC queue as a trajectory slice.

    Returns new watermark (index of last flushed entry). Returns the
    original watermark if nothing to flush or on failure.
    """
    if db is None:
        return watermark

    try:
        items = await memory.read(thread_id)
    except Exception as e:
        logger.warning("incremental_flush: WM read failed for %s: %s", thread_id, e)
        return watermark

    # Only items after the watermark
    new_items = items[watermark:]
    if not new_items:
        return watermark

    wm_items = [
        {
            "type": item.type.value if hasattr(item.type, "value") else str(item.type),
            "content": item.content[:2000],
            "timestamp": item.timestamp,
        }
        for item in new_items
    ]

    slice_number = watermark // max(len(new_items), 1)
    lc_trajectory = {
        "charter_id": thread_id,
        "thread_id": thread_id,
        "user_query": conversation_summary,
        "charter_json": {},
        "node_results": {},
        "status": status,
        "duration_ms": 0,
        "user_inputs": [],
        "charter": {"version": 0, "json": {}},
        "execution_trace": None,
        "final_output": {"type": "text", "value": ""},
        "user_feedback": {"type": "none"},
        "metadata": {
            "incremental": True,
            "slice_number": slice_number,
            "conversation_summary": conversation_summary,
            "entries_in_slice": len(new_items),
        },
        "wm_items": wm_items,
    }

    try:
        await db.execute(
            "INSERT INTO lc_queue (trajectory) VALUES (?)",
            (json.dumps(lc_trajectory),),
        )
        await db.commit()
        new_watermark = watermark + len(new_items)
        logger.info(
            "Incremental flush for %s: %d entries (watermark %d → %d)",
            thread_id,
            len(new_items),
            watermark,
            new_watermark,
        )
        return new_watermark
    except Exception as e:
        logger.warning("incremental_flush: lc_queue insert failed for %s: %s", thread_id, e)
        return watermark


async def record_feedback(
    db: Any,
    thread_id: str,
    feedback_type: str,
    correction_text: str | None = None,
) -> None:
    """Update most recent unprocessed lc_queue entry for this task with feedback."""
    if db is None:
        return

    try:
        # Find the most recent unprocessed entry for this task
        row = await db.fetchrow(
            """
            SELECT id, trajectory FROM lc_queue
            WHERE json_extract(trajectory, '$.thread_id') = ?
              AND processed = 0
            ORDER BY submitted_at DESC
            LIMIT 1
            """,
            (thread_id,),
        )
        if row is None:
            return

        trajectory = json.loads(row["trajectory"])
        trajectory["user_feedback"] = {
            "type": feedback_type,
            "correction_text": correction_text or "",
        }
        await db.execute(
            "UPDATE lc_queue SET trajectory = ? WHERE id = ?",
            (json.dumps(trajectory), row["id"]),
        )
        await db.commit()
    except Exception as e:
        logger.warning("Feedback recording failed for task %s: %s", thread_id, e)
