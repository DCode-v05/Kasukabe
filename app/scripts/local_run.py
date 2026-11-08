"""LOCAL BASE: one command that stands a Kasukabe town up on a developer
machine and prints the evidence that it worked.

Why this exists. `start-genesis.sh` is the production launcher: 250 agents,
real Bedrock, Docker, two long-lived containers, an AWS credentials mount.
It is the right shape for the genesis host and the wrong shape for the loop
a developer actually needs, which is "change one thing, run a small town,
look at what came out, repeat". Before this script that loop was a
hand-assembled `python -c` incantation that had to remember to generate a
population file, export KASUKABE_SQLITE_PATH before anything imported the
trace client, pick a tick count, and then open the SQLite file by hand to
find out whether any of it was recorded. Every one of those steps is a
place to get it subtly wrong, and getting it wrong looks like success.

What it does NOT do is reimplement the runner. ROADMAP.md's rung 1 is the
regression harness that keeps Round 2 safe, and a harness that exercises a
different code path from production is worse than no harness. So this
script shells out to `python -m world.run_genesis` with the same flags
`start-genesis.sh` uses, and confines itself to the three things around it:
generating a population of the requested size, pointing the trace store at
a real file, and reading that file back afterwards.

The trace report at the end is the point. ROADMAP.md's closing rule is that
the trace is load-bearing, and round 1's central finding was that two of
sixteen tables had rows and nobody noticed for eight game-days. A local run
that does not tell you what it recorded reproduces exactly that failure at
small scale, so this prints the row counts every time, including the zeroes.

Usage:

    python scripts/local_run.py                       # 10 agents, 2 game-days, mock
    python scripts/local_run.py --population 50 --game-days 5
    python scripts/local_run.py --live                # real Bedrock, needs a broker

Live mode is deliberately not the default. Mock mode never constructs an
engine session and never spends a cent, which is what makes it safe to run
on every change; `--live` is the one that costs money and needs the broker
on :8200.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

TICKS_PER_DAY = 144

TRACE_TABLES = (
    "agents",
    "events",
    "utterances",
    "transactions",
    "memories",
    "relationships",
    "skills",
    "llm_calls",
    "world_snapshots",
    "businesses",
    "organizations",
    "petitions",
    "cases",
    "buildings",
    "tools_registry",
    "eval_metrics",
)

EMBEDDED_HARNESS_GAPS = {
    "events": "TraceStore not mounted by the embedded harness (RC-3, fixed by T3)",
    "utterances": "no speech tool exists yet (RC-2, fixed by T2)",
    "transactions": "TraceStore not mounted by the embedded harness (RC-3, fixed by T3)",
    "memories": "TraceStore not mounted by the embedded harness (RC-3, fixed by T3)",
    "relationships": "TraceStore not mounted by the embedded harness (RC-3, fixed by T3)",
    "skills": "no skill artifact is distilled yet (fixed by T4)",
    "agents": "TraceStore not mounted by the embedded harness (RC-3, fixed by T3)",
    "llm_calls": "written by the broker, which mock mode never calls",
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a small Kasukabe town locally and report what it recorded."
    )
    parser.add_argument(
        "--population",
        type=int,
        default=10,
        help="agents in the town (default 10, ROADMAP.md's rung 1)",
    )
    parser.add_argument(
        "--game-days",
        type=int,
        default=2,
        help="game-days to run (default 2, ROADMAP.md's rung 1)",
    )
    parser.add_argument("--seed", type=int, default=2026, help="genesis seed (default 2026)")
    parser.add_argument(
        "--live",
        action="store_true",
        help="real inference through the broker on :8200 instead of mock (costs money)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=APP_ROOT.parent / "local-runs",
        help="where the trace file and checkpoints go (default kasukabe/local-runs)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="append to an existing run directory instead of starting clean",
    )
    parser.add_argument(
        "--budget-cap-cents",
        type=int,
        default=2000,
        help="spend governor cap, only meaningful with --live (default 2000 = $20)",
    )
    return parser


def generate_population(population: int, seed: int, path: Path, env: dict[str, str]) -> None:
    """Writes a genesis file of `population` agents.

    Regenerated on every run rather than cached, because the whole point of
    a local base is that the town you are looking at is the town the flags
    describe. A stale genesis_10.json left over from a different seed is
    the kind of thing that costs an afternoon.
    """
    print(f"[1/3] generating {population} agents (seed {seed})")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "world.life.genesis",
            "--population",
            str(population),
            "--seed",
            str(seed),
            "--output",
            str(path),
        ],
        cwd=APP_ROOT,
        env=env,
        check=True,
    )


def run_town(args: argparse.Namespace, genesis_path: Path, env: dict[str, str]) -> int:
    """Shells out to the real runner CLI, the same entry point
    start-genesis.sh uses in production. Returns its exit code."""
    ticks = args.game_days * TICKS_PER_DAY
    command = [
        sys.executable,
        "-m",
        "world.run_genesis",
        "--live" if args.live else "--mock",
        "--world-mode",
        "embedded",
        "--genesis-path",
        str(genesis_path),
        "--checkpoint-dir",
        str(args.out_dir / "checkpoints"),
        "--budget-cap-cents",
        str(args.budget_cap_cents),
        "--retrieval-depth",
        "1.0",
        "--supervisor-backend",
        "process",
        "--burst-ticks",
        str(ticks),
        "--no-resume",
    ]
    mode = "LIVE (real Bedrock)" if args.live else "mock"
    print(f"[2/3] running {args.game_days} game-days ({ticks} ticks), {mode}")
    return subprocess.run(command, cwd=APP_ROOT, env=env).returncode


def report_trace(db_path: Path) -> bool:
    """Prints what the run actually recorded. Returns False if the trace is
    empty enough to mean the run produced no research.

    ROADMAP.md: "The trace is load-bearing. A run that cannot write a
    complete trace halts." That rule is enforced inside the runner for a
    write that *fails*; this is the check for the quieter failure, a run
    that completed cleanly and recorded nothing.
    """
    print("[3/3] trace report")
    if not db_path.exists():
        print(f"  no trace file at {db_path}")
        return False

    connection = sqlite3.connect(db_path)
    try:
        declared = connection.execute(
            "select usd_system_total_cents from run_config where id = 1"
        ).fetchone()
        if declared:
            print(f"  ledger invariant declared at {declared[0]} cents\n")

        counts: dict[str, int] = {}
        for table in TRACE_TABLES:
            try:
                counts[table] = connection.execute(f"select count(*) from {table}").fetchone()[0]
            except sqlite3.Error as exc:
                print(f"  {table:<16} unreadable: {exc}")
                counts[table] = 0

        for table, count in counts.items():
            note = ""
            if count == 0:
                reason = EMBEDDED_HARNESS_GAPS.get(table)
                note = f"   <- {reason}" if reason else ""
            print(f"  {table:<16} {count:>6}{note}")

        snapshots = connection.execute(
            "select tick, agent_count, usd_wallets_total_cents, usd_treasury_total_cents, "
            "usd_burned_total_cents from world_snapshots order by tick"
        ).fetchall()
        if snapshots:
            print("\n  world snapshots:")
            for tick, agents, wallets, treasury, burned in snapshots:
                total = wallets + treasury + burned
                print(
                    f"    tick={tick:<5} agents={agents:<4} wallets={wallets:<7} "
                    f"treasury={treasury:<7} burned={burned:<6} total={total}"
                )

        return sum(counts.values()) > 0
    finally:
        connection.close()


def main() -> int:
    args = build_arg_parser().parse_args()

    if args.population < 6:
        print(f"population {args.population} is below the generator's floor of 6", file=sys.stderr)
        return 2

    out_dir: Path = args.out_dir.resolve()
    if out_dir.exists() and not args.keep:
        import shutil

        shutil.rmtree(out_dir)
    (out_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

    db_path = out_dir / "trace.sqlite3"
    genesis_path = APP_ROOT / "world" / f"genesis_{args.population}.json"

    env = dict(os.environ)
    env["KASUKABE_SQLITE_PATH"] = str(db_path)
    env["PYTHONPATH"] = str(APP_ROOT)

    print(f"town: {args.population} agents, {args.game_days} game-days")
    print(f"out:  {out_dir}\n")

    generate_population(args.population, args.seed, genesis_path, env)
    exit_code = run_town(args, genesis_path, env)
    if exit_code != 0:
        print(f"\nrunner exited {exit_code}", file=sys.stderr)
        report_trace(db_path)
        return exit_code

    print()
    wrote_something = report_trace(db_path)
    if not wrote_something:
        print("\nthe run completed but recorded nothing, which is not a passing run", file=sys.stderr)
        return 1

    print(f"\nok. trace at {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
