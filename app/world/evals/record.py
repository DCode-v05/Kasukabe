"""Where an evaluation result goes, so it outlives the terminal it printed in.

`EVAL-07`. The L1 harness prints a scorecard and returns an exit code.
The only record of any L1 run that exists anywhere is five samples quoted
in `docs/runs/run-22-report.md`. `eval_metrics` has been in the schema
since it was written and `wiring.py` lists it among the tables nothing
writes.

So every evaluation this project has run is gone: not summarised, not
superseded - gone, except where somebody happened to paste a number into
a markdown file by hand. Two runs cannot be compared, a regression cannot
be noticed, and the roadmap's gates are re-argued from memory each time.

`EVAL-09` is the same defect one step earlier: `judges.py` and
`character.py` expose the functions that would produce those numbers and
neither has a `__main__`, so nothing has ever called them. A gate that
has never been sampled is not a gate that failed - it is a gate nobody
has looked at, and two documents describe both modules as "built".

WHAT A ROW MEANS HERE. One row per metric per run, carrying enough to
compare two runs honestly: the value, the sample size, and what produced
it. Without the sample size a scorecard is uninterpretable - "3 of 5" and
"300 of 500" are the same fraction and not the same evidence - which is
exactly the trap `run-22-report.md`'s five samples set.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

TRACKS = (
    "political_science",
    "economics",
    "sociology",
    "religion",
    "machine_psychology",
    "business_readiness",
    "mas_engineering",
)


def record_metric(
    db_path: str | Path,
    *,
    track: str,
    metric_name: str,
    metric_value: float | None,
    payload: dict[str, Any],
    tick: int = 0,
    agent_id: str | None = None,
    ablation_arm: str = "full",
    source: str = "batch_job",
) -> bool:
    """One `eval_metrics` row. Returns whether it landed.

    Returns rather than raises: an evaluation that computed a real number
    and could not store it should still print the number, and the caller
    decides whether that is fatal. What it must never do is store nothing
    and say nothing, which is the state this function exists to end.

    `payload` carries what makes the value interpretable later - the
    sample size above all. A metric_value with no sample size is a number
    two runs cannot be compared on.
    """
    if track not in TRACKS:
        raise ValueError(f"track must be one of {TRACKS}, got {track!r}")
    if "samples" not in payload:
        raise ValueError(
            "payload must carry `samples`: a score with no sample size "
            "cannot be compared against another run, which is how "
            "run-22-report.md's five samples came to be quoted as a result"
        )

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return False
    try:
        conn.execute(
            "insert into eval_metrics "
            "(track, metric_name, metric_value, metric_payload, "
            " ablation_arm, agent_id, source, tick) "
            "values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                track, metric_name, metric_value,
                json.dumps(payload, sort_keys=True, default=str),
                ablation_arm, agent_id, source, tick,
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def provenance() -> dict[str, Any]:
    """What produced a number, for the payload.

    The same reasoning as INFRA-09's run manifest, at a smaller scale: a
    score is only comparable against another score from the same code.
    A missing commit is recorded as null rather than guessed.
    """
    from world.run_manifest import git_commit

    app_root = Path(__file__).resolve().parents[2]
    return {"app_commit": git_commit(app_root)}
