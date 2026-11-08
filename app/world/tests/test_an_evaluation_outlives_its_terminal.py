"""The character gates can be run, and their result is kept.

`EVAL-09`. `character.py` exposes `scan_turns`, `name_exchanges` and
`render` and has no `__main__`; `judges.py` is the same. Neither is
imported anywhere but its own test. So P1 (orchestration leaks), P2
(first-person leaks) and P3 (does a resident say its own name when asked)
have never been computed on any run - while
`STAKES_TIME_COMPOUNDING.md` section 6 calls both modules "built" and
`SYSTEM_REVIEW.md` section 2.6 lists them as existing evaluation.

The T1 gate is "zero orchestration leaks in 500 sampled turns". It has
never been sampled. That is not failing it and it is not passing it, and
both documents read as the latter.

`EVAL-07` is the other half. The L1 harness prints a scorecard and
returns an exit code, so every evaluation ever run is gone except where
somebody pasted a number into a markdown file by hand - the only
surviving L1 record anywhere is five samples in `run-22-report.md`.
`eval_metrics` has been in the schema since it was written and
`wiring.py` lists it among the tables nothing writes.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]
SCHEMA = APP / "supabase" / "sqlite_schema.sql"

CLEAN_TURN = {"tick": 2, "reasoning": "walk to the mill", "output": "off I go"}
LEAKY_TURN = {
    "tick": 1,
    "reasoning": "I should ask for a request_plan first",
    "output": "hello",
}


def _home(tmp_path: Path, *turns: dict) -> Path:
    home = tmp_path / "homes" / "n01"
    home.mkdir(parents=True, exist_ok=True)
    (home / "session_log.jsonl").write_text(
        "\n".join(json.dumps(turn) for turn in turns), encoding="utf-8"
    )
    return tmp_path / "homes"


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "trace.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return path


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "world.evals", *args],
        capture_output=True, text=True, cwd=APP, timeout=300,
    )


def _metrics(db: Path) -> dict[str, float | None]:
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("select metric_name, metric_value from eval_metrics"))
    finally:
        conn.close()


def test_there_is_something_to_run(tmp_path: Path) -> None:
    """The finding at its simplest: no entry point existed at all."""
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    assert "character" in result.stdout


def test_it_finds_the_leak_the_gate_is_about(tmp_path: Path) -> None:
    home = _home(tmp_path, LEAKY_TURN, CLEAN_TURN)
    result = _run("character", str(home))

    assert "P1 orchestration leak   FAIL" in result.stdout, result.stdout
    assert "request_plan" in result.stdout, (
        "EVAL-09: the gate failed and did not quote the sentence that "
        "failed it - a count with no example cannot be argued with, and "
        "the fix is always a change to the text that produced it"
    )
    assert result.returncode == 1


def test_a_clean_run_passes(tmp_path: Path) -> None:
    home = _home(tmp_path, CLEAN_TURN)
    result = _run("character", str(home))
    assert "P1 orchestration leak   PASS" in result.stdout
    assert result.returncode == 0


def test_the_result_is_written_down(tmp_path: Path) -> None:
    """EVAL-07. Without this the number exists only in the terminal that
    printed it, which is why the only surviving L1 record anywhere is a
    five-sample paste in a markdown file."""
    home = _home(tmp_path, LEAKY_TURN, CLEAN_TURN)
    db = _db(tmp_path)

    _run("character", str(home), "--db", str(db))

    metrics = _metrics(db)
    assert metrics.get("p1_orchestration_leaks") == 1.0, metrics
    assert metrics.get("p2_first_person_leaks") == 0.0


def test_the_sample_size_is_stored_with_the_score(tmp_path: Path) -> None:
    """"3 of 5" and "300 of 500" are the same fraction and not the same
    evidence. A row without the denominator cannot be compared against
    another run - the exact trap run-22-report.md's five samples set."""
    home = _home(tmp_path, LEAKY_TURN, CLEAN_TURN)
    db = _db(tmp_path)
    _run("character", str(home), "--db", str(db))

    conn = sqlite3.connect(db)
    try:
        payload = json.loads(
            conn.execute(
                "select metric_payload from eval_metrics "
                "where metric_name = 'p1_orchestration_leaks'"
            ).fetchone()[0]
        )
    finally:
        conn.close()
    assert payload["samples"] == 2
    assert "app_commit" in payload, (
        "a score is only comparable against another score from the same "
        "code, and nothing records which code this was"
    )


def test_a_score_with_no_sample_size_is_refused() -> None:
    from world.evals.record import record_metric

    with pytest.raises(ValueError, match="samples"):
        record_metric(
            ":memory:", track="machine_psychology", metric_name="x",
            metric_value=1.0, payload={},
        )


def test_no_logs_is_not_a_pass(tmp_path: Path) -> None:
    """The whole finding in one case: a gate nobody sampled must not
    report success. Two documents already describe these as built."""
    result = _run("character", str(tmp_path / "nothing-here"))
    assert result.returncode == 2, (
        "EVAL-09: an empty sample exits 0, so a run that evaluated nothing "
        "is indistinguishable from one that passed"
    )
    assert "not a pass" in result.stdout


def test_running_without_a_db_says_the_result_is_lost(tmp_path: Path) -> None:
    home = _home(tmp_path, CLEAN_TURN)
    result = _run("character", str(home))
    assert "EVAL-07" in result.stdout


def test_the_judges_driver_says_why_it_is_absent() -> None:
    """A stub that pretended would be worse than nothing. Judges cost a
    model call per sample against a model nobody has chosen, and judge 3.3
    reads a field Build Spec 14 F1 records as always empty."""
    result = _run("judges")
    assert result.returncode == 2
    assert "no judge model" in result.stdout
    assert "reasoning" in result.stdout
