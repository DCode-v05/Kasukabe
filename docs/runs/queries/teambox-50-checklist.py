"""Every checklist metric the 50-resident run's own record can answer.

Source: docs/runs/teambox-50-100usd-2026-09-16-report.md (the run's full
record: every turn's input, actions and results, and every utterance). The
run's trace database stayed on the machine that ran it, so this reads the
report, which carries every turn and every utterance verbatim.

    app/.venv/bin/python docs/runs/queries/teambox-50-checklist.py

from the repo root. Every figure docs/GOALS.md cites as "50r" comes from
this script's output (checklist status of 2026-09-21).

Definitions that matter:
  * "a successful world act" (G6, strict) = a call whose result carries no
    error and whose verb is not in READ_ONLY below - so `say`, `look_around`,
    `rest`, goals and intentions do not count as changing the world.
  * a settlement = a `buy`, `sell` or `trade` whose result is not an error.
  * a meal = an `eat` whose result is not an error.
  * N1 counts the COUNTERPARTY named in each settlement's parameters.
"""
import collections, json, re, statistics

F = "docs/runs/teambox-50-100usd-2026-09-16-report.md"
lines = open(F, encoding="utf-8").read().splitlines()
text = "\n".join(lines)

RESIDENTS, TICKS, SPEND = 50, 194, 100.40
DAYS = TICKS / 144

ACT = re.compile(r"^>\s*\*\*action\*\* `world:(\w+)`\s*(.*)$")
RES = re.compile(r"^>\s*↳\s*(.*)$")
INP = re.compile(r"^>\s*\*\*input\*\*\s*—?\s*(.*)$")
ERRK = re.compile(r'"(?:error|reason|problem|detail)"\s*:\s*"([^"]{4,240})"')
turns, cur = [], None
for l in lines:
    s = l.strip()
    m = INP.match(s)
    if m:
        if cur: turns.append(cur)
        cur = {"input": m.group(1), "acts": []}
        continue
    if cur is None: continue
    a = ACT.match(s)
    if a:
        cur["acts"].append({"verb": a.group(1), "params": a.group(2), "res": ""}); continue
    r = RES.match(s)
    if r and cur["acts"]:
        cur["acts"][-1]["res"] = r.group(1)
if cur: turns.append(cur)

READ_ONLY = {"say", "look_around", "describe_place", "describe_person", "describe_primitives",
             "find_people", "list_places", "check_debts", "quote_action_cost", "rest",
             "set_intention", "drop_intention", "set_goal", "update_goal", "keep_doing",
             "start_conversation", "convene"}
def ok(act): return not ERRK.search(act["res"] or "")
acts = [a for t in turns for a in t["acts"]]
any_call = sum(1 for t in turns if t["acts"])
world_effect = sum(1 for t in turns if any(a["verb"] not in READ_ONLY and ok(a) for a in t["acts"]))
print(f"TURNS {len(turns)}  ACTS {len(acts)}")
print(f"G6 (any tool call)          {100*any_call/len(turns):.1f}%")
print(f"G6 (a successful world act) {100*world_effect/len(turns):.1f}%")

done = collections.Counter(a["verb"] for a in acts if ok(a))
tried = collections.Counter(a["verb"] for a in acts)
settle = done["buy"] + done["sell"] + done["trade"]
print(f"\nES4 settlements: buy {done['buy']}/{tried['buy']}, sell {done['sell']}/{tried['sell']}, "
      f"trade {done['trade']}/{tried['trade']} = {settle} ({settle/DAYS:.0f} per game-day)")
print(f"C-D meals: eat ok {done['eat']} of {tried['eat']} = {done['eat']/(RESIDENTS*DAYS):.2f} per resident-day")

party = collections.Counter()
for t in turns:
    pass
PAR = re.compile(r'"(?:seller_id|buyer_id|counterparty_agent_id)"\s*:\s*"([^"]+)"')
deals_by = collections.Counter()
for a in acts:
    if a["verb"] in ("buy", "sell", "trade") and ok(a):
        m = PAR.search(a["params"] or "")
        if m: deals_by[m.group(1)] += 1
if deals_by:
    top, n = deals_by.most_common(1)[0]
    vals = sorted(deals_by.values())
    cum = 0; gini_num = 0
    tot = sum(vals); k = len(vals)
    gini = sum((2*i - k - 1) * v for i, v in enumerate(vals, 1)) / (k * tot)
    print(f"N1 counterparties: {k} distinct; busiest {top} in {n}/{settle} ({100*n/settle:.0f}%); Gini {gini:.2f}")

no_food = sum(1 for t in turns if re.search(r"no food|nothing you could give, eat|have not eaten", t["input"], re.I))
print(f"N3 perceptions saying no food: {no_food}/{len(turns)} ({100*no_food/len(turns):.0f}%)")

called = set(tried)
print(f"\nX8 distinct verbs called live: {len(called)}")
print("   " + ", ".join(f"{v} {tried[v]}" for v in sorted(called, key=lambda v: -tried[v])))

refusals = collections.Counter()
for a in acts:
    m = ERRK.search(a["res"] or "")
    if m:
        why = re.sub(r"\b(a0\d+)\b", "…", m.group(1))
        why = re.sub(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", "…", why)
        refusals[f"{a['verb']}: {why[:70]}"] += 1
print(f"\nX10 refusals total {sum(refusals.values())}; top:")
for why, n in refusals.most_common(5): print(f"   x{n:3} {why}")

per = [len(t["acts"]) for t in turns if t["acts"]]
print(f"\nX7 calls per acting turn: median {statistics.median(per):.0f} mean {statistics.mean(per):.2f} p90 {sorted(per)[int(.9*len(per))]}")

goals = [re.search(r'"(?:text|goal)"\s*:\s*"([^"]{3,160})"', a["params"] or "") for a in acts if a["verb"] == "set_goal"]
goals = [g.group(1).strip().lower() for g in goals if g]
if goals:
    g, n = collections.Counter(goals).most_common(1)[0]
    print(f"\nN16 set_goal: {len(goals)} calls, {len(set(goals))} distinct; most common x{n}: {g[:70]!r}")

s = next(i for i,l in enumerate(lines) if l.startswith("## The conversation"))
e = next(i for i,l in enumerate(lines) if l.startswith("## What the town could not do"))
conv = lines[s:e]
UT = re.compile(r"^>\s*`t(\d+)`\s*(↳ \*\*replies\*\* )?\*\*([^*]+)\*\*(?: → ([^:]+))?:\s*(.*)$")
threads, heads = [], [i for i,l in enumerate(conv) if l.startswith("### ")]
for k, i in enumerate(heads):
    stop = heads[k+1] if k+1 < len(heads) else len(conv)
    rows = []
    for l in conv[i:stop]:
        m = UT.match(l.strip())
        if m: rows.append({"tick": int(m.group(1)), "reply": bool(m.group(2)), "who": m.group(3).strip(),
                           "to": (m.group(4) or "").strip(), "text": m.group(5)})
    place = re.search(r"— ([^·]+) ·", conv[i])
    threads.append({"head": conv[i], "place": place.group(1).strip() if place else "?", "rows": rows})
utts = [r for t in threads for r in t["rows"]]
speakers = [len({r["who"] for r in t["rows"]}) for t in threads]
print(f"\nCONVERSATION {len(utts)} utterances, {len(threads)} threads")
print(f"G2 threads with 4+ utterances: {sum(1 for t in threads if len(t['rows']) >= 4)}")
print(f"G3 threads with 3+ speakers: {sum(1 for n in speakers if n >= 3)}; largest {max(speakers)}")

addressed = answered = 0
for t in threads:
    rows = t["rows"]
    for i, r in enumerate(rows):
        if not r["to"]: continue
        addressed += 1
        if any(later["who"] == r["to"] and (later["to"] == r["who"] or not later["to"]) for later in rows[i+1:]):
            answered += 1
print(f"G1 reply rate: {answered}/{addressed} addressed lines answered by the addressee ({100*answered/addressed:.0f}%)")

group_utts = sum(len(t["rows"]) for t, n in zip(threads, speakers) if n >= 3)
print(f"Q10 utterances in 3+ speaker threads: {group_utts}/{len(utts)} ({100*group_utts/len(utts):.0f}%)")

non_en = [r for r in utts if re.search(r"[Ѐ-ӿ؀-ۿऀ-ॿ぀-ヿ一-鿿가-힯]", r["text"])]
print(f"N19 utterances with non-Latin script: {len(non_en)}")
for r in non_en[:2]: print(f"   {r['who']}: {r['text'][:100]}")

by_place = collections.Counter()
for t in threads: by_place[t["place"]] += len(t["rows"])
pairs = collections.Counter(tuple(sorted((r["who"], r["to"]))) for r in utts if r["to"])
print(f"N20 top places: " + "; ".join(f"{p} {n}" for p, n in by_place.most_common(3)))
print(f"    top pairs: " + "; ".join(f"{a} ↔ {b} {n}" for (a, b), n in pairs.most_common(3)))

NO = re.compile(r"\b(no[,.!]|won't|will not|not selling|can't sell|i refuse|not for that|too (high|much|low)|"
                r"not interested|i'll pass|no deal|not going to|can't do that|cannot do that)\b", re.I)
nos = [r for r in utts if NO.search(r["text"])]
print(f"\nX18 utterances with refusal language: {len(nos)} ({100*len(nos)/len(utts):.1f}%)")
for r in nos[:3]: print(f"   {r['who']}: {r['text'][:120]}")

aid = [r for r in utts if re.search(r"dormant|energy (is )?(going )?low|stay conscious", r["text"], re.I)]
gifts = done["give"] + done["transfer"]
print(f"\nQ13 utterances about running out of energy: {len(aid)}; gifts that went through (give/transfer): {gifts}")

ps = next((i for i,l in enumerate(lines) if l.startswith("## Prices")), None)
if ps is not None:
    print("\nQ16 prices section (first lines):")
    for l in lines[ps+1:ps+8]:
        if l.strip(): print("   " + l[:150])

print(f"\nQ17 buy_energy ok {done['buy_energy']}/{tried['buy_energy']}; revive ok {done['revive']}/{tried['revive']}")
for a in acts:
    if a["verb"] == "revive":
        print(f"   revive {a['params'][:60]} -> {a['res'][:110]}")

print(f"\nQ24 cost per agent-day (as run, dormancy included): ${SPEND/(RESIDENTS*DAYS):.2f}")
print(f"X3  run cost ${SPEND:.2f}")
