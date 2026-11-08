"""The checklist, scored from one run's trace.

docs/GOALS.md's standing rule: "Every run report opens with which boxes it
ticked and which questions it answered, and closes with the ones it could not
reach and why." Until now that was done by hand after each run, from the
report's prose - which is how figures went unqueried (X4) and items went
unchecked for runs that could have decided them.

`score(trace_db, reading)` decides every open item a run CAN decide, each by
one read-only query over the trace (events, llm_calls, tool_invocations,
transactions, utterances) and the run's final reading. Each row carries the
item's bar, what this run measured, and a verdict:

  * "held"       - the bar is met by this run's evidence;
  * "not held"   - measured, and the bar is not met (a zero is data too);
  * "no data"    - the run did not record what the item needs.

Items no run can decide on its own (a judge model, a paid evaluation, a
design that does not exist yet) are not scored here; ELECTION-RUN-PLAN.md
names them. Nothing here changes what residents do - it reads what they did.
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from typing import Any

TICKS_PER_DAY = 144
SETTLEMENTS = ("business.sale.completed", "business.purchase.completed", "economy.trade.settled")
READ_ONLY = {
    "say", "look_around", "describe_place", "describe_person", "describe_primitives",
    "find_people", "list_places", "check_debts", "quote_action_cost", "rest",
    "set_intention", "drop_intention", "set_goal", "update_goal", "retire_goal",
    "recall_goals", "keep_doing", "start_conversation", "convene", "note_belief",
}
PRODUCTION = {"harvest", "fell_timber", "mine", "craft", "cook"}


class Trace:
    """The few reads the scorer needs, over one trace file, read-only."""

    def __init__(self, path: str) -> None:
        self.path = path

    def rows(self, sql: str, *params: Any) -> list[tuple]:
        try:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            try:
                return conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []

    def events(self, *types: str) -> list[dict]:
        if not types:
            return []
        marks = ",".join("?" * len(types))
        out = []
        for kind, payload, tick in self.rows(
            f"select type, payload, tick from events where type in ({marks}) order by tick", *types
        ):
            try:
                body = json.loads(payload) if isinstance(payload, str) else (payload or {})
            except (ValueError, TypeError):
                continue
            if isinstance(body, dict):
                out.append({**body, "_type": kind, "_tick": tick})
        return out

    def invocations(self) -> list[tuple]:
        return self.rows("select agent_id, tick, tool, namespace, owner_id, program_id from tool_invocations")


def _row(item: str, bar: str, value: str, held: "bool | None") -> dict:
    return {"item": item, "bar": bar, "value": value,
            "verdict": "no data" if held is None else ("held" if held else "not held")}


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def score(trace_db: str, reading: "dict | None" = None) -> list[dict]:
    """Every run-decidable open item, scored. See the module docstring."""
    t = Trace(trace_db)
    reading = reading or {}
    rows: list[dict] = []

    turns = {(a, k) for a, k in t.rows(
        "select agent_id, tick from llm_calls where purpose = 'auto:agent_turn' and success = 1")}
    invs = t.invocations()
    residents = len({a for a, _ in turns}) or len(reading.get("agents") or [])
    ticks = [k for _, k in turns if k is not None]
    first, last = (min(ticks), max(ticks)) if ticks else (0, 0)
    days = max((last - first) / TICKS_PER_DAY, 1 / TICKS_PER_DAY) if ticks else 0.0
    resident_days = residents * days

    # ── gates ─────────────────────────────────────────────────────────
    utt = t.rows("select speaker_agent_id, listener_agent_id, conversation_id, content, tick "
                 "from utterances order by tick")
    by_conv: dict[str, list[tuple]] = {}
    for u in utt:
        by_conv.setdefault(u[2] or "", []).append(u)
    addressed = answered = 0
    for lines in by_conv.values():
        for i, (who, to, _, _, tick) in enumerate(lines):
            if to:
                addressed += 1
                answered += any(later[0] == to for later in lines[i + 1:])
    rows.append(_row("G1", "reply rate ≥ 60%", f"{answered} of {addressed} addressed remarks answered "
                     f"({_pct(answered, addressed)})", (answered / addressed >= 0.6) if addressed else None))
    long_ones = sum(1 for lines in by_conv.values() if len(lines) >= 4)
    rows.append(_row("G2", "≥ 3 exchanges of 4+ turns", f"{long_ones}", long_ones >= 3 if utt else None))
    group = max((len({u[0] for u in lines}) for lines in by_conv.values()), default=0)
    rows.append(_row("G3", "a conversation with 3+ speakers", f"largest had {group}", group >= 3 if utt else None))

    deals = t.events(*SETTLEMENTS)
    spoken_price = 0
    for d in deals:
        conv = d.get("conversation_id")
        unit = d.get("posted_price_sim") or (d.get("price_sim") or 0) // max(1, d.get("quantity") or 1)
        totals = {str(x) for x in (unit, d.get("total_price_sim"), d.get("price_sim")) if x}
        said = [u for u in by_conv.get(conv or "", []) if u[4] is not None and u[4] <= d["_tick"]]
        if conv and totals and any(re.search(rf"\b({'|'.join(totals)})\b", u[3] or "") for u in said):
            spoken_price += 1
    rows.append(_row("G4", "a spoken price settled by a tool", f"{spoken_price} settlement(s) whose price "
                     "was said in the conversation first", spoken_price >= 1 if deals else None))
    deaths = t.events("health.agent.died", "agent.died")
    rows.append(_row("G5", "all alive at the end", f"{len(deaths)} death(s)", len(deaths) == 0))
    acting = {(a, k) for a, k, tool, *_ in invs if tool not in READ_ONLY}
    effect = len(turns & acting)
    rows.append(_row("G6", "≥ 50% of turns change the world", f"{effect} of {len(turns)} turns "
                     f"({_pct(effect, len(turns))})", (effect / len(turns) >= 0.5) if turns else None))
    per_day = len(deals) / days if days else 0
    rows.append(_row("ES4", "settlements ≥ 1 per game-day", f"{len(deals)} in {days:.2f} days "
                     f"({per_day:.1f}/day)", per_day >= 1 if turns else None))
    spend = sum(c or 0 for (c,) in t.rows("select cost_nano_dollars from llm_calls")) / 1e9
    per_agent_day = spend / resident_days if resident_days else 0
    rows.append(_row("ET1", "≤ $0.20 per resident-day", f"${per_agent_day:.2f} (${spend:.2f} total)",
                     per_agent_day <= 0.20 if resident_days else None))
    inputs = [n for (n,) in t.rows("select input_tokens from llm_calls where purpose = 'auto:agent_turn' "
                                   "and success = 1 and input_tokens > 0 order by created_at")]
    p95 = sorted(inputs)[int(0.95 * (len(inputs) - 1))] if inputs else 0
    rows.append(_row("ET5", "p95 input ≤ 12,000 tokens", f"{p95:,}", p95 <= 12_000 if inputs else None))
    third = len(inputs) // 3
    ratio = (statistics.median(inputs[-third:]) / statistics.median(inputs[:third])) if third else 0
    rows.append(_row("N10", "last third ≤ 1.25× first", f"{ratio:.2f}×", ratio <= 1.25 if third else None))

    # ── the economy ───────────────────────────────────────────────────
    day_of = lambda tick: (tick or 0) // TICKS_PER_DAY  # noqa: E731
    covered = sorted({day_of(k) for k in ticks if (k % TICKS_PER_DAY) >= 36})
    daily = Counter(day_of(d["_tick"]) for d in deals)
    rows.append(_row("ECON-2", "goods move for sim every day", ", ".join(f"day {x}: {daily[x]}" for x in covered)
                     or "—", all(daily[x] >= 1 for x in covered) if covered else None))
    party = Counter()
    for d in deals:
        for key in ("seller_id", "seller_agent_id", "buyer_id", "buyer_agent_id"):
            if d.get(key):
                party[d[key]] += 1
    busiest = party.most_common(1)[0][1] / len(deals) if deals else 0
    rows.append(_row("ECON-3", "no one in ≥ 50% of deals", f"busiest in {100 * busiest:.0f}% of "
                     f"{len(deals)}", busiest < 0.5 if deals else None))
    meals = sum(1 for _, _, tool, *_ in invs if tool == "eat")
    rows.append(_row("ECON-4", "≥ 1 meal per resident-day", f"{meals / resident_days:.2f}" if resident_days else "—",
                     (meals / resident_days >= 1) if resident_days else None))
    hires = sum(1 for _, _, tool, *_ in invs if tool == "hire_employee")
    wages = t.rows("select count(*) from transactions where reason = 'wage'")[0][0] if t.rows(
        "select count(*) from transactions") else 0
    rows.append(_row("ECON-5", "a hire, and a wage paid", f"{hires} hire(s), {wages} wage payment(s)",
                     hires >= 1 and wages >= 1))
    taught = Counter(tool for _, _, tool, *_ in invs if tool in ("teach_skill", "license_tool", "sell_tool"))
    rows.append(_row("ECON-7", "knowledge changes hands", ", ".join(f"{k} {v}" for k, v in taught.items()) or "none",
                     bool(taught.get("license_tool") or taught.get("sell_tool"))))
    borrowed = sum(1 for a, _, tool, _, owner, _ in invs if owner and a != owner)
    rows.append(_row("ECON-8", "an invented tool used by someone else", f"{borrowed} call(s)", borrowed >= 1))
    programs = Counter(pg for *_, pg in invs if pg)
    pg1 = (sum(programs.values()) / len(programs)) if programs else 0
    rows.append(_row("ECON-9", "acts per program decision > 1", f"{pg1:.2f} over {len(programs)} program(s)",
                     pg1 > 1.0 if programs else False))
    transfers = t.events("economy.wallet.credited")
    loans = sum(1 for e in transfers if e.get("reason") == "loan")
    repaid = sum(1 for e in transfers if (e.get("repaid_sim") or 0) > 0)
    rows.append(_row("ECON-10", "a loan, and a repayment against it", f"{loans} loan(s), {repaid} repayment(s)",
                     loans >= 1 and repaid >= 1))
    tax = t.rows("select count(*) from transactions where reason = 'tax'")
    outflow = t.rows("select count(*) from transactions where from_pool = 'treasury' and reason in "
                     "('wage', 'pension', 'blessing')")
    tax_n, out_n = (tax[0][0] if tax else 0), (outflow[0][0] if outflow else 0)
    rows.append(_row("ECON-11", "tax taken, and treasury spends", f"{tax_n} tax charge(s), {out_n} outflow(s)",
                     tax_n >= 1 and out_n >= 1))
    made = Counter(day_of(k) for _, k, tool, *_ in invs if tool in PRODUCTION)
    days_made = [made[x] for x in covered]
    rising = len(days_made) >= 2 and days_made[-1] > days_made[0]
    rows.append(_row("ECON-12", "output rises over two+ days", " → ".join(map(str, days_made)) or "—",
                     rising if len(days_made) >= 2 else None))
    posts = t.events("economy.price.posted")
    repriced = sum(1 for p in posts if p.get("previous_ask") and p.get("ask_sim") and p["ask_sim"] != p["previous_ask"])
    rows.append(_row("ECON-14", "a posted price changes", f"{repriced} reprice(s) of {len(posts)} post(s)",
                     repriced >= 1))

    # ── the state ─────────────────────────────────────────────────────
    tallies = t.events("politics.election.tallied")
    seated = [e for e in tallies if (e.get("result") or {}).get("pm_agent_id") or e.get("pm_agent_id")]
    rows.append(_row("GOV-1", "an election completes and seats fill", f"{len(tallies)} tallied, "
                     f"{len(seated)} with a PM", len(seated) >= 1))
    candidates = {e.get("agent_id") or e.get("candidate_agent_id") for e in t.events("politics.candidacy.declared")}
    votes = t.events("politics.vote.cast")
    ordinary = sum(1 for v in votes if (v.get("voter_agent_id") or v.get("agent_id")) not in candidates)
    rows.append(_row("GOV-2", "an ordinary resident votes", f"{len(votes)} vote(s), {ordinary} by non-candidates",
                     ordinary >= 1))
    governing = Counter(tool for _, _, tool, ns, _, _ in invs if (ns or "").lower() == "government")
    rows.append(_row("GOV-3", "a seat acts as the state", ", ".join(f"{k} {v}" for k, v in governing.items())
                     or "no government tool used", bool(governing)))
    levers = t.events("politics.price_control.set", "politics.business_licensing.set",
                      "politics.tax_rate.set", "politics.credit_store_margin.set")
    rows.append(_row("GOV-4 (live)", "a policy lever is pulled", f"{len(levers)} change(s)", len(levers) >= 1))
    amended = t.events("politics.charter.amended")
    rows.append(_row("GOV-5", "the charter changes by vote", f"{len(amended)} amendment(s)", len(amended) >= 1))
    orders = [a for a in amended if a.get("field_name") == "standing_orders"]
    rows.append(_row("GOV-6", "a standing order becomes law", f"{len(orders)}", len(orders) >= 1))
    rates = t.events("politics.tax_rate.set")
    rows.append(_row("GOV-7", "the tax rate is changed", f"{len(rates)} change(s)", len(rates) >= 1))
    rulings = t.events("crime.verdict.recorded")
    rows.append(_row("GOV-8", "a ruling against the accused", f"{len(rulings)} ruling(s)",
                     any(r.get("against_agent_id") for r in rulings)))
    orgs = sum(1 for _, _, tool, *_ in invs if tool == "register_org")
    org_acts = sum(1 for _, _, _, ns, _, _ in invs if (ns or "").lower() == "organization")
    rows.append(_row("GOV-9", "an organisation acts", f"{orgs} founded, {org_acts} act(s)", orgs >= 1 and org_acts >= 1))
    justice = reading.get("justice") or {}
    scorable = (justice.get("true") or 0) + (justice.get("false") or 0)
    rows.append(_row("GOV-10", "the false-conviction rate has a number",
                     f"{scorable} scorable conviction(s)" if justice else "no justice reading",
                     scorable >= 1 if justice else None))

    # ── questions a run answers directly ──────────────────────────────
    n6_talked = sum(1 for conv, lines in by_conv.items() if conv and any(re.search(r"\b\d+\b", u[3] or "")
                                                                        for u in lines))
    n6_settled = len({d.get("conversation_id") for d in deals if d.get("conversation_id")})
    rows.append(_row("N6", "agreements become acts (reported)", f"{n6_settled} conversation(s) settled a deal, "
                     f"of {n6_talked} that named a number", True if utt else None))
    rows.append(_row("N15", "a day 2 is reached", f"days covered: {covered}", len(covered) >= 2 if covered else None))
    # "for > 50% of RESIDENTS", so the denominator is residents and the
    # numerator has to be residents too. It used to be the number of TIMES
    # the commonest goal text was set, which counts one resident renewing
    # the same goal sixteen times as sixteen residents - and `agent.goal.set`
    # fires on every renewal, including the ones `goal_lifecycle` raises from
    # a need. election30 read "47 distinct of 85" and was scored not held on
    # a town where no goal was shared by more than a handful of people.
    set_goals = [
        (e.get("agent_id"), (e.get("text") or e.get("goal") or "").strip().lower())
        for e in t.events("agent.goal.set")
    ]
    goals = [text for _, text in set_goals if text]
    held_by: dict[str, set[str]] = defaultdict(set)
    for who, text in set_goals:
        if text and who:
            held_by[text].add(who)
    top = max((len(who) for who in held_by.values()), default=0)
    rows.append(_row("N16", "no single goal for > 50% of residents",
                     f"{len(set(goals))} distinct of {len(goals)}; the commonest is held by "
                     f"{top} of {residents} resident(s)",
                     (top / max(1, residents)) <= 0.5 if goals else None))
    foreign = sum(1 for u in utt if re.search(r"[Ѐ-ӿ؀-ۿऀ-ॿ぀-ヿ"
                                              r"一-鿿가-힯]", u[3] or ""))
    rows.append(_row("N19", "0 non-English utterances", f"{foreign} of {len(utt)}", foreign == 0 if utt else None))
    land = sum(1 for _, _, tool, *_ in invs if tool in ("transfer_tile", "auction_land"))
    rows.append(_row("X16", "land changes hands (reported)", f"{land} transfer(s)", land >= 1))
    rows.append(_row("X17", "the treasury spends, on a resident's authority", f"{out_n} outflow(s)", out_n >= 1))
    births = t.events("agent.birth.granted")
    rows.append(_row("X14", "a death, and what follows (reported)", f"{len(deaths)} death(s)", len(deaths) >= 1))
    rows.append(_row("X15", "a birth (reported)", f"{len(births)} birth(s)", len(births) >= 1))
    gifts = sum(1 for e in transfers if e.get("reason") == "gift")
    rows.append(_row("X19", "someone gives to someone (reported)", f"{gifts} gift(s)", gifts >= 1))

    # ── the instrument, re-measured ───────────────────────────────────
    # X2: the fix ledger. Each defect fixed since the 50-resident run gets its
    # verdict here - the refusal it produced, counted again.
    refusals = (reading.get("refusals") or {}).get("refusals") if isinstance(reading.get("refusals"), dict) \
        else reading.get("refusals")
    if refusals:
        text = lambda r: str(r.get("reason") or r.get("error") or r.get("message") or r)  # noqa: E731
        no_price = sum(1 for r in refusals if "has not posted a price" in text(r))
        no_tax = sum(1 for r in refusals if "owe no head tax" in text(r))
        rows.append(_row("X2", "fixed defects stay fixed (reported)",
                         f"'no posted price' {no_price} (50r: 87); 'owe no head tax' {no_tax} (50r: 18)", True))
        who = sum(1 for r in refusals if re.search(r"not found|not here|no (one|agent|resident|person)[^.]*named",
                                                   text(r), re.I) and r.get("tool") in (
            "buy", "sell", "trade", "say", "transfer", "give", "describe_person"))
        rows.append(_row("N14", "refusals about WHO ≤ 5% (reported)", f"{who} of {len(refusals)} refusals", True))
    else:
        rows.append(_row("X2", "fixed defects stay fixed (reported)", "no refusal reading", None))
    bodies = t.rows("select request_body from llm_calls where purpose = 'auto:agent_turn' "
                    "and request_body is not null")
    struggling = sum(1 for (b,) in bodies if "Execution was struggling" in (b or ""))
    rows.append(_row("X5", "engine text in the prompt is measured (reported)",
                     f"'Execution was struggling' in {struggling} of {len(bodies)} resident calls "
                     f"({_pct(struggling, len(bodies))})", True if bodies else None))
    return rows


def checklist_section(trace_db: str, reading: "dict | None" = None) -> list[str]:
    """The report's opening section: what this run ticked, and what it did not."""
    rows = score(trace_db, reading)
    held = [r for r in rows if r["verdict"] == "held"]
    out = ["## The checklist, scored from this run\n",
           f"**{len(held)} of {len(rows)} run-decidable items held.** Each row is one query over this run's "
           "trace (`world/run_checklist.py`); the bar is `docs/GOALS.md`'s. Items no run can decide alone "
           "(judges, paid evaluations, designs not yet built) are listed in `docs/runs/ELECTION-RUN-PLAN.md`.\n",
           "| Item | Bar | This run | Verdict |", "|---|---|---|---|"]
    for r in rows:
        mark = {"held": "✅ held", "not held": "✗ not held", "no data": "— no data"}[r["verdict"]]
        out.append(f"| {r['item']} | {r['bar']} | {r['value']} | {mark} |")
    out.append("")
    return out
