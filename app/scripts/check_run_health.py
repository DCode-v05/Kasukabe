#!/usr/bin/env python3
"""Is this run healthy? One command, asked of a run that is still going.

WHY IT EXISTS. A smoke run is only worth its money if a clear answer comes out
of it, and "it seems to be working" is not one. Before committing $60 and most
of a shared box's remaining hours, the questions worth answering are exactly:
are all the residents actually taking turns, is the model the one we are paying
for, are all six cameras filming, is the report really refreshing, and is
anything erroring. Each of those has a number behind it, so each is checked
rather than eyeballed.

Everything here reads. The trace is opened `mode=ro` like run_report's, and
nothing is written anywhere, so this is safe to run against a live run as often
as you like.

Exit code is 0 only if every check passed, so it can gate a decision:

    python scripts/check_run_health.py --run fifty-day2 --residents 50 \
        --model gpt-6-luna --views 6 && echo "safe to continue"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
OK, BAD, MEH = "PASS", "FAIL", "warn"


class Report:
    """Collects one line per check and remembers whether any of them failed."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.failed = False

    def add(self, verdict: str, what: str, detail: str) -> None:
        self.rows.append((verdict, what, detail))
        if verdict == BAD:
            self.failed = True

    def print(self) -> None:
        width = max(len(w) for _, w, _ in self.rows)
        for verdict, what, detail in self.rows:
            mark = {OK: "  ok  ", BAD: " FAIL ", MEH: " warn "}[verdict]
            print(f"[{mark}] {what:<{width}}  {detail}")


def _residents(db: sqlite3.Connection, expected: int, r: Report) -> None:
    """Every resident must have taken a turn.

    Measured on trial-day2-20, all 20 residents had called within 4 ticks of
    the start, so a resident still silent well after that is not slow - it is
    stuck, and the run is only as good as its quietest agent.
    """
    row = db.execute(
        "select count(distinct agent_id) from llm_calls where agent_id is not null"
    ).fetchone()
    seen = row[0] if row else 0
    detail = f"{seen} of {expected} residents have taken a turn"
    if seen >= expected:
        r.add(OK, "residents acting", detail)
    elif seen == 0:
        r.add(BAD, "residents acting", "nobody has taken a turn yet")
    else:
        r.add(BAD, "residents acting", detail + f" - {expected - seen} silent")


def _model(db: sqlite3.Connection, want: str | None, r: Report, only: bool = False) -> None:
    """The lane that is actually serving, not the one that was configured.

    Getting this wrong is silent and costs 3.6x, so it is asked of the trace
    rather than of the settings.
    """
    rows = list(db.execute(
        "select model, count(*), sum(cost_nano_dollars)/1e9 from llm_calls "
        "group by 1 order by 2 desc"
    ))
    if not rows:
        r.add(BAD, "model serving", "no calls recorded yet")
        return
    served = ", ".join(f"{m} {n:,} calls ${c or 0:.2f}" for m, n, c in rows)
    if want is None:
        r.add(OK, "model serving", served)
        return
    if only:
        strays = [(m, n, c) for m, n, c in rows if m != want]
        if strays:
            cost = sum(c or 0 for _, _, c in strays)
            r.add(BAD, "model serving",
                  f"ONLY {want} was allowed, but " +
                  ", ".join(f"{m} served {n:,} calls (${c or 0:.2f})" for m, n, c in strays) +
                  f" - ${cost:.2f} on models this run excluded")
        else:
            r.add(OK, "model serving", served + " - nothing else served a call")
        return
    r.add(OK if rows[0][0] == want else BAD, "model serving",
          served if rows[0][0] == want else f"expected {want} to lead; got {served}")


def _spend(db: sqlite3.Connection, cap: float | None, r: Report) -> None:
    row = db.execute("select sum(cost_nano_dollars)/1e9, count(*) from llm_calls").fetchone()
    spent, calls = (row[0] or 0.0), (row[1] or 0)
    detail = f"${spent:.2f} over {calls:,} calls"
    if cap:
        detail += f" of ${cap:.2f} ({spent / cap * 100:.0f}%)"
    r.add(OK, "spend", detail)


def _errors(db: sqlite3.Connection, r: Report) -> None:
    """A few upstream errors are normal - the pool retries and re-routes. A
    high rate means the run is paying for failures."""
    total = db.execute("select count(*) from llm_calls").fetchone()[0] or 0
    if not total:
        r.add(MEH, "call errors", "no calls yet")
        return
    rows = list(db.execute(
        "select error_type, count(*) from llm_calls where error_type is not null "
        "group by 1 order by 2 desc"
    ))
    bad = sum(n for _, n in rows)
    rate = bad / total * 100
    detail = f"{bad} of {total:,} calls ({rate:.1f}%)"
    if rows:
        detail += " - " + ", ".join(f"{t} x{n}" for t, n in rows[:3])
    r.add(OK if rate < 5 else BAD, "call errors", detail)


def _ticks(db: sqlite3.Connection, r: Report) -> None:
    row = db.execute(
        "select min(tick), max(tick), count(distinct tick) from llm_calls where tick is not null"
    ).fetchone()
    if not row or row[0] is None:
        r.add(MEH, "ticks", "no ticks recorded yet")
        return
    lo, hi, n = row
    r.add(OK, "ticks", f"{lo} -> {hi} ({n} with activity), game-day {hi // 144}")


def _cameras(out: Path, views: int, stale_s: float, r: Report, finished: bool = False) -> None:
    """Six directories existing is not six cameras filming. What matters is
    that each has frames and that the newest one is recent."""
    video = out / "video"
    if not video.is_dir():
        r.add(BAD, "cameras", f"no video directory at {video}")
        return
    dirs = sorted(d for d in video.iterdir() if d.is_dir())
    if not dirs:
        r.add(BAD, "cameras", "video directory is empty - nothing is filming")
        return

    now = time.time()
    stalled, fine = [], []
    for d in dirs:
        frames = sorted((d / "frames").glob("frame-*.jpg"))
        if not frames:
            stalled.append(f"{d.name}: no frames")
            continue
        age = now - frames[-1].stat().st_mtime
        ok = finished or age <= stale_s
        (fine if ok else stalled).append(
            f"{d.name}: {len(frames)} frames" + ("" if finished else f", {age:.0f}s old")
        )

    verb = "with frames" if finished else "filming"
    detail = f"{len(fine)} of {len(dirs)} {verb}"
    if len(dirs) != views:
        detail += f" (expected {views} views)"
    if stalled:
        detail += " | STALLED: " + "; ".join(stalled)
    r.add(BAD if (stalled or len(dirs) != views) else OK, "cameras", detail)

    if finished:
        made = [d.name for d in dirs if (d / "run.mp4").exists()]
        r.add(
            OK if len(made) == len(dirs) else BAD, "videos encoded",
            f"{len(made)} of {len(dirs)} have run.mp4"
            + ("" if len(made) == len(dirs)
               else " - rebuild the rest with --encode-only"),
        )


def _report(out: Path, every: float, r: Report, finished: bool = False) -> None:
    """The live report should be younger than two refresh intervals. Older
    than that and the loop has died, and the run is no longer answerable
    while it runs."""
    path = out / "report.md"
    if not path.exists():
        r.add(BAD, "live report", f"no report at {path} yet")
        return
    age = time.time() - path.stat().st_mtime
    size = path.stat().st_size
    if finished:
        r.add(OK, "report", f"{size:,} bytes")
        return
    detail = f"{size:,} bytes, written {age:.0f}s ago (refresh every {every:.0f}s)"
    r.add(OK if age <= every * 2 + 60 else BAD, "live report", detail)


def _sessions(out: Path, r: Report) -> None:
    """Only written at the end; absent mid-run is correct, not a problem."""
    sessions = sorted((out / "sessions").glob("*.jsonl")) if (out / "sessions").is_dir() else []
    if sessions:
        r.add(OK, "session logs", f"{len(sessions)} kept")
    else:
        r.add(MEH, "session logs", "not copied yet - written when the run stops")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="the run name, e.g. fifty-day2")
    ap.add_argument("--residents", type=int, default=50, help="how many there should be")
    ap.add_argument("--model", default=None, help="the model that should be leading, e.g. gpt-6-luna")
    ap.add_argument("--only", action="store_true",
                    help="--model is the ONLY model allowed: any call served by "
                         "another is a failure, not a preference that lost")
    ap.add_argument("--views", type=int, default=6, help="how many cameras there should be")
    ap.add_argument("--cap", type=float, default=None, help="the budget, to show progress against")
    ap.add_argument("--report-every", type=float, default=120.0)
    ap.add_argument("--camera-stale", type=float, default=180.0,
                    help="a camera whose newest frame is older than this has stalled")
    ap.add_argument("--db", default=None, help="trace path (default data/<run>.db)")
    ap.add_argument("--out", default=None, help="run folder (default ../docs/runs/series/<run>)")
    ap.add_argument("--finished", action="store_true",
                    help="the run has ended: stop treating stale frames and an "
                         "unchanging report as faults, and require the mp4s")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else APP / "data" / f"{args.run}.db"
    out = Path(args.out) if args.out else APP.parent / "docs" / "runs" / "series" / args.run

    if not db_path.exists():
        print(f"no trace at {db_path} - has the run started?")
        return 2

    r = Report()
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        _residents(db, args.residents, r)
        _ticks(db, r)
        _model(db, args.model, r, args.only)
        _spend(db, args.cap, r)
        _errors(db, r)
    finally:
        db.close()
    _cameras(out, args.views, args.camera_stale, r, args.finished)
    _report(out, args.report_every, r, args.finished)
    _sessions(out, r)

    print(f"\n{args.run} - {db_path}\n")
    r.print()
    print()
    if r.failed:
        print("NOT HEALTHY - something above says FAIL.")
        return 1
    print("healthy on every check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
