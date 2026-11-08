"""Does the town behave the way people do? Seven measures, from a COMPLETED run.

    uv run python -m world.evals.behavioural data/final1.db

Every bar traces to a row in docs/HUMAN_BASELINES.md, and every row there
traces to published research. That is the strongest form of "threshold
fixed in advance" available to this project: these numbers were settled by
people who had never heard of Kasukabe, years before it existed, so they
cannot have been chosen to flatter a run.

WHAT THIS IS FOR. Fifteen run reports have said the town "is not behaving
like humans" with nowhere to say what humans do, so every judgement was
against somebody's intuition. This replaces the intuition with citations.

WHAT IT IS NOT. It does not replace `loop_report.py`. That grades the six
gates the project set for itself, which are about whether the machinery
works: are remarks delivered, does anybody die, do turns touch the world.
This asks a different question - given that the machinery works, does the
RESULT look like people - and it can fail while every gate passes.

READ OFF A COMPLETED STORE ONLY. Build Spec v2 section 10.1, and this
project has broken it three times, twice from the harness. Pointed at a
live run, the numbers are about the run's first minute.

A LIMIT, STATED. Residents are not humans and this does not claim they
should score identically. It claims something narrower: where the town
falls outside a range measured across ten languages and five continents,
that is worth a look, and B2 and B3 encode places where our own design
contradicts a human universal.
"""

from __future__ import annotations

import collections
import json
import re
import sqlite3
import sys
from typing import Any

# --------------------------------------------------------------------
# Bars. Each cites docs/HUMAN_BASELINES.md, which cites its source.
# --------------------------------------------------------------------

# B1. Stivers et al., PNAS 106(26) 2009: across ten languages, answers
# were 64% (Korean) to 87% (Dutch, Yeli-Dnye) of all responses. 64 is the
# floor of the slowest-answering language measured, not an average, so a
# town below it is outside the human range rather than merely at its edge.
ANSWER_RATE_FLOOR = 0.64

# B3. Dunbar: spontaneous conversation caps at about four, observed mean
# 3.4 (one speaker plus 2.4 listeners); at five it fissions into two.
# This is the measure our own G3 gets backwards - G3 has a floor of three
# speakers and NO ceiling, so a six- or eight-speaker thread scores as a
# success when a human group that size would already have split.
CONVERSATION_SIZE_CEILING = 4

# B4. What share of over-large threads behave the way humans do and break
# up. Half, because the research says fission is what happens, not what
# usually happens, and a first bar should be reachable.
FISSION_SHARE_FLOOR = 0.50

# A thread is "the same conversation" for these purposes when the world
# says so. Unlike loop_report's gathering heuristic, this reads the
# world's own conversation_id, because the question here is about the
# object the town maintains, not about a window we impose on it.

# How far apart two remarks can be and still count as one bout of the
# conversation, for the fission measure. Matches CONVERSATION_WINDOW_TICKS.
BOUT_WINDOW_TICKS = 12

# B5. A price named in speech and then moved by a tool call.
SETTLING_TX_TYPES = ("trade", "purchase", "sale")
SETTLEMENT_WINDOW_TICKS = 20

# What counts as naming a price: two to six digits standing alone. Same
# rule loop_report uses, so the two cannot disagree about what a price is.
PRICE = re.compile(r"\b(\d{2,6})\b")


def rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    """Query, or an empty list for a table this store does not have.

    A missing COLUMN raises rather than answering zero - see
    loop_report.rows for why that distinction has cost this project a
    dormancy counter, six invisible trades and every purchase in a
    ten-run series.
    """
    try:
        return list(conn.execute(sql))
    except sqlite3.OperationalError as exc:
        if "no such column" in str(exc):
            raise SystemExit(
                f"this trace store predates the current schema: {exc}. Grading "
                "it would report 0 for things it cannot see. Re-run against a "
                "store written by this build."
            ) from exc
        return []
    except sqlite3.Error:
        return []


def _addressees(raw: Any) -> list[str]:
    """`addressed_agent_ids` as a list, however the row stored it.

    EVAL-11. SQLite keeps the column as JSON text; a reader that assumed a
    list counted zero addressees on every row and never said so.
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


def _selected_by(u: dict) -> list[str]:
    """Every resident this remark put an obligation on.

    Sacks/Schegloff/Jefferson's "current speaker selects next" does not
    stop at one: a remark naming two people selects two, and both owe an
    answer. B1 read `listener_agent_id` alone, so a remark that named three
    residents counted as one selection - and an answer from either of the
    other two did not count at all.
    """
    who = list(u.get("addressed") or [])
    if not who and u.get("listener"):
        who = [u["listener"]]
    return [w for w in dict.fromkeys(who) if w]


def utterances(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "speaker": r[0], "listener": r[1], "conversation": r[2],
            "location": r[3], "content": r[4] or "", "tick": r[5] or 0,
            # EVAL-11: everyone the remark named. `listener_agent_id` holds
            # one of them; run 23 carried more on 172 of 229 rows.
            "addressed": _addressees(r[6]),
        }
        for r in rows(
            conn,
            "select speaker_agent_id, listener_agent_id, conversation_id, "
            "location, content, tick, addressed_agent_ids "
            "from utterances order by tick, id",
        )
    ]


def names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in rows(conn, "select id, display_name from agents")}


# --------------------------------------------------------------------
# B1 - being asked, and answering
# --------------------------------------------------------------------


def selections(utts: list[dict]) -> list[dict]:
    """Remarks that SELECT a next speaker.

    Sacks, Schegloff and Jefferson (1974): a vocative is the simplest form
    of "current speaker selects next", and the party so selected "has
    rights, and is obliged, to take next turn to speak, and no others have
    such rights or obligations".

    So the denominator is not every remark - it is every remark that put
    an obligation on somebody. "Morning, all" selects nobody and owes
    nobody an answer, and counting it would drag the rate down for a
    behaviour that is perfectly human.
    """
    return [u for u in utts if _selected_by(u)]


def answer_rate(utts: list[dict]) -> tuple[int, int, list[dict]]:
    """How often the selected party answers the person who selected them.

    Same definition WorldState.open_questions uses to decide a question is
    closed, deliberately: a mechanism scored by one rule and driven by
    another is marking its own homework.
    """
    by_speaker: dict[str, list[dict]] = collections.defaultdict(list)
    for u in utts:
        by_speaker[u["speaker"]].append(u)

    selected = selections(utts)
    unanswered = []
    answered = 0
    for u in selected:
        # EVAL-11: an answer from ANY selected party closes the obligation.
        if any(
            r["tick"] > u["tick"] and r["listener"] == u["speaker"]
            for addressee in _selected_by(u)
            for r in by_speaker.get(addressee, [])
        ):
            answered += 1
        else:
            unanswered.append(u)
    return answered, len(selected), unanswered


# --------------------------------------------------------------------
# B2 - a named resident is told they were named
# --------------------------------------------------------------------


def unrecorded_addressees(conn: sqlite3.Connection) -> int:
    """Remarks whose recorded addressee list is narrower than the words.

    This is the mechanism check, and it reads the EVENTS rather than the
    utterances table, because `addressed_agent_ids` lives on the event.
    A run that predates the fix answers -1 rather than 0: no evidence is
    not the same as no defect, and a zero here would read as a pass.
    """
    seen = False
    short = 0
    for (payload,) in rows(
        conn, "select payload from events where type='agent.utterance.spoken'"
    ):
        try:
            body = json.loads(payload) or {}
        except (TypeError, json.JSONDecodeError):
            continue
        if "addressed_agent_ids" not in body:
            continue
        seen = True
        listed = body.get("addressed_agent_ids") or []
        involved = body.get("involved_agent_ids") or []
        if any(a not in involved for a in listed):
            short += 1
    return short if seen else -1


# --------------------------------------------------------------------
# B3, B4 - how many people one conversation holds
# --------------------------------------------------------------------


def threads(utts: list[dict]) -> dict[str, list[dict]]:
    """The world's own conversations, by id."""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for u in utts:
        if u["conversation"]:
            out[str(u["conversation"])].append(u)
    return dict(out)


def sizes(utts: list[dict]) -> list[int]:
    return [len({u["speaker"] for u in remarks}) for remarks in threads(utts).values()]


def fissioned(utts: list[dict]) -> tuple[int, int]:
    """(over-large threads that split, over-large threads).

    A thread has FISSIONED when, having exceeded the human ceiling, the
    room goes on to carry a second open conversation - which is what
    Dunbar observed: "groups tended to partition into new conversational
    cliques at multiples of about four individuals."

    Measured on the world's records rather than inferred from timing,
    because a split is a thing the conversation registry can actually
    show: two ids alive in one place at once.
    """
    big = 0
    split = 0
    for remarks in threads(utts).values():
        if len({u["speaker"] for u in remarks}) <= CONVERSATION_SIZE_CEILING:
            continue
        big += 1
        where = remarks[0]["location"]
        span = range(remarks[0]["tick"], remarks[-1]["tick"] + 1)
        others = {
            u["conversation"]
            for u in utts
            if u["location"] == where
            and u["tick"] in span
            and u["conversation"] != remarks[0]["conversation"]
        }
        if others:
            split += 1
    return split, big


# --------------------------------------------------------------------
# B5 - a price named, then moved
# --------------------------------------------------------------------


def settlements(conn: sqlite3.Connection, utts: list[dict]) -> tuple[int, int]:
    """(prices named in speech that a tool call then moved, prices named).

    Human bargaining ends in a transfer; that is what distinguishes a
    bargain from a conversation about one. Run 19 named prices all morning
    and moved nothing.
    """
    owner_of = {
        r[0]: r[1]
        for r in rows(conn, "select id, owner_agent_id from businesses")
        if r[1]
    }

    txs = []
    for r in rows(
        conn,
        "select from_agent_id, to_agent_id, amount, tick, tx_type, counterparty_id "
        "from transactions where from_agent_id is not null or to_agent_id is not null",
    ):
        frm = r[0] or owner_of.get(r[5] or "")
        to = r[1] or owner_of.get(r[5] or "")
        if frm and to and frm != to and (r[4] or "") in SETTLING_TX_TYPES:
            txs.append({"pair": {frm, to}, "amount": r[2], "tick": r[3] or 0})

    named = 0
    moved = 0
    for u in utts:
        spoken = {int(n) for n in PRICE.findall(u["content"])}
        if not spoken:
            continue
        named += 1
        if any(
            0 <= tx["tick"] - u["tick"] <= SETTLEMENT_WINDOW_TICKS
            and u["speaker"] in tx["pair"]
            for tx in txs
        ):
            moved += 1
    return moved, named


# --------------------------------------------------------------------
# B6, B7 - favours and debts. Reported, never barred.
# --------------------------------------------------------------------

# Gouldner (1960) and the reciprocity literature: delayed exchange creates
# a relationship AND an obligation. DELAYED is the word that matters - a
# 72-tick run is one morning, so a bar on the return rate would be
# measuring the run length rather than the town. Reported instead, with
# the direction across runs as the thing worth watching.

DEBT_WORDS = re.compile(
    r"\b(owe[sd]?|owing|debt|repay|paid back|borrow(ed|ing)?|lent|loan)\b",
    re.IGNORECASE,
)


def favours(conn: sqlite3.Connection) -> tuple[int, int]:
    """(gifts returned within the run, gifts given).

    A gift is a one-sided move - `give`, or a transfer with nothing coming
    back. It is RETURNED when the recipient later moves something to the
    giver, in any form: the literature is explicit that a return may
    differ in kind and be deferred indefinitely.
    """
    gifts = [
        {"frm": r[0], "to": r[1], "tick": r[2] or 0}
        for r in rows(
            conn,
            "select from_agent_id, to_agent_id, tick from transactions "
            "where tx_type='transfer' and from_agent_id is not null "
            "and to_agent_id is not null",
        )
    ]
    returned = sum(
        1
        for g in gifts
        if any(o["frm"] == g["to"] and o["to"] == g["frm"] and o["tick"] > g["tick"] for o in gifts)
    )
    return returned, len(gifts)


def debts_spoken(utts: list[dict]) -> int:
    """Remarks that name an obligation between residents.

    Reported because it is the richest social material the town produces
    and there is no human baseline for how much of it a morning should
    contain. Bram's unspoken 400 sim is the best thing in the run log.
    """
    return sum(1 for u in utts if DEBT_WORDS.search(u["content"]))


# --------------------------------------------------------------------


def main(path: str) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    who = names(conn)
    utts = utterances(conn)
    if not utts:
        print(f"{path}: no utterances. Nothing to say about how it talks.")
        return 1

    answered, selected, unanswered = answer_rate(utts)
    rate = answered / selected if selected else 0.0
    short = unrecorded_addressees(conn)
    thread_sizes = sorted(sizes(utts))
    median = thread_sizes[len(thread_sizes) // 2] if thread_sizes else 0
    biggest = max(thread_sizes) if thread_sizes else 0
    split, big = fissioned(utts)
    moved, priced = settlements(conn, utts)
    returned, given = favours(conn)

    print(f"\n{'=' * 72}\nBEHAVIOURAL EVAL  {path}")
    print("bars from docs/HUMAN_BASELINES.md; sources cited there")
    print("=" * 72)
    print(f"utterances {len(utts)}   conversations {len(threads(utts))}   residents {len(who)}")

    checks = [
        (
            "B1 selected party answers",
            f"{answered}/{selected} = {rate:.0%}",
            f">= {ANSWER_RATE_FLOOR:.0%}",
            rate >= ANSWER_RATE_FLOOR,
            "Stivers 2009: 64-87% across 10 languages",
        ),
        (
            "B2 addressees all recorded",
            "no evidence" if short < 0 else f"{short} short",
            "0",
            short == 0,
            "SSJ 1974: a vocative selects, and obliges",
        ),
        (
            "B3 median conversation size",
            f"{median} speakers (largest {biggest})",
            f"<= {CONVERSATION_SIZE_CEILING}",
            0 < median <= CONVERSATION_SIZE_CEILING,
            "Dunbar: caps at ~4, observed mean 3.4",
        ),
        (
            "B4 over-large threads split",
            f"{split}/{big}" if big else "none over-large",
            f">= {FISSION_SHARE_FLOOR:.0%}",
            big == 0 or split / big >= FISSION_SHARE_FLOOR,
            "Dunbar: at 5 a conversation fissions",
        ),
        (
            "B5 named price then moved",
            f"{moved}/{priced}",
            ">= 1",
            moved >= 1,
            "a bargain ends in a transfer",
        ),
    ]
    print(f"\n{'MEASURE':<30}{'VALUE':<26}{'BAR':<10}RESULT")
    for name, value, bar, ok, _ in checks:
        print(f"{name:<30}{value:<26}{bar:<10}{'PASS' if ok else 'FAIL'}")

    print(f"\n{'REPORTED, NOT BARRED':<30}{'VALUE'}")
    print(f"{'B6 favours returned':<30}{returned}/{given}")
    print(f"{'B7 debts named in speech':<30}{debts_spoken(utts)}")
    print("   Reciprocity is delayed by definition; a bar would measure the run length.")

    print("\nwhy each bar sits where it does:")
    for name, _, _, _, source in checks:
        print(f"   {name:<30}{source}")

    if unanswered:
        print(f"\nselected and did not answer ({len(unanswered)}), first few:")
        for u in unanswered[:5]:
            speaker = who.get(u["speaker"], u["speaker"])
            listener = who.get(u["listener"], u["listener"])
            print(f'   {speaker} -> {listener}: "{u["content"][:74]}"')

    failed = [name for name, _, _, ok, _ in checks if not ok]
    print(f"\n{'ALL HUMAN BASELINES MET' if not failed else 'BELOW HUMAN: ' + ', '.join(failed)}\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/final1.db"))
