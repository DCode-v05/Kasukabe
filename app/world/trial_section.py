"""The four bullets of the operator's diagnosis, answered from one run's trace.

`run_report.py` scores the checklist and describes the town. This asks the one
question a seeded-economy run is bought to answer: with food on the shelves, a
price on every counter, materials in the yard and a larder in every house,
does the town trade, cook, earn and vote - or does it do what election30 did
and pile up wheat nobody buys?

    python -m world.trial_section --db data/<run>.db >> <report>.md

Written as a section to append rather than a second report: the run's own
report stays the one document, and this is the part of it that is about the
change being tested.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter

SETTLEMENTS = ("economy.trade.settled", "business.sale.completed")


def rows(db: sqlite3.Connection, sql: str, *args):
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def count(db, table: str, column: str, value: str) -> int:
    got = rows(db, f"select count(*) from {table} where {column}=?", value)
    return got[0][0] if got else 0


def section(path: str) -> str:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: list[str] = []
    w = out.append

    w("## The four bullets, answered from this run\n")

    residents = rows(db, "select count(distinct agent_id) from llm_calls "
                         "where purpose='auto:agent_turn' and success=1")
    n_res = residents[0][0] if residents else 0
    span = rows(db, "select min(tick), max(tick) from tool_invocations")
    lo, hi = span[0] if span else (0, 0)
    spent = rows(db, "select sum(cost_nano_dollars) from llm_calls")
    usd = (spent[0][0] or 0) / 1e9 if spent else 0.0
    days = max((hi - lo) / 144, 1 / 144)

    w(f"- residents who acted: **{n_res}**")
    w(f"- ticks **{lo} to {hi}** (game-day {lo // 144}), **{hi - lo}** ticks = "
      f"{(hi - lo) / 144:.2f} game-days")
    w(f"- spend **${usd:,.2f}**, which is **${usd / max(n_res, 1) / days:,.2f}** "
      f"per resident-day")
    w("")

    w("### 1. Cooking: does bread get baked?\n")
    for verb in ("cook", "craft", "eat", "harvest"):
        by = rows(db, "select agent_id, count(*) from tool_invocations "
                      "where tool=? group by 1 order by 2 desc", verb)
        total = sum(n for _, n in by)
        who = ", ".join(f"{a} x{n}" for a, n in by[:4]) or "nobody"
        w(f"- `{verb}`: **{total}** ({who})")
    w("")

    w("### 2 and 3. Do shops sell, and did it take a conversation?\n")
    for verb in ("buy", "sell", "trade", "post_price", "stock_shelf",
                 "take_from_shelf", "draw_from_till", "give", "transfer"):
        w(f"- `{verb}`: **{count(db, 'tool_invocations', 'tool', verb)}**")
    marks = ",".join("?" * len(SETTLEMENTS))
    settled = rows(db, f"select payload from events where type in ({marks})", *SETTLEMENTS)
    shop = count(db, "events", "type", "business.sale.completed")
    w(f"- **settlements: {len(settled)}**, of which across a shop counter: **{shop}**")
    if settled:
        items: Counter = Counter()
        value = 0
        for (p,) in settled:
            try:
                d = json.loads(p)
            except (TypeError, ValueError):
                continue
            items[d.get("item")] += int(d.get("quantity") or 0)
            value += int(d.get("price_sim") or d.get("total_sim") or 0)
        if items:
            w("- goods moved: " + ", ".join(f"{n} {i}" for i, n in items.most_common(8)))
        w(f"- value settled: **{value:,} sim**")
    for kind in ("business.stock.moved", "business.till.moved",
                 "economy.price.posted", "economy.upkeep.charged"):
        w(f"- `{kind}`: **{count(db, 'events', 'type', kind)}**")
    w("")

    w("### 4. Does anybody earn?\n")
    for verb in ("set_wage", "pay_wage", "hire_employee", "offer_job", "work_site",
                 "mine", "fell_timber"):
        w(f"- `{verb}`: **{count(db, 'tool_invocations', 'tool', verb)}**")
    w("")

    w("### The election, on game-day 2\n")
    cands = rows(db, "select payload from events where type='politics.candidacy.declared'")
    votes = rows(db, "select payload from events where type='politics.vote.cast'")
    tallies = rows(db, "select payload, tick from events "
                       "where type='politics.election.tallied' order by tick")
    phases = rows(db, "select payload, tick from events "
                      "where type='politics.election.phase_advanced' order by tick")
    w(f"- candidacies declared: **{len(cands)}**")
    w(f"- votes cast: **{len(votes)}**")
    w(f"- tallies: **{len(tallies)}**")
    for p, t in phases:
        try:
            d = json.loads(p)
        except (TypeError, ValueError):
            continue
        w(f"  - tick {t}: phase -> **{d.get('phase') or d.get('to') or d}**")
    for p, t in tallies:
        try:
            d = json.loads(p)
        except (TypeError, ValueError):
            continue
        w(f"  - tick {t}: **PM {d.get('pm_agent_id')}**, "
          f"ministers {d.get('minister_agent_ids')}")
    if votes:
        voters = {json.loads(p).get("voter_agent_id") for (p,) in votes if p}
        standing = {json.loads(p).get("agent_id") for (p,) in cands if p}
        w(f"- distinct voters **{len(voters)}**, of whom not candidates: "
          f"**{len(voters - standing)}**")
    w("")

    w("### Did the government govern?\n")
    gov = rows(db, "select tool, count(*) from tool_invocations "
                   "where namespace='government' group by 1 order by 2 desc")
    if gov:
        for t, n in gov:
            w(f"- `{t}`: **{n}**")
    else:
        w("- no verb in the government namespace was called")
    w("")

    w("### Every verb this town called\n")
    used = rows(db, "select tool, count(*) from tool_invocations "
                    "group by 1 order by 2 desc")
    w(f"**{len(used)} distinct verbs**, {sum(n for _, n in used)} calls.\n")
    w("| verb | calls |")
    w("|---|--:|")
    for t, n in used:
        w(f"| `{t}` | {n} |")
    w("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="the run's trace database")
    print(section(ap.parse_args().db))


if __name__ == "__main__":
    main()
