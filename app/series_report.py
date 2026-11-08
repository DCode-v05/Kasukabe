"""One table across every run in the series, read from completed stores.

    uv run python series_report.py run1 run2 ... > ../docs/runs/SERIES.md

Reads each run's own trace store and its saved session logs. Nothing here
grades anything the gate scorer does not - loop_report.py owns the bars,
and this only lines up what it said, run by run, so a change that helped
can be told from one that happened to land on a good day.

Build Spec v2 section 10.1: every number is off a COMPLETED store. A run
still going is not in the table.
"""

from __future__ import annotations

import collections
import glob
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from loop_report import (  # noqa: E402
    DEEP_EXCHANGE_TURNS,
    GROUP_SPEAKERS_MIN,
    alternating_turns,
    exchanges,
    reply_rate,
    settlements,
    utterances,
    world_effect,
)

SERIES = pathlib.Path(__file__).parent.parent / "docs" / "runs" / "series"

# EVAL-22. Every run the series consists of, in order, so `series_report.py`
# with no arguments describes THE SERIES rather than a slice of its first
# half.
#
# The default used to be `short1 run2 run3 run4 run5 run6` - six of the ten
# diagnostic runs, chosen when six was all there were and never updated as
# run7..run10 and then final1..final6 landed. Anyone running this to see
# "the series" got a table missing four diagnostic runs and the entire
# fixed-build cohort, with nothing on the page saying so.
#
# final5 and final6 are in the list deliberately. final5 is disputed and
# final6 persisted nothing (SERIES.md section 5); both belong in the input
# so the report SAYS they are missing or anomalous instead of quietly
# producing a four-run table labelled as six.
DIAGNOSTIC_RUNS = ["short1", "run2", "run3", "run4", "run5", "run6",
                   "run7", "run8", "run9", "run10"]
FIXED_BUILD_RUNS = ["final1", "final2", "final3", "final4", "final5", "final6"]
THE_SERIES = DIAGNOSTIC_RUNS + FIXED_BUILD_RUNS


def one(name: str) -> dict | None:
    db = pathlib.Path(__file__).parent / "data" / f"{name}.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    utts = utterances(conn)
    answered, directed, _ = reply_rate(utts)
    groups = exchanges(utts)
    acted, cognition, tools = world_effect(conn)
    calls, ok, spend = conn.execute(
        "select count(*), sum(success), sum(cost_nano_dollars)/1e9 from llm_calls"
    ).fetchone()
    alive = conn.execute("select count(*) from agents where is_alive=1").fetchone()[0]
    total = conn.execute("select count(*) from agents").fetchone()[0]
    ticks = conn.execute("select min(tick), max(tick) from utterances").fetchone()

    # Per-TURN action rate, from the engines' own session logs. G6 counts
    # model CALLS and one turn is several, so the two answer different
    # questions and both are worth having - measured on short1 they
    # disagreed by five points, in the direction opposite to the one I
    # guessed.
    turns = acted_turns = 0
    for path in glob.glob(str(SERIES / name / "sessions" / "*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if "actions" not in record:
                    continue
                turns += 1
                if record.get("actions"):
                    acted_turns += 1

    return {
        "name": name,
        "calls": calls or 0,
        "failed": (calls or 0) - (ok or 0),
        "spend": spend or 0.0,
        "utterances": len(utts),
        "directed": directed,
        "answered": answered,
        "reply_rate": answered / directed if directed else 0.0,
        "deep": len([g for g in groups if alternating_turns(g) >= DEEP_EXCHANGE_TURNS]),
        "gatherings": len(
            [g for g in groups if len({u["speaker"] for u in g}) >= GROUP_SPEAKERS_MIN]
        ),
        "biggest": max(
            (len({u["speaker"] for u in g}) for g in groups), default=0
        ),
        "settlements": len(settlements(conn, utts)),
        "alive": f"{alive}/{total}",
        "effect": acted / cognition if cognition else 0.0,
        "turn_effect": acted_turns / turns if turns else 0.0,
        "tools": tools,
        "first_tick": ticks[0],
        "last_tick": ticks[1],
    }


def main(names: list[str]) -> int:
    runs = [r for r in (one(n) for n in names) if r]
    # EVAL-22. Say what was asked for and not found.
    #
    # A run with no store in `app/data/` used to be dropped without a word,
    # so the totals line below would read "4 runs, 1,534 calls" after being
    # asked for sixteen and a reader had no way to tell the difference
    # between "twelve runs are missing" and "the series is four runs long".
    # Those stores are gitignored and the final cohort's are long gone, so
    # this is the normal case on any fresh checkout, not an edge one.
    missing = [n for n in names if n not in {r["name"] for r in runs}]
    if missing:
        print(
            f"*Asked for {len(names)} runs, found {len(runs)}. No trace store under "
            f"`app/data/` for: {', '.join(missing)} - these are gitignored and are "
            "not archived with the run directories (EVAL-24). Every figure below "
            "covers the runs that were found and no others.*\n"
        )
    if not runs:
        print("no completed runs found")
        return 1

    print("| run | calls | failed | spend | utts | directed | G1 reply | G2 deep | "
          "G3 groups | G4 settled | G5 alive | G6 calls | per turn |")
    print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|:-:|--:|--:|")
    for r in runs:
        print(
            f"| {r['name']} | {r['calls']} | {r['failed']} | ${r['spend']:.2f} | "
            f"{r['utterances']} | {r['directed']} | {r['reply_rate']:.0%} | {r['deep']} | "
            f"{r['gatherings']} | {r['settlements']} | {r['alive']} | "
            f"{r['effect']:.0%} | {r['turn_effect']:.0%} |"
        )

    total_calls = sum(r["calls"] for r in runs)
    total_failed = sum(r["failed"] for r in runs)
    print(
        f"\n**Totals.** {len(runs)} runs, {total_calls:,} calls, {total_failed} failed "
        f"({total_failed / total_calls:.2%}), ${sum(r['spend'] for r in runs):.2f}, "
        f"{sum(r['utterances'] for r in runs)} utterances, "
        f"{sum(r['settlements'] for r in runs)} settlements. Largest gathering across "
        f"the series: {max(r['biggest'] for r in runs)} speakers."
    )

    everything: collections.Counter = collections.Counter()
    for r in runs:
        everything.update(r["tools"])
    print("\n**Tools called across the series.**\n")
    print("| tool | calls |")
    print("|---|--:|")
    for tool, n in everything.most_common(16):
        print(f"| `{tool}` | {n} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or THE_SERIES))
