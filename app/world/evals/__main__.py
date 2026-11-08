"""Run the character gates over a completed run, and write the result down.

    uv run python -m world.evals character <session_home_root> [--db PATH]

`EVAL-09`. `character.py` exposes `scan_turns`, `name_exchanges` and
`render`, and has no `__main__`. `judges.py` is the same. Neither is
imported anywhere but its own test, so P1, P2 and P3 have never been
computed on any run this project has made - while
`STAKES_TIME_COMPOUNDING.md` section 6 calls both modules "built" and
`SYSTEM_REVIEW.md` section 2.6 lists them as existing evaluation.

The T1 gate is "zero orchestration leaks in 500 sampled turns". It has
never been sampled. That is not the same as failing it, and it is not the
same as passing it, and the two documents above read as the latter.

`EVAL-07` is why this also writes: the L1 harness prints a scorecard and
returns an exit code, so every evaluation ever run is gone except where
somebody pasted a number into markdown. `eval_metrics` has been in the
schema since it was written and nothing has ever inserted into it.

WHY ONLY `character` AND NOT `judges` YET. `character` is deterministic -
a regex that found `request_plan` in a resident's reasoning has not
formed an opinion, so it can run here, now, over an existing session log,
with no model calls and no money. `judges` calls a model per sample; it
needs a budget and a decision about which model grades the run, and
building a driver that quietly spends is worse than having none. Its
entry point is stubbed below and says exactly that rather than pretending
the gap is closed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _turns_from(home_root: Path) -> list[dict[str, Any]]:
    """Every turn in every engine's session log under `home_root`.

    `session_log.jsonl` is append-only and is never cleared, so one file
    holds every run that machine has done - `run_report.py`'s own comment
    records n02's holding 59 turns across three days when 15 belonged to
    the run being reported. This reads them ALL and says how many, rather
    than silently reporting somebody else's run: filtering by time would
    need a run boundary the log does not carry.
    """
    turns: list[dict[str, Any]] = []
    for log in sorted(home_root.glob("*/session_log.jsonl")):
        agent_id = log.parent.name
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            row.setdefault("agent_id", agent_id)
            turns.append(row)
    return turns


def run_character(home_root: Path, db: str | None) -> int:
    from world.evals.character import render, scan_turns
    from world.evals.record import provenance, record_metric

    turns = _turns_from(home_root)
    if not turns:
        print(
            f"no session logs under {home_root} - nothing to sample.\n"
            "This is not a pass: the gates below were not evaluated."
        )
        return 2

    report = scan_turns(turns)
    print(render(report))

    if db is None:
        print(
            "\nNot recorded: no --db given, so this result exists only in "
            "this terminal (EVAL-07)."
        )
        return 0 if (report.p1_passes and report.p2_passes) else 1

    payload = {"samples": report.sampled, **provenance()}
    written = 0
    for name, value, extra in (
        ("p1_orchestration_leaks", float(len(report.orchestration)),
         {"occurrences": report.orchestration[:20]}),
        ("p2_first_person_leaks", float(len(report.first_person)),
         {"occurrences": report.first_person[:20]}),
        ("p3_name_answer_rate", report.p3_rate,
         {"asked": report.name_asked, "answered": report.name_answered}),
    ):
        if record_metric(
            db, track="machine_psychology", metric_name=name,
            metric_value=value, payload={**payload, **extra},
        ):
            written += 1

    print(f"\nRecorded {written} of 3 metrics in {db}.")
    if written < 3:
        print("Some did not land - the numbers above are still the numbers.")
    return 0 if (report.p1_passes and report.p2_passes) else 1


def run_judges(_args: argparse.Namespace) -> int:
    """Deliberately not implemented, and saying so.

    EVAL-09 asks for `python -m world.evals.judges <db> --persona-dir ...
    --sample 50`. Building it is a few lines; running it spends money, one
    model call per sampled turn, against a judge model nobody has chosen.
    A driver that quietly spends is worse than no driver, and a stub that
    pretends is worse than both.

    Judge 3.3 has a second problem worth settling first: it reads
    `reasoning`, and Build Spec section 14 F1 records that field as empty
    in every session log ever written. As it stands it would score
    outputs while reporting on reasoning.
    """
    print(
        "world.evals.judges has no driver yet, on purpose.\n\n"
        "  * it costs one model call per sampled turn, and no judge model\n"
        "    has been chosen or budgeted;\n"
        "  * judge 3.3 reads `reasoning`, which Build Spec 14 F1 records as\n"
        "    empty in every session log - it would score outputs and call\n"
        "    them reasoning.\n\n"
        "Both are decisions, not code. `character` runs today and costs "
        "nothing (EVAL-09)."
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="world.evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    character = sub.add_parser(
        "character", help="P1-P3 over a completed run's session logs"
    )
    character.add_argument("home_root", type=Path)
    character.add_argument(
        "--db", default=None,
        help="trace store to write eval_metrics into; without it the result "
             "is printed and lost (EVAL-07)",
    )

    sub.add_parser("judges", help="not implemented - see EVAL-09")

    # PROG-03. Wired here on the same day it was written, and that is the
    # point rather than tidiness: `character.py` and `judges.py` sat with
    # no `__main__` long enough for two documents to describe them as
    # "built", and WIRE-02 shipped counters that nothing read. A measure
    # with no entry point is a measure nobody will run.
    programs = sub.add_parser(
        "programs", help="PROG-03: what programmatic tool calling did, and whether it held"
    )
    programs.add_argument("db", type=Path, help="the run's trace store")
    programs.add_argument(
        "--record-into", default=None,
        help="trace store to write eval_metrics into; without it the result "
             "is printed and lost (EVAL-07)",
    )

    args = parser.parse_args(argv)
    if args.command == "character":
        return run_character(args.home_root, args.db)
    if args.command == "programs":
        from world.evals.programs import main as run_programs

        return run_programs(str(args.db), args.record_into)
    return run_judges(args)


if __name__ == "__main__":
    raise SystemExit(main())
