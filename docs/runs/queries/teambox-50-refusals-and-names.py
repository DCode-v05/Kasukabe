"""Q2/X10 refusal recovery and N14 names-vs-ids, from the 50-resident run's report.

    app/.venv/bin/python docs/runs/queries/teambox-50-refusals-and-names.py

from the repo root. Each resident's transcript sits under a `#### Name `id``
heading in "Sessions, per engine" → "Transcripts"; each act is an `**action**`
line followed by its `↳` result. A refusal is a result that begins "Error".

Recovery (BUILD_SPEC 5.6, as measured here): for every refused act, the same
resident's NEXT act - later in the turn, or first in its next turn - is either
an identical repeat (same verb, same parameters), the same verb with changed
parameters, or a different verb.

N14: acts that name a person in a person parameter, split by whether the value
is an id (a0123) or words, and the share of each that was refused.
"""
import collections, json, re

F = "docs/runs/teambox-50-100usd-2026-09-16-report.md"
lines = open(F, encoding="utf-8").read().splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("### Transcripts"))
HEAD = re.compile(r"^#### (.+?) `(a\d+)`")
ACT = re.compile(r"^>\s*\*\*action\*\* `world:(\w+)`\s*(.*?)\s*$")
RES = re.compile(r"^>\s*↳\s*(.*)$")
acts_by = collections.defaultdict(list)
who = None
for l in lines[start:]:
    s = l.strip()
    if s.startswith("## "):
        break
    m = HEAD.match(s)
    if m:
        who = m.group(2); continue
    a = ACT.match(s)
    if a and who:
        acts_by[who].append({"verb": a.group(1), "params": a.group(2), "res": ""}); continue
    r = RES.match(s)
    if r and who and acts_by[who]:
        acts_by[who][-1]["res"] = r.group(1)

refused = lambda a: a["res"].startswith("Error")
outcome = collections.Counter()
next_ok = 0
for acts in acts_by.values():
    for a, b in zip(acts, acts[1:]):
        if not refused(a):
            continue
        if b["verb"] == a["verb"] and b["params"] == a["params"]:
            outcome["identical repeat"] += 1
        elif b["verb"] == a["verb"]:
            outcome["same verb, changed parameters"] += 1
        else:
            outcome["a different verb"] += 1
        next_ok += not refused(b)
total = sum(outcome.values()) or 1
print(f"refusals followed by another act: {total}")
for k, v in outcome.most_common():
    print(f"   {k}: {v} ({100*v/total:.0f}%)")
print(f"   next act succeeded: {next_ok} ({100*next_ok/total:.0f}%)")

PERSON = ("seller_id", "buyer_id", "counterparty_agent_id", "to_agent_id",
          "listener_agent_id", "agent_id", "holder_agent_id", "to")
kinds = collections.Counter(); refusals = collections.Counter()
for acts in acts_by.values():
    for a in acts:
        try:
            params = json.loads(a["params"] or "{}")
        except ValueError:
            continue
        for key in PERSON:
            v = params.get(key)
            if isinstance(v, str) and v.strip():
                kind = "id" if re.fullmatch(r"a\d{3,5}", v.strip()) else "name"
                kinds[kind] += 1; refusals[kind] += refused(a)
                break
for kind in ("id", "name"):
    n = kinds[kind]
    if n:
        print(f"N14 by {kind}: {n} calls, {refusals[kind]} refused ({100*refusals[kind]/n:.1f}%)")

REF = re.compile(r"no (one|agent|resident|person)[^\"]*named|not found|unknown (agent|person)|"
                 r"no such (agent|person)|isn't here|is not here|not here|not co-located|could not find", re.I)
refref = collections.Counter()
for acts in acts_by.values():
    for a in acts:
        try:
            params = json.loads(a["params"] or "{}")
        except ValueError:
            continue
        for key in PERSON:
            v = params.get(key)
            if isinstance(v, str) and v.strip():
                kind = "id" if re.fullmatch(r"a\d{3,5}", v.strip()) else "name"
                refref[kind] += refused(a) and bool(REF.search(a["res"]))
                break
for kind in ("id", "name"):
    if kinds[kind]:
        print(f"N14 reference-shaped refusals by {kind}: {refref[kind]} of {kinds[kind]} "
              f"({100*refref[kind]/kinds[kind]:.1f}%)")
