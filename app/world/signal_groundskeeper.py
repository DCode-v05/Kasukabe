"""Keeps agent prompts from growing without bound while a run is going.

The problem, measured
---------------------
Each engine keeps its own learning-centre signal store in its
`SEPTEMBER_HOME`. `engine/src/learning_centre/pipeline.py` builds one
Bedrock request per batch out of every unprocessed signal in that store,
trimming each signal's *content* to 300 characters but capping neither the
*number* of signals nor the `system_event` type - while the two sibling call
sites in the same file both exclude `system_event`.

Nothing marks a signal processed when the call fails, so the batch that was
too large to send is retried next tick with more signals in it. That is a
poison queue, and it behaves exactly like one:

    fresh town      30 KB per request      100% dispatch success
    20 minutes      126 KB
    2 hours         552 KB, 49 MB largest  3.3% success
    6 hours         630 KB, 36.5 MB largest 90.9% success, 5.9 GB of trace

One request was measured carrying 118,179 replayed records, 36,673 of them
bare `thread_state_change` state-machine noise.

Why this file exists rather than a two-line fix
-----------------------------------------------
The fix belongs in `pipeline.py`: exclude `system_event` the way its
siblings do, and cap the list. `engine/src/learning_centre/` is frozen under
ROADMAP Law 1, so that is written up in docs/TEST_RUN.md rather than
patched.

This is the part that can be done without touching frozen code. The signal
rows live in each agent's own SQLite database - the agent's data, not the
engine's logic - and every consumer in the learning centre already excludes
`system_event`. They are write-only noise from the prompt's point of view.
Deleting them steals nothing from anybody.

Purging the backlog by hand once took prompts from 643 KB to 97.5 KB and
dispatch from 78.7% to 98.1%. This runs that purge on a timer so a five-hour
run ends the way it started instead of drowning slowly.

What it deliberately does not touch
-----------------------------------
`episodes`, `knowledge_store`, `social_graph_*`, `working_memory_log`, and
every non-`system_event` signal - `tool_call`, `tool_result`, `outcome`,
`event`. Those are the agent's memory. Only the state-machine chatter goes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("kasukabe.groundskeeper")

SWEEP_INTERVAL_S = float(os.environ.get("KASUKABE_GROUNDSKEEPER_INTERVAL_S", "300"))

MIN_ROWS_TO_SWEEP = int(os.environ.get("KASUKABE_GROUNDSKEEPER_MIN_ROWS", "200"))

NOISE_TYPE = "system_event"


@dataclass
class SweepResult:
    swept_at: float = 0.0
    brains_seen: int = 0
    brains_swept: int = 0
    rows_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.rows_deleted} noise rows from {self.brains_swept}/"
            f"{self.brains_seen} brains"
            + (f" · {len(self.errors)} error(s)" if self.errors else "")
        )


def sweep_once(home_root: str | Path) -> SweepResult:
    """Delete the `system_event` backlog from every agent brain under root.

    Never raises. A brain that is locked, missing its table, or mid-creation
    is skipped and recorded - one unreadable engine must not stop the other
    twenty-nine from being swept, and it must not take the run down either.
    """
    result = SweepResult(swept_at=time.time())
    root = Path(home_root).expanduser()
    if not root.is_dir():
        return result

    for home in sorted(root.iterdir()):
        brain = home / "brain.sqlite"
        if not home.is_dir() or not brain.is_file():
            continue
        result.brains_seen += 1
        try:
            conn = sqlite3.connect(brain, timeout=2.0)
            try:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM learning_signals WHERE type = ?",
                    (NOISE_TYPE,),
                ).fetchone()[0]
                if pending < MIN_ROWS_TO_SWEEP:
                    continue
                conn.execute(
                    "DELETE FROM learning_signals WHERE type = ?", (NOISE_TYPE,)
                )
                conn.commit()
                result.rows_deleted += pending
                result.brains_swept += 1
            finally:
                conn.close()
        except sqlite3.Error as exc:
            result.errors.append(f"{home.name}: {exc}")
    return result


class SignalGroundskeeper:
    """Runs sweep_once on a timer for the length of a run."""

    def __init__(self, home_root: str | Path, interval_s: float = SWEEP_INTERVAL_S) -> None:
        self.home_root = home_root
        self.interval_s = interval_s
        self.last: SweepResult | None = None
        self.total_deleted = 0
        self.sweeps = 0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="signal-groundskeeper")
            logger.info(
                "groundskeeper started: sweeping %s every %.0fs",
                self.home_root, self.interval_s,
            )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                result = await asyncio.to_thread(sweep_once, self.home_root)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - must never end the run
                logger.warning("groundskeeper sweep raised: %s", exc)
                continue
            self.last = result
            self.sweeps += 1
            self.total_deleted += result.rows_deleted
            if result.rows_deleted:
                logger.info("groundskeeper swept %s", result.summary())
            for err in result.errors[:3]:
                logger.debug("groundskeeper skipped %s", err)

    def status(self) -> dict[str, object]:
        """For GET /world/prompt-health, so the panel can show it working."""
        return {
            "sweeps": self.sweeps,
            "total_rows_deleted": self.total_deleted,
            "interval_s": self.interval_s,
            "last_swept_at": self.last.swept_at if self.last else None,
            "last_summary": self.last.summary() if self.last else None,
        }
