"""X5 from run 29's trace: engine-originated text in a resident's context, and its effect.

    app/.venv/bin/python docs/runs/queries/run-29-engine-text.py

from the repo root. Resident turns (`purpose = 'auto:agent_turn'`) with a
stored request body. "Effect" is the narrowest measurable one: does the call
that carries the line make a tool call as often as the calls that do not?
"""
import re
import sqlite3

c = sqlite3.connect("app/data/kasukabe-run29-20260914.db")
rows = c.execute(
    "select request_body, response_body from llm_calls where purpose = 'auto:agent_turn' "
    "and request_body is not null and response_body is not null"
).fetchall()


def acts(response):
    return bool(re.search(r'"type"\s*:\s*"tool_use"|"toolUse"|tool_calls', response or ""))


for label, pattern in (("request_plan hint", r"Consider calling request_plan"),
                       ("'Execution was struggling' interrupt", r"Execution was struggling")):
    hit = [acts(r) for b, r in rows if re.search(pattern, b)]
    miss = [acts(r) for b, r in rows if not re.search(pattern, b)]
    rate = lambda xs: f"{100 * sum(xs) / len(xs):.0f}%" if xs else "-"
    print(f"{label}: in {len(hit)} of {len(rows)} calls; acts {rate(hit)} with it, {rate(miss)} without")
