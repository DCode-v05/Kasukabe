"""Did the town actually talk? Six gates, read off a COMPLETED run.

Written for the autonomous loop (docs/AUTONOMOUS_LOOP.md). Every number comes
from the trace store, which means it can be run after the world has stopped -
Build Spec v2 section 10.1 forbids grading a mid-run sample as a result, and
this project has made that mistake three times, so the tool that decides
pass/fail deliberately cannot be pointed at a live server.

    uv run python loop_report.py data/loop1.db

Exit code 0 when every gate passes, 1 otherwise, so the loop can branch on it.
"""

from __future__ import annotations

import collections
import json
import re
import sqlite3
import sys

# Gates. See docs/AUTONOMOUS_LOOP.md for why each bar sits where it does.
REPLY_RATE_MIN = 0.60
DEEP_EXCHANGES_MIN = 3
DEEP_EXCHANGE_TURNS = 4
# G3: three or more distinct speakers in one place, one window.
#
# EVAL-12: THIS GATE AND B3/B4 REWARD OPPOSITE THINGS, and the series has
# been graded against both.
#
# G3 has a floor of three speakers and NO CEILING, so a thread grows
# without limit and scores better for it. `evals/behavioural.py`'s B3
# fails a town whose median conversation runs above FOUR speakers -
# Dunbar, observed mean 3.4, fissioning at five - and B4 fails one whose
# over-large threads do not split. Run 23's 98-turn, 17-person bakery
# thread passes G3 and fails B3 and B4 on the same evidence.
# `HUMAN_BASELINES.md` section 2.2 already names the contradiction.
#
# The review's instruction is "decide one", and that is a research
# decision rather than a repair - whether the town should be measured
# against what a human group does, or against whether the town can hold a
# conversation at all. Nobody has made it. What is fixed here is that the
# conflict is now visible from BOTH ends: `behavioural.py` has said so
# since it was written and this file did not, so a reader of the gates saw
# no conflict at all.
#
# `test_the_two_conversation_gates_disagree` fails if either number moves
# without the other, so the decision cannot be made by accident.
GROUP_SPEAKERS_MIN = 3
SETTLEMENTS_MIN = 1
WORLD_EFFECT_MIN = 0.50

# Two remarks belong to the same gathering if they share a place and fall
# inside this many ticks of each other. Matches the conversation window `say`
# itself uses to thread a reply.
GATHERING_WINDOW_TICKS = 8
# A spoken price and the payment that settles it, at most this far apart.
SETTLEMENT_WINDOW_TICKS = 20

# The transaction types that can SETTLE a bargain. `transfer` is
# deliberately absent: it is the tool for handing somebody money for
# nothing, and counting it would let G4 pass on a gift.
#
# `purchase` and `sale` were added after final1, which had two successful
# `sell` calls and a trace holding only `burn` and `transfer` - both shop
# tools recorded TransactionReason.TRANSFER, so a bargain settled at the
# bakery counter, which is where most bargains in a town with shops would
# be settled, was indistinguishable from a handout and scored zero for
# the whole diagnostic series. The BAR is unchanged at one settlement;
# this is a detector that could not see what it was pointed at, the same
# correction already made for `trade`.
SETTLING_TX_TYPES = ("trade", "purchase", "sale")


def rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    """Query, or an empty list for a table this store does not have.

    Tolerant on purpose: stores written by older builds are missing whole
    tables, and a report that cannot read one of them should still print
    the rest.

    It is NOT tolerant of a missing COLUMN, and that distinction is the
    whole of this function. A missing table answers "there is nothing to
    count"; a missing column answers "0" to a question the store could
    have answered, which is the silent default that has cost this project
    a dormancy counter, six invisible trades, and every purchase in the
    diagnostic series. If this raises, re-run the report against a store
    written by the current schema.
    """
    try:
        return list(conn.execute(sql))
    except sqlite3.OperationalError as exc:
        if "no such column" in str(exc):
            raise SystemExit(
                f"this trace store predates the current schema: {exc}. "
                "Grading it would report 0 for things it simply cannot see. "
                "Re-run against a store written by this build."
            ) from exc
        return []
    except sqlite3.Error:
        return []


def utterances(conn):
    return [
        {
            "speaker": r[0], "listener": r[1], "location": r[2],
            "content": r[3] or "", "tick": r[4] or 0,
            "addressed": _addressees(r[5]),
        }
        for r in rows(
            conn,
            # EVAL-11: `addressed_agent_ids` too. A remark can name several
            # people and `listener_agent_id` holds one of them; run 23 filled
            # the list on 172 of 229 utterances, and G1 counted none of them.
            "select speaker_agent_id, listener_agent_id, location, content, "
            "tick, addressed_agent_ids "
            "from utterances order by tick, id",
        )
    ]


def _addressees(raw) -> list[str]:
    """`addressed_agent_ids` as a list, however the row stored it.

    EVAL-11. The column is JSON text on SQLite and a list once decoded, and
    a report that assumed either one silently counted zero addressees.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in parsed if x] if isinstance(parsed, list) else []


def names(conn) -> dict[str, str]:
    return {r[0]: r[1] for r in rows(conn, "select id, display_name from agents")}


def reply_rate(utts) -> tuple[int, int, list]:
    """A directed question is answered when its addressee later speaks TO the
    asker. Same definition WorldState.open_questions uses to decide a question
    is closed, deliberately: a mechanism scored by one rule and driven by
    another is marking its own homework."""
    # EVAL-11. Everyone the remark named, not just the primary listener.
    #
    # `addressed_agent_ids` has been on the record since the addressee work
    # and G1 read only `listener_agent_id`, so a remark aimed at three
    # people counted as one question owed by one person - and the two
    # people who were also asked could answer without it ever registering.
    # Run 23 carried the list on 172 of its 229 utterances.
    def asked_of(u) -> list[str]:
        who = list(u["addressed"]) or ([u["listener"]] if u["listener"] else [])
        return [w for w in dict.fromkeys(who) if w]

    directed = [u for u in utts if asked_of(u)]
    by_speaker = collections.defaultdict(list)
    for u in utts:
        by_speaker[u["speaker"]].append(u)
    answered = []
    for u in directed:
        # Answered when ANY of the people it named later speaks to the
        # asker. One reply closes the question, which is the same rule
        # WorldState.open_questions uses.
        if any(
            r["tick"] > u["tick"] and r["listener"] == u["speaker"]
            for addressee in asked_of(u)
            for r in by_speaker.get(addressee, [])
        ):
            answered.append(u)
    return len(answered), len(directed), directed


def exchanges(utts) -> list[list[dict]]:
    """Group remarks into gatherings by place and proximity in time. NOT by
    conversation_id: `say` mints a fresh id per utterance unless it can find a
    prior one, so a thread keyed on it fragments."""
    out: list[list[dict]] = []
    for u in utts:
        for group in reversed(out):
            if (
                group[-1]["location"] == u["location"]
                and u["tick"] - group[-1]["tick"] <= GATHERING_WINDOW_TICKS
            ):
                group.append(u)
                break
        else:
            out.append([u])
    return out


def alternating_turns(group: list[dict]) -> int:
    """Turns where the speaker changed. Four people each saying one thing is a
    conversation; one person saying four things is not."""
    turns = 0
    last = None
    for u in group:
        if u["speaker"] != last:
            turns += 1
            last = u["speaker"]
    return turns


def settlements(conn, utts) -> list[dict]:
    """Talk between two people, then a bargain settled between those same two.

    WIDENED, and the reason is a defect in this function rather than a
    result I did not like. The first version required the transaction amount
    to equal a number spoken verbatim. Marta said "45 per unit is fair for
    fresh cut" about ten units of wheat; the trade that settles it moves 450,
    which that test scores as no settlement at all. It was measuring string
    equality and calling it economics.

    A `trade` is now counted directly: it is the atomic two-sided swap, so a
    trade between two residents who were talking to each other IS the
    bargain being struck. An exact spoken-number match is still recorded
    separately, as the stronger signal, because "they said 450 and moved
    450" is worth telling apart from "they talked and then traded".

    Build Spec v2 section 10.4 forbids moving a THRESHOLD after seeing a
    result. The bar here is unchanged at one settlement; what changed is a
    detector that could not see the thing it was pointed at.
    """
    # A shop's owner stands in for the shop. A resident who bargains with
    # Marta and then pays her bakery has settled with Marta - the counter
    # is where the money moves, and the business is the person who owns
    # it as far as a bargain is concerned.
    owner_of = {
        r[0]: r[1]
        for r in rows(conn, "select id, owner_agent_id from businesses")
        if r[1]
    }

    def party(agent_id, counterparty):
        """The person on this side of a move, shop or no shop."""
        return agent_id or owner_of.get(counterparty or "")

    txs = []
    for r in rows(
        conn,
        "select from_agent_id, to_agent_id, amount, tick, tx_type, counterparty_id "
        "from transactions where from_agent_id is not null or to_agent_id is not null",
    ):
        # ONE side may be a business. The query used to require both agent
        # columns, which excluded every purchase and sale outright, so
        # widening SETTLING_TX_TYPES alone would have changed nothing.
        frm, to = party(r[0], r[5]), party(r[1], r[5])
        if not frm or not to or frm == to:
            continue
        txs.append({"frm": frm, "to": to, "amount": r[2], "tick": r[3] or 0, "kind": r[4] or ""})
    found = []
    # A trade between two people who had been talking. The trade tool is the
    # settlement mechanism, so this is the bargain itself, not a proxy.
    # EVERY tick a pair spoke, not just the latest. Keying on the most
    # recent one meant a settled bargain stopped counting the moment those
    # two said anything else - the count went 2, then 1, as the run went on
    # and the town kept talking. A settlement that happened does not
    # un-happen because its participants spoke again later.
    spoke_between: dict[frozenset, list[int]] = collections.defaultdict(list)
    for u in utts:
        if u["listener"]:
            spoke_between[frozenset((u["speaker"], u["listener"]))].append(u["tick"])
    for tx in txs:
        if tx["kind"] not in SETTLING_TX_TYPES:
            continue
        pair = frozenset((tx["frm"], tx["to"]))
        said_at = next(
            (t for t in reversed(spoke_between.get(pair, []))
             if 0 <= tx["tick"] - t <= SETTLEMENT_WINDOW_TICKS),
            None,
        )
        if said_at is not None:
            found.append({
                "speaker": tx["frm"], "listener": tx["to"], "content": f"({tx['kind']})",
                "tick": said_at, "amount": tx["amount"], "tx_tick": tx["tick"],
            })
    for u in utts:
        spoken = {int(n) for n in re.findall(r"\b(\d{2,6})\b", u["content"])}
        if not spoken:
            continue
        for tx in txs:
            if not (0 <= tx["tick"] - u["tick"] <= SETTLEMENT_WINDOW_TICKS):
                continue
            pair = {tx["frm"], tx["to"]}
            if u["listener"] and pair != {u["speaker"], u["listener"]}:
                continue
            if u["speaker"] not in pair:
                continue
            if tx["amount"] in spoken:
                found.append({**u, "amount": tx["amount"], "tx_tick": tx["tick"]})
                break
    return found


def world_effect(conn) -> tuple[int, int, collections.Counter]:
    """Share of COGNITION turns that produced a tool call. Background work
    (the learning centre's post-turn summariser) is excluded: it never calls
    tools, and counting it made earlier reports read far worse than the town
    deserved."""
    acted = total = 0
    tools: collections.Counter = collections.Counter()
    for body, call_class in rows(
        conn, "select response_body, call_class from llm_calls where success=1"
    ):
        try:
            payload = json.loads(body) or {}
        except (TypeError, json.JSONDecodeError):
            continue
        if call_class != "cognition":
            continue
        total += 1
        hit = False
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tools[block.get("name")] += 1
                hit = True
        acted += 1 if hit else 0
    return acted, total, tools


def main(path: str) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    who = names(conn)
    utts = utterances(conn)
    calls, ok, spend = rows(
        conn, "select count(*), sum(success), sum(cost_nano_dollars)/1e9 from llm_calls"
    )[0]
    tick = (rows(conn, "select max(tick) from llm_calls") or [(0,)])[0][0] or 0

    print(f"\n{'=' * 66}\nRUN {path}\n{'=' * 66}")
    print(f"calls {calls}  ok {ok}  spend ${spend or 0:.2f}  last tick {tick}")
    print(f"utterances {len(utts)}")

    answered, directed, _ = reply_rate(utts)
    rate = answered / directed if directed else 0.0
    groups = exchanges(utts)
    deep = [g for g in groups if alternating_turns(g) >= DEEP_EXCHANGE_TURNS]
    gatherings = [g for g in groups if len({u["speaker"] for u in g}) >= GROUP_SPEAKERS_MIN]
    settled = settlements(conn, utts)
    alive = rows(conn, "select count(*) from agents where is_alive=1")
    alive_n = alive[0][0] if alive else 0
    total_agents = rows(conn, "select count(*) from agents")[0][0]
    acted, cog, tools = world_effect(conn)
    effect = acted / cog if cog else 0.0

    # EVAL-13. A gate that cannot fail is arithmetic, not evidence.
    #
    # G5 asks whether everybody is alive at the stop. Death needs 24 game-
    # hours of dormancy (DESIGN section 3) and no run has reached tick 144,
    # so G5 has passed 20 runs out of 20 and measured nothing in any of
    # them. Reported as NOT EXERCISED until a run is long enough to put the
    # question, because "20/20 alive" and "nobody could have died" are
    # different sentences and only one of them is true.
    # 144 ticks a game-day (ten game-minutes a tick, DESIGN section 1).
    # Read from the utterances/calls the report already loaded rather
    # than imported, because this script runs against a trace file with
    # no world package in the path.
    TICKS_PER_DAY = 144
    max_tick = tick
    day_reached = max_tick >= TICKS_PER_DAY
    gates = [
        ("G1 reply rate", f"{answered}/{directed} = {rate:.0%}", rate >= REPLY_RATE_MIN),
        ("G2 exchanges 4+ turns", f"{len(deep)}", len(deep) >= DEEP_EXCHANGES_MIN),
        ("G3 group (3+ speakers)", f"{len(gatherings)}", len(gatherings) >= 1),
        ("G4 settlements", f"{len(settled)}", len(settled) >= SETTLEMENTS_MIN),
        (
            "G5 alive at stop",
            f"{alive_n}/{total_agents}" if day_reached
            else f"{alive_n}/{total_agents} (tick {max_tick} < {TICKS_PER_DAY})",
            (alive_n == total_agents and total_agents > 0) if day_reached else None,
        ),
        ("G6 turns w/ effect", f"{acted}/{cog} = {effect:.0%}", effect >= WORLD_EFFECT_MIN),
    ]
    print(f"\n{'GATE':<26}{'VALUE':<20}RESULT")
    for name, value, passed in gates:
        verdict = "NOT EXERCISED" if passed is None else ("PASS" if passed else "FAIL")
        print(f"{name:<26}{value:<20}{verdict}")

    print(f"\ntool calls: {dict(tools.most_common(12))}")
    if gatherings:
        g = max(gatherings, key=lambda x: len({u['speaker'] for u in x}))
        print(f"\nlargest gathering: {len({u['speaker'] for u in g})} speakers "
              f"at {g[0]['location']}, ticks {g[0]['tick']}-{g[-1]['tick']}")
        for u in g[:6]:
            print(f"   {who.get(u['speaker'], u['speaker'])} -> "
                  f"{who.get(u['listener']) or 'aloud'}: {u['content'][:110]}")
    if settled:
        s = settled[0]
        print(f"\nsettlement: {who.get(s['speaker'], s['speaker'])} said {s['amount']}, "
              f"paid at tick {s['tx_tick']}")

    failed = [n for n, _, p in gates if not p]
    print(f"\n{'ALL GATES PASS' if not failed else 'FAILED: ' + ', '.join(failed)}\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/loop1.db"))
