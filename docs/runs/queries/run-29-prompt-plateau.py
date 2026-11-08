"""N10 and ET5 from run 29's trace: does prompt size plateau, and what is p95?

    app/.venv/bin/python docs/runs/queries/run-29-prompt-plateau.py

from the repo root. Resident turns only (`purpose = 'auto:agent_turn'`),
successful calls with a recorded input size, in the order they were made.
N10's bar: the last third's median input <= 1.25x the first third's.
ET5's bar: p95 input <= 12,000 tokens.
"""
import sqlite3
import statistics

DB = "app/data/kasukabe-run29-20260914.db"
rows = sqlite3.connect(DB).execute(
    "select input_tokens from llm_calls where purpose = 'auto:agent_turn' "
    "and success = 1 and input_tokens > 0 order by created_at"
).fetchall()
xs = [r[0] for r in rows]
third = len(xs) // 3


def q(values, p):
    return sorted(values)[int(p * (len(values) - 1))]


first, last = statistics.median(xs[:third]), statistics.median(xs[-third:])
print(f"resident calls {len(xs)}; median {statistics.median(xs):,.0f}; "
      f"p90 {q(xs, .9):,}; p95 {q(xs, .95):,}")
print(f"N10: first third median {first:,.0f}, last third {last:,.0f}, ratio {last / first:.2f} (bar <= 1.25)")
