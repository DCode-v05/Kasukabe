"""Before $60 and most of a shared box's hours, one command that answers.

A smoke run is only worth its money if a clear answer comes out of it, and "it
seems to be working" is not one. These are the five questions worth paying to
answer - are the residents all taking turns, is the model the one being paid
for, are the cameras filming, is the report refreshing, is anything erroring -
and each has a number behind it.

The checker must be able to say NO. A health check that passes whatever it is
shown is worse than none, because it converts an unexamined run into a
confident one.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from scripts.check_run_health import main

SCHEMA = """
create table llm_calls (
    id integer primary key, agent_id text, model text, tick integer,
    cost_nano_dollars integer, error_type text
);
"""


def _trace(path: Path, residents: int, model: str = "gpt-6-luna", errors: int = 0) -> None:
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany(
        "insert into llm_calls (agent_id, model, tick, cost_nano_dollars, error_type) "
        "values (?,?,?,?,?)",
        [(f"n{i:02d}", model, 324 + i, 10_000_000, None) for i in range(1, residents + 1)]
        + [("n01", model, 330, 10_000_000, "UpstreamError") for _ in range(errors)],
    )
    db.commit()
    db.close()


def _run_dir(root: Path, views: int, *, fresh: bool = True, report: bool = True) -> Path:
    out = root / "out"
    for i in range(views):
        frames = out / "video" / f"view{i}" / "frames"
        frames.mkdir(parents=True)
        frame = frames / "frame-0000000.jpg"
        frame.write_bytes(b"x")
        if not fresh:
            old = time.time() - 9999
            import os
            os.utime(frame, (old, old))
    if report:
        (out / "report.md").write_text("# report")
    return out


def _check(tmp: Path, **kw) -> int:
    argv = ["--run", "t", "--db", str(tmp / "t.db"), "--out", str(tmp / "out")]
    for k, v in kw.items():
        flag = "--" + k.replace("_", "-")
        argv += [flag] if v is True else [flag, str(v)]
    return main(argv)


def test_a_healthy_run_passes(tmp_path: Path) -> None:
    _trace(tmp_path / "t.db", residents=50)
    _run_dir(tmp_path, views=6)

    assert _check(tmp_path, residents=50, views=6, model="gpt-6-luna") == 0


def test_a_silent_resident_fails(tmp_path: Path, capsys) -> None:
    """The whole point of counting them. 49 of 50 acting is a stuck agent."""
    _trace(tmp_path / "t.db", residents=49)
    _run_dir(tmp_path, views=6)

    assert _check(tmp_path, residents=50, views=6) == 1
    assert "silent" in capsys.readouterr().out


def test_the_wrong_model_fails(tmp_path: Path, capsys) -> None:
    """Silent and 3.6x more expensive, so it is asked of the trace rather than
    of the settings that were meant to produce it."""
    _trace(tmp_path / "t.db", residents=50, model="moonshotai.kimi-k2.5")
    _run_dir(tmp_path, views=6)

    assert _check(tmp_path, residents=50, views=6, model="gpt-6-luna") == 1
    assert "expected gpt-6-luna" in capsys.readouterr().out


def test_a_second_model_serving_one_call_fails_under_only(tmp_path: Path, capsys) -> None:
    """ONLY_MODEL is enforced in the broker, but the trace is the only place
    it can be CHECKED from outside the process. On trial-day2-20, deepseek
    served 236 calls and $1.43 while Kimi was the chosen model - that is the
    leak this catches, and "is the right model leading" would have called it
    healthy."""
    db = tmp_path / "t.db"
    _trace(db, residents=50, model="gpt-6-luna")
    con = sqlite3.connect(db)
    con.execute(
        "insert into llm_calls (agent_id, model, tick, cost_nano_dollars) values (?,?,?,?)",
        ("n01", "deepseek.v3.2", 330, 1_430_000_000),
    )
    con.commit()
    con.close()
    _run_dir(tmp_path, views=6)

    assert _check(tmp_path, residents=50, views=6, model="gpt-6-luna", only=True) == 1
    out = capsys.readouterr().out
    assert "deepseek.v3.2 served 1 calls" in out
    assert "run excluded" in out


def test_the_same_trace_passes_when_only_is_not_asked_for(tmp_path: Path) -> None:
    """Without --only a second model is overflow working as designed, not a
    fault. The flag is the difference between a preference and a rule."""
    db = tmp_path / "t.db"
    _trace(db, residents=50, model="gpt-6-luna")
    con = sqlite3.connect(db)
    con.execute(
        "insert into llm_calls (agent_id, model, tick, cost_nano_dollars) values (?,?,?,?)",
        ("n01", "deepseek.v3.2", 330, 1_430_000_000),
    )
    con.commit()
    con.close()
    _run_dir(tmp_path, views=6)

    assert _check(tmp_path, residents=50, views=6, model="gpt-6-luna") == 0


def test_a_missing_camera_fails(tmp_path: Path) -> None:
    _trace(tmp_path / "t.db", residents=50)
    _run_dir(tmp_path, views=5)

    assert _check(tmp_path, residents=50, views=6) == 1


def test_a_stalled_camera_fails_even_though_its_frames_exist(tmp_path: Path, capsys) -> None:
    """Six directories with frames in them is not six cameras filming. This is
    the failure that a `ls` would call healthy."""
    _trace(tmp_path / "t.db", residents=50)
    _run_dir(tmp_path, views=6, fresh=False)

    assert _check(tmp_path, residents=50, views=6) == 1
    assert "STALLED" in capsys.readouterr().out


def test_a_stale_report_fails_while_the_run_is_live(tmp_path: Path) -> None:
    """The live report loop dying is invisible otherwise - report.md still
    exists and still reads correctly, it just stops being true."""
    _trace(tmp_path / "t.db", residents=50)
    out = _run_dir(tmp_path, views=6)
    import os
    old = time.time() - 9999
    os.utime(out / "report.md", (old, old))

    assert _check(tmp_path, residents=50, views=6) == 1


def test_a_finished_run_is_not_faulted_for_being_finished(tmp_path: Path) -> None:
    """Stale frames and an unchanging report are what a finished run looks
    like. Judging it by live-run rules would cry wolf on every completed run."""
    _trace(tmp_path / "t.db", residents=50)
    out = _run_dir(tmp_path, views=6, fresh=False)
    for d in (out / "video").iterdir():
        (d / "run.mp4").write_bytes(b"x")

    assert _check(tmp_path, residents=50, views=6, finished=True) == 0


def test_a_finished_run_missing_a_video_fails(tmp_path: Path, capsys) -> None:
    """Once the run is over the mp4 is the deliverable, and this is exactly
    the bug that lost all six of them in the rehearsal."""
    _trace(tmp_path / "t.db", residents=50)
    out = _run_dir(tmp_path, views=6, fresh=False)
    for d in list((out / "video").iterdir())[:4]:
        (d / "run.mp4").write_bytes(b"x")

    assert _check(tmp_path, residents=50, views=6, finished=True) == 1
    assert "4 of 6 have run.mp4" in capsys.readouterr().out


def test_it_never_opens_the_trace_for_writing(tmp_path: Path) -> None:
    """It is pointed at a live, paid run. Read-only is the property that makes
    that safe, exactly as it is for run_report."""
    source = (Path(__file__).resolve().parents[2] / "scripts" / "check_run_health.py").read_text()
    connects = [c for c in source.split("sqlite3.connect(")[1:]]

    assert connects, "no connection found"
    for call in connects:
        assert "mode=ro" in call[:80], "check_run_health opens a trace without mode=ro"


def test_a_missing_trace_is_a_plain_answer(tmp_path: Path) -> None:
    """Asked before the run has started, which is a normal thing to do."""
    assert main(["--run", "nope", "--db", str(tmp_path / "none.db"),
                 "--out", str(tmp_path / "out")]) == 2
