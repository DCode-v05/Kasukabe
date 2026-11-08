"""Keeping prompts small for the length of a run.

Measured on this project, with no groundskeeper running:

    fresh town   30 KB per request     100% dispatch success
    20 minutes   126 KB
    2 hours      552 KB, 49 MB largest   3.3% success
    6 hours      630 KB, 36.5 MB largest 90.9% success, 5.9 GB of trace

Purging the `system_event` backlog by hand once took prompts from 643 KB to
97.5 KB and dispatch from 78.7% to 98.1%. These tests pin the two properties
that make doing it on a timer safe: it deletes only the noise, and one
unreadable engine never stops the others or the run.
"""

from __future__ import annotations

import sqlite3

from world.signal_groundskeeper import MIN_ROWS_TO_SWEEP, sweep_once


def brain(home, noise: int = 0, real: int = 0) -> None:
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "brain.sqlite")
    conn.execute("CREATE TABLE learning_signals (type TEXT, content TEXT)")
    conn.executemany("INSERT INTO learning_signals VALUES ('system_event', 'x')",
                     [() for _ in range(noise)])
    conn.executemany("INSERT INTO learning_signals VALUES ('tool_call', 'real')",
                     [() for _ in range(real)])
    conn.commit()
    conn.close()


def signals(home) -> dict[str, int]:
    conn = sqlite3.connect(home / "brain.sqlite")
    try:
        return dict(conn.execute(
            "SELECT type, COUNT(*) FROM learning_signals GROUP BY type"))
    finally:
        conn.close()


def test_the_noise_goes_and_the_memory_stays(tmp_path):
    """tool_call, tool_result, outcome and event are the agent's memory.
    Only the state-machine chatter is write-only noise - every reader in
    the learning centre already excludes system_event."""
    home = tmp_path / "a0001"
    brain(home, noise=5000, real=300)

    result = sweep_once(tmp_path)

    assert result.rows_deleted == 5000
    assert result.brains_swept == 1
    assert signals(home) == {"tool_call": 300}


def test_a_small_backlog_is_left_alone(tmp_path):
    """A few hundred rows cost nothing in prompt size and an engine
    mid-write does not need the contention."""
    home = tmp_path / "a0001"
    brain(home, noise=MIN_ROWS_TO_SWEEP - 1, real=10)

    result = sweep_once(tmp_path)

    assert result.rows_deleted == 0
    assert result.brains_swept == 0
    assert signals(home)["system_event"] == MIN_ROWS_TO_SWEEP - 1


def test_every_brain_is_swept(tmp_path):
    for i in range(30):
        brain(tmp_path / f"a{i:04d}", noise=1000, real=5)

    result = sweep_once(tmp_path)

    assert result.brains_seen == 30
    assert result.brains_swept == 30
    assert result.rows_deleted == 30_000


def test_one_broken_brain_does_not_stop_the_others(tmp_path):
    """An engine that is locked, missing its table, or mid-creation must
    not cost the other twenty-nine their sweep - and must not take the run
    down either."""
    brain(tmp_path / "a0001", noise=1000)
    (tmp_path / "a0002").mkdir()
    (tmp_path / "a0002" / "brain.sqlite").write_bytes(b"not a database")
    brain(tmp_path / "a0003", noise=1000)

    result = sweep_once(tmp_path)

    assert result.rows_deleted == 2000
    assert result.brains_swept == 2
    assert len(result.errors) == 1
    assert "a0002" in result.errors[0]


def test_a_brain_without_the_table_is_skipped_quietly(tmp_path):
    """An engine that has booted but not yet created its signal store."""
    home = tmp_path / "a0001"
    home.mkdir()
    sqlite3.connect(home / "brain.sqlite").close()

    result = sweep_once(tmp_path)

    assert result.rows_deleted == 0
    assert result.brains_seen == 1


def test_a_missing_home_root_is_not_an_error(tmp_path):
    result = sweep_once(tmp_path / "nothing-here")
    assert result.brains_seen == 0
    assert result.errors == []


def test_a_directory_without_a_brain_is_not_counted(tmp_path):
    (tmp_path / "a0001").mkdir()
    (tmp_path / "a0001" / "persona.txt").write_text("hello", encoding="utf-8")
    assert sweep_once(tmp_path).brains_seen == 0
