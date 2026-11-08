"""Did programmatic tool calling do anything, and did it break anything.

`PROG-03`. `docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md` section 7 asks for
the parent link *"first"*, and gives the reason plainly: *"without it no
run can measure whether programmatic calling helped."* PROG-01 wrote the
link. This is the thing that reads it, and it exists because of the
failure that section 3 of the same audit is about - counters were added,
nothing read them, and the fix that reached nobody was indistinguishable
from no fix.

FIVE MEASURES, AND ONLY ONE OF THEM IS A GATE.

  * **P1 acts per decision** - the headline, and reported rather than
    barred. One hand-made call buys one act; a program buys as many as it
    runs. A bar here would be this file deciding the cost-model question
    the audit explicitly reserves for the operator.
  * **P2 adoption** - how many residents wrote one, how many composed
    tools took up control flow at all. A feature nobody used has a P1 of
    nothing, and the two numbers have to be read together or the first
    one lies by omission.
  * **P3 the ceilings held** - THE GATE. No program exceeded the call
    ceiling, and none left its caller unable to act. This is the one
    thing that is not a matter of taste: the audit's own sentence is
    *"an unbounded loop is a resident that spends its whole wallet in one
    turn"*, and if that happened the run is contaminated whatever else it
    shows.
  * **P4 the confound** - do residents who wrote programs end richer than
    residents who did not. Reported with the question attached, never
    scored, because the audit says exactly what it is: *"either the most
    interesting result this project could produce, or a confound that
    makes every earlier run incomparable."* A gate here would answer a
    question by asserting it.
  * **P5 what it cost to find out** - programs run against model calls
    made. The denominator of the whole argument.

WHY THIS READS THE TRACE STORE AND NOT THE SESSION LOGS. Every earlier
figure in this project - task labels, refusal recovery, verb reach, "41
of 76 verbs never called" - was recovered by scraping `session_log.jsonl`
for facts the world already had (EVAL-25). `tool_invocations` is now
mirrored, and a program's whole shape is in it. Scraping a log for this
would rebuild the same defect on the day it stopped being necessary.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from world.evals.record import provenance, record_metric
from world.tools.program import PROGRAM_MAX_CALLS

ACTS_PER_HAND_CALL = 1.0


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return list(conn.execute(sql, args))
    except sqlite3.Error:
        return []


def _has_program_columns(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in _rows(conn, "pragma table_info(tool_invocations)")}
    return {"program_id", "program_step"} <= cols


@dataclass
class Programs:
    """One run's worth of programmatic tool calling, as read."""

    supported: bool = True
    programs: int = 0
    acts: int = 0
    residents: set[str] = field(default_factory=set)
    per_program: list[int] = field(default_factory=list)
    over_ceiling: list[tuple[str, int]] = field(default_factory=list)
    stopped_early: dict[str, int] = field(default_factory=dict)
    tools_with_control_flow: int = 0
    tools_composed: int = 0
    model_calls: int = 0
    sim_of_program_writers: list[int] = field(default_factory=list)
    sim_of_everyone_else: list[int] = field(default_factory=list)
    broke_someone: list[str] = field(default_factory=list)

    @property
    def acts_per_decision(self) -> float:
        return self.acts / self.programs if self.programs else 0.0


def read(conn: sqlite3.Connection) -> Programs:
    out = Programs()
    if not _has_program_columns(conn):
        out.supported = False
        return out

    grouped = _rows(
        conn,
        "select program_id, agent_id, count(*) "
        "from tool_invocations where program_id is not null "
        "group by program_id, agent_id",
    )
    for program_id, agent_id, total in grouped:
        out.programs += 1
        acts = max(int(total) - 1, 0)
        out.acts += acts
        out.per_program.append(acts)
        out.residents.add(agent_id)
        if acts > PROGRAM_MAX_CALLS:
            out.over_ceiling.append((program_id, acts))

    for (payload,) in _rows(
        conn, "select payload from events where type = 'business.invented_tool.invoked'"
    ):
        try:
            body = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except (TypeError, ValueError):
            continue
        reason = body.get("stopped_early")
        if reason:
            out.stopped_early[reason] = out.stopped_early.get(reason, 0) + 1

    for (payload,) in _rows(
        conn, "select payload from events where type = 'agent.tool.invented'"
    ):
        out.tools_composed += 1
        try:
            body = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except (TypeError, ValueError):
            continue
        if body.get("has_control_flow"):
            out.tools_with_control_flow += 1

    calls = _rows(conn, "select count(*) from llm_calls")
    out.model_calls = int(calls[0][0]) if calls else 0

    for agent_id, sim, dormant in _rows(
        conn, "select id, sim_balance, is_dormant from agents"
    ):
        if agent_id in out.residents:
            out.sim_of_program_writers.append(int(sim or 0))
            if int(sim or 0) <= 0 or int(dormant or 0):
                out.broke_someone.append(agent_id)
        else:
            out.sim_of_everyone_else.append(int(sim or 0))
    return out


def _median(values: list[int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def main(path: str, db_out: str | None = None) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    found = read(conn)

    print(f"\n{'=' * 72}\nPROGRAMMATIC TOOL CALLING  {path}\n{'=' * 72}")

    if not found.supported:
        print(
            "This trace has no `program_id` on `tool_invocations`, so it was\n"
            "written before PROG-01. That is not a run in which nothing was\n"
            "programmed - it is a run that could not have said either way."
        )
        return 2

    if not found.programs:
        print(
            f"No programs ran. {found.tools_composed} composed tool(s) exist, "
            f"{found.tools_with_control_flow} of them with control flow.\n"
            "P1 is undefined rather than zero: nobody bought anything, so\n"
            "there is no price. What this measures is reach, not capability -\n"
            "the deterministic guards in world/tools/tests say the mechanism\n"
            "works; this says whether the town took it up."
        )

    print(
        f"programs {found.programs}   acts inside them {found.acts}   "
        f"residents who ran one {len(found.residents)}"
    )

    barred = [
        (
            "P3 no program passed the call ceiling",
            f"{len(found.over_ceiling)} over {PROGRAM_MAX_CALLS}",
            "0",
            not found.over_ceiling,
        ),
        (
            "P3 no program emptied a wallet",
            f"{len(found.broke_someone)} left unable to act",
            "0",
            not found.broke_someone,
        ),
    ]
    print(f"\n{'GATE':<44}{'VALUE':<24}{'BAR':<6}RESULT")
    for name, value, bar, ok in barred:
        print(f"{name:<44}{value:<24}{bar:<6}{'PASS' if ok else 'FAIL'}")

    biggest = max(found.per_program) if found.per_program else 0
    print(f"\n{'REPORTED, NOT BARRED':<44}{'VALUE'}")
    headline = (
        f"{found.acts_per_decision:.2f}" if found.programs else "undefined"
    )
    print(
        f"{'P1 acts bought per decision':<44}"
        f"{headline}  (by hand: {ACTS_PER_HAND_CALL:.2f})"
    )
    print(f"{'   largest single program':<44}{biggest} acts")
    print(
        f"{'P2 composed tools using control flow':<44}"
        f"{found.tools_with_control_flow}/{found.tools_composed}"
    )
    print(
        f"{'P5 programs against model calls':<44}"
        f"{found.programs}/{found.model_calls}"
    )
    if found.stopped_early:
        which = ", ".join(f"{k} {v}" for k, v in sorted(found.stopped_early.items()))
        print(f"{'   programs that met a ceiling':<44}{which}")

    writers = _median(found.sim_of_program_writers)
    others = _median(found.sim_of_everyone_else)
    print(f"\n{'P4 THE CONFOUND - A QUESTION, NOT A SCORE':<44}{'VALUE'}")
    print(
        f"{'   median sim, wrote a program':<44}"
        f"{writers:.0f}  (n={len(found.sim_of_program_writers)})"
    )
    print(
        f"{'   median sim, did not':<44}"
        f"{others:.0f}  (n={len(found.sim_of_everyone_else)})"
    )
    print(
        "   'Thinking costs money' is the experiment. If six acts cost one\n"
        "   call, a resident that writes good programs is structurally richer\n"
        "   than one that does not - which is either the most interesting\n"
        "   result this project could produce or a confound that makes every\n"
        "   earlier run incomparable. That is an operator decision; see\n"
        "   docs/decisions/PROG-COST-MODEL.md. This file will not score it."
    )

    written = 0
    if db_out:
        payload = {"samples": found.programs, **provenance()}
        for name, value, extra in (
            ("prog_acts_per_decision",
             found.acts_per_decision if found.programs else None,
             {"acts": found.acts, "largest": biggest}),
            ("prog_adoption_residents", float(len(found.residents)),
             {"tools_with_control_flow": found.tools_with_control_flow,
              "tools_composed": found.tools_composed}),
            ("prog_ceilings_held", 0.0 if (found.over_ceiling or found.broke_someone) else 1.0,
             {"over_ceiling": len(found.over_ceiling),
              "emptied": len(found.broke_someone)}),
            ("prog_median_sim_writers", writers,
             {"n_writers": len(found.sim_of_program_writers),
              "median_sim_others": others,
              "n_others": len(found.sim_of_everyone_else)}),
        ):
            if record_metric(
                db_out, track="mas_engineering", metric_name=name,
                metric_value=value, payload={**payload, **extra},
            ):
                written += 1
        print(f"\nRecorded {written} of 4 metrics in {db_out}.")
        if written < 4:
            print("Some did not land - the numbers above are still the numbers.")

    failed = [name for name, _, _, ok in barred if not ok]
    print(f"\n{'CEILINGS HELD' if not failed else 'CEILING BREACHED: ' + ', '.join(failed)}\n")
    return 0 if not failed else 1


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="world.evals.programs", description=__doc__)
    parser.add_argument("db", type=Path, help="the run's trace store")
    parser.add_argument(
        "--record-into", default=None,
        help="trace store to write eval_metrics into; without it the result "
             "is printed and lost (EVAL-07)",
    )
    args = parser.parse_args(argv)
    return main(str(args.db), args.record_into)


if __name__ == "__main__":
    raise SystemExit(cli())
