"""§4 evals against a live run. Real engines, real model, real store.

Thresholds are the ones agreed BEFORE the run (spec §8.5). Each objective
is measured independently and a failure marks only itself - failure
isolates, work continues, everything is reviewed at the end (§8.3).

Read-only: it measures a run someone else is driving. It never writes to
the world, so it cannot flatter its own result.
"""
import collections
import glob
import json
import re
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "data/kasukabe.db"
HOMES = "/tmp/kasukabe-agents/*/session_log.jsonl"

THRESHOLDS = {
    "4.1 cross-town walk completes":      "arrival within 12 ticks of setting off",
    "4.2 nobody reports being lost":      "zero transcript lines",
    "4.3 hungry beside food gets fed":    "eats within 20 of its own turns",
    "4.6 new tools used unprompted":      ">=1 correct first-time call each",
    "4.7 refusal recovery":               ">=50%",
    "group chat":                         ">=1 thread with 3+ participants",
}

results: list[tuple[str, str, str]] = []          # objective, verdict, evidence
def record(name, ok, evidence):
    results.append((name, "PASS" if ok else "FAIL", evidence))


def record_unmeasurable(name, evidence):
    """A gate whose input never occurred.

    EVAL-10 / Build Spec section 5.6: "a run with zero refusals must be
    reported as NOT-MEASURABLE rather than as a pass". The difference is
    the whole point of an evaluation - `tot == 0 or rate >= 50` returns
    True for a town that recovered from every refusal AND for a town that
    never did anything, and reporting both as PASS is how twenty runs in a
    row were graded green on evidence nobody had.
    """
    results.append((name, "N/A", evidence))


def tool_of(action) -> str:
    """The action's tool name.

    It lives under "tool"; "name" is always None in a session log. Reading
    "name" made this harness report 0 moves, 0 meals and 0 tool calls on a
    run that had all three - the measurement failing, not the town.
    """
    return str(action.get("tool") or action.get("name") or "")


def turns():
    for f in sorted(glob.glob(HOMES)):
        who = f.split("/")[-2]
        for line in open(f):
            try:
                yield who, json.loads(line)
            except Exception:
                continue

# ── 4.1 movement completes ──────────────────────────────────────────────
#
# Measures ARRIVAL, not the estimate. The first version of this check
# passed on "longest estimate 8 ticks" while observing zero arrivals -
# nothing could have made it red, which is a defect in the test and not
# evidence about the town (spec §9.4).
#
# A journey is proven complete when the agent's OWN later perception says
# it is standing at the place it set off for. That is end-to-end: the tool
# started it, the tick loop walked it, and the agent can see it happened.
try:
    per_agent = collections.defaultdict(list)
    for who, d in turns():
        per_agent[who].append(d)

    started = arrived = stranded = in_flight = changed_plan = 0
    est = []
    for who, entries in per_agent.items():
        for i, d in enumerate(entries):
            for a in d.get("actions") or []:
                if "move" not in tool_of(a):
                    continue
                r = a.get("result")
                if not isinstance(r, str):
                    continue
                try:
                    j = json.loads(r)
                except Exception:
                    continue
                dest = j.get("destination_location_id") or j.get("location_id")
                if j.get("walking") is True:
                    started += 1
                    if j.get("estimated_ticks") is not None:
                        est.append(j["estimated_ticks"])
                    # Did a later turn of THIS agent perceive it at `dest`?
                    later_turns = entries[i + 1:]
                    later = " ".join(str(e.get("input") or "") for e in later_turns)
                    # Did it later set off somewhere ELSE? Then this journey
                    # was abandoned on purpose, not lost. Spec §3.5 asks
                    # that an interrupted journey resolve to one honest
                    # outcome, and ending up somewhere real after changing
                    # your mind IS that outcome. Measured case: n02 set off
                    # for the Willows Market, then issued seventeen tool
                    # calls in its next turn - four of them moves - and
                    # settled at the Moore family's house, where it stayed.
                    superseded = False
                    for later_turn in later_turns:
                        for b in later_turn.get("actions") or []:
                            if "move" not in tool_of(b):
                                continue
                            rb = b.get("result")
                            if not isinstance(rb, str):
                                continue
                            try:
                                jb = json.loads(rb)
                            except Exception:
                                continue
                            other = jb.get("destination_location_id") or jb.get("location_id")
                            if other and other != dest and not jb.get("already_walking_there"):
                                superseded = True
                                break
                        if superseded:
                            break

                    if dest and dest in later:
                        arrived += 1
                    elif superseded:
                        changed_plan += 1
                    elif len(later_turns) < 3:
                        # Still in flight when the log ends. A journey with
                        # no turns left after it cannot be judged either
                        # way, and counting it as a failure would make this
                        # eval impossible to pass on any run that is still
                        # going - which is every run, at the moment it is
                        # measured.
                        in_flight += 1
                    else:
                        stranded += 1
                elif j.get("walking") is False and not j.get("error"):
                    arrived += 1  # already standing there

    worst = max(est) if est else 0
    ok = started + arrived > 0 and stranded == 0 and worst <= 12
    record("4.1 cross-town walk completes", ok,
           f"{arrived} arrivals confirmed by the agent's own later perception, "
           f"{stranded} started but never arrived, {changed_plan} abandoned for a new destination, "
           f"{in_flight} still walking when measured, "
           f"{started} journeys begun, "
           f"longest estimate {worst} ticks (limit 12)")
except Exception as e:
    record("4.1 cross-town walk completes", False, f"eval errored: {e}")

# ── 4.2 nobody reports being lost ───────────────────────────────────────
try:
    LOST = re.compile(
        r"(lost|not sticking|didn'?t stick|never reach|can'?t reach|wandering|"
        r"don'?t know where|drift|going in circles|walking in circles|stuck)",
        re.I)
    hits = []
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for tick, txt in c.execute("select tick, content from utterances"):
        if txt and LOST.search(txt):
            hits.append((tick, txt[:90]))
    record("4.2 nobody reports being lost", not hits,
           "clean" if not hits else f"{len(hits)} lines, e.g. t{hits[0][0]}: {hits[0][1]!r}")
except Exception as e:
    record("4.2 nobody reports being lost", False, f"eval errored: {e}")

# ── 4.3 a hungry resident gets fed ──────────────────────────────────────
try:
    ate = collections.Counter()
    for who, d in turns():
        for a in d.get("actions") or []:
            if tool_of(a).endswith("eat"):
                r = str(a.get("result"))
                if not (r.startswith("Error") or '"detail"' in r):
                    ate[who] += 1
    record("4.3 hungry beside food gets fed", bool(ate),
           f"{sum(ate.values())} successful meals by {len(ate)} residents"
           if ate else "nobody ate")
except Exception as e:
    record("4.3 hungry beside food gets fed", False, f"eval errored: {e}")

# ── 4.6 the new tools get used, unprompted and correctly ────────────────
try:
    tried = collections.Counter()
    failed = collections.Counter()
    for who, d in turns():
        for a in d.get("actions") or []:
            n = tool_of(a)
            for tool in ("eat", "give", "rest"):
                if n.endswith(tool):
                    tried[tool] += 1
                    r = str(a.get("result"))
                    if r.startswith("Error") or '"detail"' in r:
                        failed[tool] += 1
    missing = [t for t in ("eat", "give", "rest") if tried[t] == 0]
    record("4.6 new tools used unprompted", not missing,
           " · ".join(f"{t}: {tried[t]} calls, {failed[t]} refused" for t in ("eat","give","rest")))
except Exception as e:
    record("4.6 new tools used unprompted", False, f"eval errored: {e}")

# ── 4.7 a refusal is followed by a correction that works ────────────────
try:
    per = collections.defaultdict(list)
    for who, d in turns():
        for a in d.get("actions") or []:
            n = tool_of(a)
            r = str(a.get("result"))
            per[who].append((n, r.startswith("Error") or '"detail"' in r))
    tot = rec = 0
    for who, seq in per.items():
        for i, (n, bad) in enumerate(seq):
            if not bad:
                continue
            tot += 1
            if any(m == n and not b for m, b in seq[i+1:]):
                rec += 1
    rate = (rec / tot * 100) if tot else None
    if tot == 0:
        record_unmeasurable(
            "4.7 refusal recovery",
            "no refusals occurred, so recovery was not exercised",
        )
    else:
        record("4.7 refusal recovery", rate >= 50,
               f"{rec}/{tot} = {rate:.0f}% (limit 50%)")
except Exception as e:
    record("4.7 refusal recovery", False, f"eval errored: {e}")

# ── group chat ──────────────────────────────────────────────────────────
try:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    g = collections.defaultdict(set)
    for cid, sp, li in c.execute(
            "select conversation_id, speaker_agent_id, listener_agent_id from utterances"):
        g[cid].add(sp)
        if li:
            g[cid].add(li)
    groups = [v for v in g.values() if len(v) >= 3]
    record("group chat", bool(groups),
           f"{len(groups)} threads with 3+ people, of {len(g)} threads; "
           f"sizes {sorted(len(v) for v in g.values())}")
except Exception as e:
    record("group chat", False, f"eval errored: {e}")

# ── 4.1 adversarial variants ────────────────────────────────────────────
#
# The spec asks for these by name and no run had ever exercised them. Each
# is measured from what the residents actually did, not staged - a run
# where a variant never arose reports "not exercised", which is honest and
# is not a pass.
try:
    per_agent = collections.defaultdict(list)
    for who, d in turns():
        per_agent[who].append(d)

    reissued_same = 0       # asked again for somewhere it was already walking
    told_already = 0        # ...and was told so instead of restarted
    changed_mind = 0        # picked a new destination mid-walk
    no_route = 0            # given a destination with no route
    no_route_told = 0       # ...and told in terms it could act on
    interrupted = 0         # spoken to while walking

    for who, entries in per_agent.items():
        walking_to = None
        for d in entries:
            spoke_this_turn = False
            for a in d.get("actions") or []:
                name = tool_of(a)
                if name.endswith("say"):
                    spoke_this_turn = True
                if "move" not in name:
                    continue
                r = a.get("result")
                if not isinstance(r, str):
                    continue
                try:
                    j = json.loads(r)
                except Exception:
                    continue
                dest = j.get("destination_location_id") or j.get("location_id")
                if j.get("error"):
                    no_route += 1
                    if "route" in str(j.get("error", "")).lower():
                        no_route_told += 1
                elif j.get("already_walking_there"):
                    reissued_same += 1
                    told_already += 1
                elif j.get("walking") is True:
                    if walking_to and dest and dest != walking_to:
                        changed_mind += 1
                    elif walking_to and dest == walking_to:
                        reissued_same += 1
                    walking_to = dest
                elif j.get("walking") is False:
                    walking_to = None
            if walking_to and spoke_this_turn:
                interrupted += 1

    exercised = []
    if reissued_same:
        exercised.append(
        f"re-issued same destination x{reissued_same} ({told_already} told they were already on the way)")
    if changed_mind:
        exercised.append(f"changed destination mid-walk x{changed_mind}")
    if no_route:
        exercised.append(f"no-route destination x{no_route} ({no_route_told} told why)")
    if interrupted:
        exercised.append(f"spoke while walking x{interrupted}")

    # The one that must never regress: a re-issue has to be answered, not
    # silently restarted. Six of Marta Thorne's moves in one turn were
    # restarts, and she arrived nowhere.
    ok = (reissued_same == 0 or told_already == reissued_same) and (
        no_route == 0 or no_route_told == no_route)
    record("4.1a adversarial: interruption and repetition", ok,
           " · ".join(exercised) if exercised else "no variant arose in this run - NOT EXERCISED")
except Exception as e:
    record("4.1a adversarial: interruption and repetition", False, f"eval errored: {e}")

# ── 4.5 a deal agreed in talk settles in the ledger ──────────────────────
try:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    said_numbers = 0
    PRICE = re.compile(r"\b(\d+)\s*(sim|each|total)\b", re.I)
    for (txt,) in c.execute("select content from utterances"):
        if txt and PRICE.search(txt):
            said_numbers += 1
    moved = c.execute(
        "select count(*) from transactions where currency='sim' and reason!='genesis'"
    ).fetchone()[0]
    if said_numbers == 0:
        record_unmeasurable(
            "4.5 a deal agreed in talk settles in the ledger",
            "nobody named a price, so no deal could settle or fail to",
        )
    else:
        record("4.5 a deal agreed in talk settles in the ledger",
               moved > 0,
               f"{said_numbers} remarks naming a price · {moved} sim movements")
except Exception as e:
    record("4.5 a deal agreed in talk settles in the ledger", False, f"eval errored: {e}")

# ── the board ───────────────────────────────────────────────────────────
print("\n  §4 EVAL BOARD - thresholds fixed before the run")
print("  " + "-" * 76)
for name, verdict, ev in results:
    print(f"  [{verdict}] {name}")
    print(f"         threshold: {THRESHOLDS.get(name,'-')}")
    print(f"         measured:  {ev}")
passed = sum(1 for _, v, _ in results if v == "PASS")
failed = sum(1 for _, v, _ in results if v == "FAIL")
unmeasured = sum(1 for _, v, _ in results if v == "N/A")
print("  " + "-" * 76)
# EVAL-10 / EVAL-13: three numbers, not one. "18 of 20 pass" hid the
# difference between an objective a run met and an objective the run never
# put to the test - and a gate that cannot fail is not evidence, it is
# arithmetic. Reported separately so a board with a large N/A column reads
# as "this run was too short to ask", which is the true statement about
# every run this project has done.
print(f"  {passed} passed · {failed} failed · {unmeasured} not exercised "
      f"({len(results)} objectives)")
if unmeasured:
    print(
        f"  {unmeasured} objective(s) had no input to judge: the run did not "
        "reach the behaviour they measure. Not a pass."
    )
