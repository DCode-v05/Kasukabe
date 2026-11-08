"""Write the run down: who spoke to whom, and what the town invented.

Run it at any point during a run, or after it. It reads the LIVE world for
most of what follows.

CORE-03 / EVAL-14: THE PARAGRAPH THAT USED TO BE HERE IS NO LONGER TRUE, and
its being untrue was expensive. It said "the trace store does not carry this:
the world's event bus is not bound to it in a genesis run, so `utterances`,
`skills` and `tools_registry` stay empty while `events` fills with broker
traffic only". `attach_persistence` binds the bus and has for some time; the
`events` table carries world rows selectively (EVAL-02/INFRA-16), and
`utterances`, `skills`, `tools_registry`, `tool_invocations` (EVAL-25) and
`run_manifest` (INFRA-09) are all written during a run.

What is still true is the WARNING, for a different reason: this generator
mixes live reads with trace reads, so regenerating it after the process
exits blanks every live row. `run-20-report.md` and `run-21-report.md`
show `Residents --`, "Nobody spoke" and "No agent earned proficiency" for
runs that did all three. So: **generate the report before stopping the
run**, and read a regenerated one as a report about a world that is no
longer there. For a
long run, `--watch` snapshots on an interval so a crash costs at most one
interval rather than the whole history.

    uv run python -m world.run_report                      # write it now
    uv run python -m world.run_report --watch               # every 5 minutes
    uv run python -m world.run_report --watch 600           # every 10 minutes
    uv run python -m world.run_report -o docs/RUN.md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_WORLD = "http://127.0.0.1:8100"
DEFAULT_DB = "data/kasukabe.db"
THREAD_GAP_TICKS = 6


def get(base: str, path: str) -> Any:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _served_by(db: str) -> str:
    """The row label for the call count: who actually served this run.

    Reads `llm_calls.model` rather than trusting a provider name compiled in
    years ago. One model gets named; more than one is counted, because a run
    that fanned out across providers should say so in the row a reader checks
    first.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = [
                r[0] for r in conn.execute(
                    "select model from llm_calls where model is not null "
                    "group by model order by count(*) desc"
                )
            ]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a label must never fail a report
        return "Model calls"
    if len(rows) == 1:
        return f"Calls to `{rows[0]}`"
    if len(rows) > 1:
        return f"Model calls across {len(rows)} models"
    return "Model calls"


def elections_section(db: str, names: "Mapping[str, str] | None" = None) -> list[str]:
    """Who stood, what for, how the vote went, and who took the seats.

    The report said "1 tallied, 1 with a PM" and stopped. A reader could not
    learn who had stood, what any of them stood for, how many votes anybody
    got, or how the winner was chosen - which is most of what an election run
    is for. All of it was already in the events table and none of it was
    printed.

    `because` on a candidacy is the platform the candidate declared; it is
    required, so every row has one.
    """
    import json as _json

    def name(a: str) -> str:
        return f"{names[a]} ({a})" if names and a in names else a

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except Exception:  # noqa: BLE001
        return []
    try:
        if names is None:
            try:
                names = {
                    str(i): n
                    for i, n in conn.execute("select id, display_name from agents")
                    if n
                }
            except Exception:  # noqa: BLE001
                names = {}
        stood: dict[str, dict[str, str]] = {}
        for payload, in conn.execute(
            "select payload from events where type = 'politics.candidacy.declared' order by id"
        ):
            try:
                d = _json.loads(payload) if isinstance(payload, str) else payload
            except Exception:  # noqa: BLE001
                continue
            stood.setdefault(d.get("election_id", ""), {})[d.get("agent_id", "")] = (
                d.get("because") or ""
            )
        by_id: dict[str, dict] = {}
        for payload, in conn.execute(
            "select payload from events where type = 'politics.election.tallied' order by id"
        ):
            try:
                d = _json.loads(payload) if isinstance(payload, str) else payload
            except Exception:  # noqa: BLE001
                continue
            by_id[d.get("election_id", "")] = d
        tallies = list(by_id.values())
    finally:
        conn.close()

    if not stood and not tallies:
        return []

    out = ["## The elections\n"]
    for i, t in enumerate(tallies, 1):
        eid = t.get("election_id", "")
        counts = t.get("vote_counts") or {}
        pm = t.get("pm_agent_id")
        ministers = t.get("minister_agent_ids") or []
        total = sum(counts.values())
        platforms = stood.get(eid, {})

        out.append(f"### Election {i} — `{eid}`\n")
        out.append(
            f"**{len(counts)} stood. {total} vote(s) cast.** "
            + (f"Prime minister: **{name(pm)}**. " if pm else "No prime minister. ")
            + (f"Ministers: {', '.join(name(m) for m in ministers)}." if ministers else "")
        )
        if total == 0:
            out.append("")
            out.append(
                "> Every candidate finished on nought, so the seats were filled "
                "among people nobody voted for. The result is what the tie-break "
                "did, not what the town decided."
            )
        out.append("")
        out.append("| Candidate | Votes | Seat | What they stood for |")
        out.append("|---|--:|---|---|")
        for a, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            seat = "**PM**" if a == pm else ("minister" if a in ministers else "—")
            plat = (platforms.get(a) or "—").replace("|", "/")
            if len(plat) > 150:
                plat = plat[:147] + "..."
            out.append(f"| {name(a)} | {v} | {seat} | {plat} |")
        out.append("")
    return out


def api_calls(db: str, limit: int = 400) -> list[tuple]:
    """The most recent Bedrock calls, newest first, with wall-clock stamps.

    Every call the town made to think. Recorded with `failed_at` so a failure
    says which link broke rather than only that something did.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(
            "select created_at, agent_id, model, "
            "purpose, input_tokens, output_tokens, "
            "round(cost_nano_dollars/1e9, 6), latency_ms, success, "
            "coalesce(failed_at,''), coalesce(error_type,'') "
            "from llm_calls order by created_at desc limit ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"run_report: api_calls query failed: {exc}", file=sys.stderr, flush=True)
        return []
    finally:
        conn.close()


NL = chr(10)


def _blocks(body: str | None) -> list[dict]:
    """Content blocks out of a stored body, or nothing.

    Bodies are capped at 64 KB by broker/trace.py (TEST_RUN.md Finding 1), so
    a long one arrives truncated and will not parse. That is reported as an
    unreadable turn rather than skipped: a session with a hole in it is a
    finding about the cap, and silently dropping the row would hide it.
    """
    if not body:
        return []
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return []
    content = parsed.get("content")
    return content if isinstance(content, list) else []


def _text_of(blocks: list[dict]) -> str:
    return " ".join(
        (b.get("text") or "").strip()
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


def _prompt_of(body: str | None) -> str:
    """What the engine actually asked. The LAST user message, because the
    ones before it are the conversation it is already carrying."""
    if not body:
        return ""
    try:
        req = json.loads(body)
    except (ValueError, TypeError):
        return ""
    if not isinstance(req, dict):
        return ""
    messages = req.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, str):
        return last.strip()
    if not isinstance(last, dict):
        return ""
    content = last.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return _text_of(content)
    return ""


def _tools_of(blocks: list[dict]) -> str:
    """The tool_use blocks, named with their arguments.

    This is the column that says the agent DID something rather than merely
    said something, which is the difference the run is being watched for.
    """
    out = []
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        args = b.get("input") or {}
        if isinstance(args, dict) and args:
            shown = ", ".join(f"{k}={_cell(str(v), 40)}" for k, v in list(args.items())[:3])
        else:
            shown = ""
        out.append(f"`{b.get('name','?')}`({shown})")
    return " ".join(out)


def _cell(text: str, width: int) -> str:
    """One markdown table cell: single-line, escaped, trimmed.

    A prompt carries newlines and pipes, and either one silently breaks the
    row it is sitting in - so the table would render as prose and the report
    would look corrupted rather than long.
    """
    flat = " ".join((text or "").split()).replace("|", r"\|")
    return (flat[: width - 1] + "…") if len(flat) > width else flat


def sessions_by_engine(db: str, per_agent: int = 12) -> dict[str, list[tuple]]:
    """Every engine's own turns, newest first: input, what the model said,
    what it then called.

    One row is one round trip through the broker, which is one session turn
    for that engine. Read straight from `llm_calls` rather than from the
    per-agent `session_log.jsonl`, because the trace store is the only place
    that holds BOTH halves - the prompt that went out and the body that came
    back - and a checklist assembled from two sources could disagree with
    itself about what a turn was.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        rows = conn.execute(
            "select agent_id, created_at, purpose, "
            "request_body, response_body, stop_reason, input_tokens, output_tokens, "
            "round(cost_nano_dollars/1e9, 6), success, coalesce(error_type,'') "
            "from llm_calls order by created_at desc"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    by_agent: dict[str, list[tuple]] = {}
    for (aid, ts, purpose, req, res, stop, tin, tout, cost, ok, err) in rows:
        seen = by_agent.setdefault(aid or "unassigned", [])
        if len(seen) >= per_agent:
            continue
        blocks = _blocks(res)
        seen.append((
            ts, purpose or "unspecified", _prompt_of(req), _text_of(blocks),
            _tools_of(blocks), stop or "", tin or 0, tout or 0, cost or 0.0, ok, err,
        ))
    return by_agent


def calls_per_minute(db: str) -> list[tuple]:
    """Call volume and spend minute by minute — the run's pulse."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(
            "select cast(created_at/60 as int)*60 as m, "
            "count(*), sum(success), round(sum(cost_nano_dollars)/1e9, 4) "
            "from llm_calls group by m order by m desc limit 90"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


MISSING_TRACE_MARKER = "the trace store has no record of this run's inference"


def town_was_busy(db: str) -> bool:
    """Did anything happen that could only have happened by thinking?

    Utterances and memories are produced by agent turns and by nothing
    else, so either one present with an empty `llm_calls` means the calls
    happened and the record of them did not survive.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        for table in ("utterances", "memories"):
            try:
                if conn.execute(f"select count(*) from {table}").fetchone()[0]:
                    return True
            except sqlite3.Error:
                continue
        return False
    finally:
        conn.close()


def spend(db: str) -> dict[str, Any]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        row = conn.execute(
            "select count(*), coalesce(sum(success),0), "
            "coalesce(sum(cost_nano_dollars),0) from llm_calls"
        ).fetchone()
        try:
            refused_by_cap = conn.execute(
                "select count(*) from llm_calls where success=0 "
                "and error_type='SpendCapExceededError'"
            ).fetchone()[0] or 0
            fails = conn.execute(
                "select coalesce(failed_at,'untagged'), count(*) from llm_calls "
                "where success=0 and coalesce(error_type,'') != 'SpendCapExceededError' "
                "group by 1 order by 2 desc"
            ).fetchall()
        except sqlite3.OperationalError:
            refused_by_cap = 0
            fails = conn.execute(
                "select coalesce(failed_at,'untagged'), count(*) from llm_calls "
                "where success=0 group by 1 order by 2 desc"
            ).fetchall()
        return {
            "calls": row[0] or 0,
            "ok": row[1] or 0,
            "usd": (row[2] or 0) / 1e9,
            "failures": fails,
            "cap_refusals": refused_by_cap,
        }
    finally:
        conn.close()


def tool_call_counts(home_root: str) -> dict[str, int]:
    """How many times each tool was actually called, town-wide.

    From the engines' own session logs, which record every action a
    resident took with the tool's name under `tool`. The trace store holds
    model calls, not tool calls, so it cannot answer this - and `name` on
    an action record is always None, which is how an earlier eval read
    zero movements on a run with thirteen journeys.
    """
    counts: dict[str, int] = {}
    root = Path(home_root)
    if not root.is_dir():
        return counts
    for log in sorted(root.glob("*/session_log.jsonl")):
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not belongs_to_this_run(record):
                continue
            for action in record.get("actions") or []:
                tool = action.get("tool")
                if not tool:
                    continue
                bare = str(tool).split(":", 1)[-1]
                counts[bare] = counts.get(bare, 0) + 1
    return counts


def gated_tool_names() -> set[str]:
    """Tools that require standing somewhere in particular.

    Read from the tool SPECS, not from `/tools/registry`. That endpoint
    serves the model its schema - name, description, input_schema - and
    carries no `location_gate` at all, so asking it produced a gate map in
    which nothing was gated. Run 20's report duly listed `vote`, `pray`,
    `petition` and `define_tool` as tools nobody reached for; you vote at
    the town hall and pray at the temple, and nobody was going to call them
    from a farm.

    The same shape as reading `action["name"]` when the field is `tool`,
    and the same fix: read it from the thing that actually has it.
    """
    try:
        import asyncio

        from world.tools.bootstrap import build_world
    except Exception:  # noqa: BLE001 - a report must not die for want of a gate map
        return set()
    try:
        _world, registry = asyncio.run(build_world(genesis_tick=0, seed=1))
    except Exception:  # noqa: BLE001
        return set()
    return {
        name for name, spec in registry.tools.items()
        if spec.location_gate is not None
    }


def prices_section(rows: list[dict]) -> list[str]:
    """E1 to E3. The first posted price, and the spread across shops.

    E1 is literally the tick of the first post, so it is the first thing
    printed. E2 wants the coefficient of variation of the same item across
    shops to fall day over day, which needs each shop's ask shown rather
    than an average that hides the spread.
    """
    out = ["## Prices\n"]
    if not rows:
        out.append(
            "**Nobody posted a price.** Read from `events` "
            "(`economy.price.posted`); every run before this one produced "
            "the same zero, which is why `buy` and `sell` had nothing to "
            "converge to.\n"
        )
        return out
    first = min(int(r.get("tick", 0)) for r in rows)
    out.append(f"**First posted price at tick {first}.** From `events`.\n")
    out.append("| Tick | Posted by | Holder | Item | Ask (sim) | Bid (sim) |")
    out.append("|--:|---|---|---|--:|--:|")
    for row in sorted(rows, key=lambda r: (r.get("tick", 0), str(r.get("holder_id")))):
        ask = row.get("ask_sim")
        bid = row.get("bid_sim")
        out.append(
            f"| {row.get('tick')} | {row.get('poster_id')} | "
            f"{row.get('holder_id')} ({row.get('holder_kind')}) | "
            f"{row.get('item')} | {'-' if ask is None else ask} | "
            f"{'-' if bid is None else bid} |"
        )
    out.append("")
    return out


def stakes_section(
    *, upkeep: list[dict], head_tax: list[dict], energy: list[dict],
    dormancies: list[dict], head_tax_arrears: list[dict] | None = None,
) -> list[str]:
    """S1 to S4. What was charged, what was bought, and who stopped."""
    out = ["## Stakes\n"]
    if not upkeep and not head_tax:
        out.append(
            "**No day boundary was reached, so no upkeep and no head tax "
            "were charged.** Nobody has yet paid for existing. Read from "
            "`transactions`; evidence rule 10 applies - this is a morning "
            "figure and is labelled one.\n"
        )
    else:
        out.append(f"**Upkeep charged:** {len(upkeep)} time(s), "
                   f"{sum(int(r.get('amount', 0)) for r in upkeep)} sim total. "
                   "From `transactions`.\n")
        out.append(f"**Head tax charged:** {len(head_tax)} time(s), "
                   f"{sum(int(r.get('amount', 0)) for r in head_tax)} sim total. "
                   "From `transactions`.\n")
        arrears = head_tax_arrears or []
        if arrears:
            out.append(
                f"**Head tax unpaid:** {len(arrears)} head(s) could not pay; "
                f"{sum(int(r.get('amount', 0)) for r in arrears)} sim became "
                "arrears instead of treasury revenue.\n"
            )
        else:
            out.append("**Nobody fell into head-tax arrears:** every head "
                       "billed paid it.\n")

    if energy:
        bought = sum(int(r.get("usd_cents", 0)) for r in energy)
        out.append(
            f"**Energy bought:** {len(energy)} purchase(s), "
            f"{sum(int(r.get('sim_amount', 0)) for r in energy)} sim spent for "
            f"${bought / 100:.2f} of thinking. From `events` "
            "(`economy.energy.bought`).\n"
        )
    else:
        out.append(
            "**Nobody bought energy.** The dollar wallet only went down, so "
            "nothing anybody did changed how long they could think. From "
            "`events`.\n"
        )

    if dormancies:
        out.append(
            f"**{len(dormancies)} resident(s) went dormant** - they ran out of "
            f"thinking money and stopped taking turns. From `health.agent.dormant`.\n")
        out.append("| Resident | Tick | Cause |")
        out.append("|---|--:|---|")
        for row in dormancies:
            out.append(f"| {row.get('agent_id')} | {row.get('tick')} | {row.get('cause')} |")
        out.append("")
    else:
        out.append("**No dormancies.** Nobody ran out of thinking.\n")
    return out


def wages_section(*, promised: list[dict], paid: list[dict]) -> list[str]:
    """S6 and S7. A promise is only a promise once it is not kept.

    Charness and Dufwenberg's finding is that promises raise performance
    to a majority, so the bar is on direction - and the number that says
    whether it held is the gap, not the total paid.
    """
    out = ["## Wages\n"]
    if not promised:
        out.append(
            "**No wage was ever promised.** `hire_employee` grants access to "
            "a business's tools and pays nobody, so a job is a label until "
            "somebody sets a wage. From `transactions` and `events`.\n"
        )
        return out

    settled = 0
    for promise in promised:
        if any(
            p.get("employee") == promise.get("employee")
            and p.get("employer") == promise.get("employer")
            and int(p.get("amount", 0)) >= int(promise.get("amount", 0))
            for p in paid
        ):
            settled += 1
    out.append(
        f"**{settled} of {len(promised)} wage promise(s) paid.** From "
        "`transactions`.\n"
    )
    out.append("| Employer | Employee | Promised | Paid |")
    out.append("|---|---|--:|---|")
    for promise in promised:
        was_paid = any(
            p.get("employee") == promise.get("employee")
            and p.get("employer") == promise.get("employer")
            for p in paid
        )
        out.append(
            f"| {promise.get('employer')} | {promise.get('employee')} | "
            f"{promise.get('amount')} | {'yes' if was_paid else '**unpaid**'} |"
        )
    out.append("")
    return out


def untouched_tools(
    served: list[dict], call_counts: dict[str, int], tried: "set[str] | None" = None
) -> list[dict]:
    """Tools that exist, need no particular place, and nobody called.

    "What the town could not do" is built from refusals, so it only ever
    shows what a resident TRIED and was denied. Run 19 listed one row while
    the transcript had residents unable to sell planks, find a buyer, or
    buy food - because none of them ever called the tool that does it. A
    run in which nobody tries anything scores a clean table.

    `trade` is the case: ungated, visible to all twenty, does exactly what
    Corwin and Nell agreed out loud, and invoked zero times.

    Gated tools are excluded deliberately. `vote` goes uncalled whenever
    there is no election, and thirty such rows would bury the one that
    matters.

    `tried` is every tool somebody called and was REFUSED. A refused call
    never lands, so `call_counts` cannot see it - run 27 listed `pay_tax`
    as never called two sections after refusing it five times.
    """
    tried = tried or set()
    return [
        {"name": tool["name"]}
        for tool in served
        if not tool.get("gated")
        and not call_counts.get(tool["name"])
        and tool["name"] not in tried
    ]


def acting_turn_rate(turns: list[dict]) -> dict:
    """How many turns actually touched the world, and who never did.

    "Dispatch success 100.0%" is true of the calls that were made and says
    nothing about the turns that made none. Ainsley Holloway and Tobias
    Holloway each took twelve turns in run 19 and called nothing at all,
    and the headline still read 100%.
    """
    total = len(turns)
    acting = sum(1 for turn in turns if turn.get("tool_calls"))
    by_agent: dict[str, int] = {}
    for turn in turns:
        agent_id = str(turn.get("agent_id"))
        by_agent[agent_id] = by_agent.get(agent_id, 0) + int(turn.get("tool_calls") or 0)
    return {
        "turns": total,
        "acting": acting,
        "percent": round(100.0 * acting / total, 1) if total else 0.0,
        "silent_residents": sorted(a for a, calls in by_agent.items() if not calls),
    }


def _everyone_named(utterance: dict) -> list[str]:
    """Speaker, listener, and every resident the remark addressed by name.

    CORE-28. `say` has carried `addressed_agent_ids` since the addressee
    work, and run 23 filled it on 172 of 229 utterances - but the thread
    grouping below read `speaker` and `listener` only. Since a thread's
    `people` list is what decides whether the NEXT utterance overlaps it,
    under-counting participants splits one conversation into several, and
    thread shape is exactly what G3, B3 and B4 are scored on.
    """
    people = [utterance.get("speaker_agent_id"), utterance.get("listener_agent_id")]
    people.extend(utterance.get("addressed_agent_ids") or [])
    seen: list[str] = []
    for person in people:
        if person and person not in seen:
            seen.append(person)
    return seen


def threads(rows: list[dict]) -> list[dict]:
    """Group utterances into exchanges, so a reply sits under what it answers.

    By `conversation_id` first, because the world assigns one when the
    speech happens and it is the only grouping that is not a guess.

    The guess used to be the whole of this function - place, participant
    overlap, and a tick gap - and it read run 16 badly in both
    directions. The world had recorded 8 conversations, 5 of them with a
    real second speaker and the longest 15 turns; the report rendered 40
    exchanges and stamped "(no reply)" on 29 of them, because a thread
    that moved from the quarry to the mill, or that a third resident
    joined, split into singletons. A reader would have concluded the town
    talked past itself. The same heuristic was removed from the Talk
    panel in game/static/js/township/talk.js months earlier; this copy
    outlived the fix.

    The heuristic stays as the fallback for rows with no
    `conversation_id` - anything the world could not thread still has to
    appear, and dropping it would trade one wrong reading for a worse
    one.
    """
    out: list[dict] = []
    by_conversation: dict[str, dict] = {}
    for u in rows:
        conversation = u.get("conversation_id")
        if conversation:
            thread = by_conversation.get(conversation)
            if thread is None:
                thread = {
                    "place": u.get("location_id"), "conversation_id": conversation,
                    "people": [], "lines": [],
                    "first_tick": u["tick"], "last_tick": u["tick"],
                }
                by_conversation[conversation] = thread
                out.append(thread)
            thread["lines"].append(u)
            thread["last_tick"] = u["tick"]
            for p in _everyone_named(u):
                if p not in thread["people"]:
                    thread["people"].append(p)
            continue

        last = out[-1] if out else None
        people = _everyone_named(u)
        overlaps = last and any(p in last["people"] for p in people)
        close = last and (u["tick"] - last["last_tick"]) <= THREAD_GAP_TICKS
        same_place = last and last["place"] == u.get("location_id")
        if last and last.get("conversation_id"):
            overlaps = False
        if overlaps and close and same_place:
            last["lines"].append(u)
            last["last_tick"] = u["tick"]
            for p in people:
                if p not in last["people"]:
                    last["people"].append(p)
        else:
            out.append({
                "place": u.get("location_id"), "people": list(people),
                "lines": [u], "first_tick": u["tick"], "last_tick": u["tick"],
            })
    return out


IST = timezone(timedelta(hours=5, minutes=30), "IST")


def ist(epoch: float | int | str | None, with_date: bool = True) -> str:
    """One epoch second as `26 Aug 2026, 10:47:03 PM IST`."""
    if epoch in (None, ""):
        return "—"
    try:
        dt = datetime.fromtimestamp(float(epoch), IST)
    except (TypeError, ValueError, OSError):
        return "—"
    hour = dt.hour % 12 or 12
    clock = f"{hour}:{dt:%M:%S} {dt:%p} IST"
    return f"{dt:%d %b %Y}, {clock}" if with_date else clock


def ist_now() -> str:
    return datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")


def _flat(value: Any, limit: int) -> str:
    """One line of markdown-safe text, truncated.

    Session text is agent-authored and full of newlines, pipes and
    backticks; dropped into a table or a blockquote raw it breaks the
    document open.
    """
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    text = text.replace("|", "\\|").replace("`", "'")
    return (text[:limit] + "…") if len(text) > limit else (text or "—")


SESSIONS_PER_ENGINE = int(os.environ.get("KASUKABE_REPORT_SESSIONS", "400"))


def sessions(home_root: str, per_agent: int = SESSIONS_PER_ENGINE) -> dict[str, dict[str, Any]]:
    """Every engine's recent sessions, keyed by agent id.

    A missing or unreadable log is an empty entry rather than an omission -
    an engine that has not thought yet is a fact worth printing, and an
    engine silently missing from a table reads as one that does not exist.
    """
    root = Path(home_root).expanduser()
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out

    for home in sorted(root.iterdir()):
        if not home.is_dir():
            continue
        log = home / "session_log.jsonl"
        rows: list[dict[str, Any]] = []
        if log.is_file():
            try:
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if belongs_to_this_run(record):
                    rows.append(record)

        turns = sum((r.get("done_status") or {}).get("num_turns") or 0 for r in rows)
        calls = sum((r.get("done_status") or {}).get("tool_calls_count") or 0 for r in rows)
        models = sorted({(r.get("done_status") or {}).get("model_id")
                         for r in rows if (r.get("done_status") or {}).get("model_id")})
        out[home.name] = {
            "sessions": len(lines) if log.is_file() else 0,
            "recent": rows[-per_agent:],
            "turns": turns,
            "tool_calls": calls,
            "models": models,
            "log_path": str(log),
            "log_exists": log.is_file(),
        }
    return out


RUN_DOC_DIR = Path(__file__).resolve().parents[2] / "docs" / "runs"
RUN_DOC_PATTERN = "run-{n}-report.md"


def next_run_number(directory: Path = RUN_DOC_DIR) -> int:
    """One past the highest run already on disk.

    Reads the directory rather than keeping a counter: a counter in a file
    is one more thing to get out of step with reality, and the documents
    themselves are the reality.
    """
    highest = 0
    if directory.is_dir():
        for f in directory.glob("run-*-report.md"):
            m = re.match(r"run-(\d+)-report\.md$", f.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def run_doc_for(world: str, directory: Path = RUN_DOC_DIR) -> Path:
    """This run's document, created on first write and reused after.

    Identity is the world's boot, not the reporter's: `--watch` refreshes
    the same document every ninety seconds, and a reporter restarted
    mid-run must land on the run's existing document rather than opening a
    new one. The marker is the run's own start time, written into the file
    the first time it is created.
    """
    directory.mkdir(parents=True, exist_ok=True)
    started = run_started_at(world)
    if started:
        for f in sorted(directory.glob("run-*-report.md")):
            try:
                head = f.read_text(encoding="utf-8")[:2000]
            except OSError:
                continue
            if f"<!-- run-started: {started} -->" in head:
                return f
    return directory / RUN_DOC_PATTERN.format(n=next_run_number(directory))


def run_started_at(world: str) -> str | None:
    """A stable identifier for THIS boot of the world.

    Tick 0 is not it - a resumed run starts at tick 1048 - and wall-clock
    now is not it either, because it changes every refresh. The world's own
    reported start is the only thing that is constant for one run and
    different for the next.
    """
    state = get(world, "/world/state") or {}
    for key in ("run_id", "started_at", "boot_id"):
        if state.get(key):
            return str(state[key])
    return None


def trace_invocations(db: "str | Path") -> list[dict]:
    """Every tool call a finished run made, from its trace store.

    Shaped exactly as `/world/tool_invocations` serves them, so the join in
    `stakes_report.label_calls` cannot tell which it was handed. [] when the
    store has no such table or cannot be read - the caller then labels
    nothing rather than inventing labels.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "select agent_id, tick, tool, program_id, program_step "
            "from tool_invocations order by tick"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [
        {"agent_id": a, "tick": t, "tool_name": tool,
         "program_id": pid, "program_step": step}
        for a, t, tool, pid, step in rows
    ]


def invocations_for_labelling(live: list[dict], db: "str | Path") -> list[dict]:
    """What the task-label join reads: the live world while it answers, the
    trace store once it does not.

    Run 29's final report labelled all 2,717 model calls "idle". The join's
    only source was the live endpoint, on the grounds that invocations were
    "an in-memory ring with no table behind it" - which stopped being true
    at EVAL-25, when the trace store gained `tool_invocations`. A final
    report is written after the world stops, so it joined against nothing
    and described a town that harvested, felled and traded all morning as
    one that did nothing. The live answer still wins while there is one:
    it is the same rows, and it is what a mid-run refresh has.
    """
    return live or trace_invocations(db)


def _labelled_calls(db: str, invocations: list[dict]) -> list[dict]:
    """Every model call, with the task_label its turn earned (EVAL-03).

    ECON-25 as well as EVAL-05: `stakes_report.label_calls` existed with four tests and NO production
    caller, so `task_label` - which the evaluation spec's section 4 names as
    the x-axis of the learning curve - was never computed for any run. This
    is its caller.

    The join is by (agent_id, tick), which is why INFRA-02 had to land
    first: until the tick reached the `llm_calls` row there was nothing to
    join on, and every call would have been labelled "idle".
    """
    import sqlite3

    from world.stakes_report import label_calls

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "select agent_id, tick, input_tokens from llm_calls "
            "where agent_id is not null"
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        print(f"run_report: labelled-calls query failed: {exc}", file=sys.stderr, flush=True)
        return []

    calls = [
        {"agent_id": a, "tick": t, "input_tokens": n or 0}
        for a, t, n in rows
    ]
    return label_calls(calls, invocations)


def time_section(events: list[dict]) -> list[str]:
    """T2-T4: what got done without deciding, and what got decided and
    then not done.

    EVAL-05. `stakes_report.time_spent` computes all of it and had no
    caller anywhere - `grep time_spent run_report.py` returned nothing. So
    the evaluation spec's Time section has never appeared in a single
    report this project has produced, and T4 - the share of intentions
    actually fulfilled, which is also business-readiness figure R4 - has
    never been read off a run.
    """
    from world.stakes_report import time_spent

    report = time_spent(events)
    lines = ["## Time\n"]
    if not (report.continuing_started or report.intentions_set):
        lines.append(
            "Nobody committed to anything and nobody set an intention. That "
            "is a town acting one tick at a time, which is a finding about "
            "the town rather than a gap in the record.\n"
        )
        return lines

    lines.append(
        f"- **Commitments** - {report.continuing_started} started, "
        f"{report.continuing_repetitions} repetitions carried out without a "
        "fresh decision (T2: the cheapest work in the town)\n"
    )
    if report.wake_reasons:
        reasons = ", ".join(
            f"{reason} x{count}"
            for reason, count in sorted(
                report.wake_reasons.items(), key=lambda kv: (-kv[1], kv[0])
            )
        )
        lines.append(f"- **Why commitments ended** - {reasons}\n")

    share = report.fulfilled_share
    lines.append(
        f"- **Intentions** - {report.intentions_set} set, "
        f"{report.intentions_fulfilled} fulfilled, "
        f"{report.intentions_expired} expired at the day's end"
        + (f" (**T4: {share:.0%}** fulfilled)\n" if share is not None else "\n")
    )
    return lines


def diffusion_section(events: list[dict]) -> list[str]:
    """C4: whether anything anybody invented reached anybody else.

    EVAL-05. `stakes_report.diffusion` was likewise uncalled, so the T5
    gate - "a tool held by three residents" - has never been measured on a
    run. Before conveyance existed the answer was 1 by construction; the
    point of reporting it is to find out whether it still is.
    """
    from world.stakes_report import diffusion

    report = diffusion(events)
    lines = ["## Diffusion\n"]
    if not report.anything_moved:
        lines.append(
            "Nothing anybody invented reached anybody else. No licence, no "
            "sale, no lesson, no inheritance - so T5 is 1 by construction "
            "and says nothing about the town yet.\n"
        )
        return lines

    lines.append(
        f"- **Ways a tool moved** - {report.licences} licensed "
        f"({report.revocations} revoked), {report.sales} sold, "
        f"{report.teachings} taught, {report.inheritances} inherited\n"
    )
    lines.append(
        f"- **Skills taught** - {report.skill_teachings} lesson(s) with "
        f"`teach_skill`, reaching {len(report.holders_by_skill)} procedure(s)\n"
    )
    lines.append(
        f"- **Widest reach** - one invented tool or skill ended up in "
        f"**{report.widest_reach}** pairs of hands (T5 asks for three)\n"
    )
    if report.holders_by_skill:
        lines.append("\n| skill | held by |\n|---|--:|\n")
        for skill, who in sorted(report.holders_by_skill.items(),
                                 key=lambda kv: (-len(kv[1]), kv[0]))[:5]:
            lines.append(f"| {skill} | {len(who)} |\n")
        lines.append("\n")
    if report.holders_by_tool:
        widest = sorted(
            report.holders_by_tool.items(), key=lambda kv: (-len(kv[1]), kv[0])
        )[:5]
        lines.append("\n| tool | held by |\n|---|--:|\n")
        for tool, who in widest:
            lines.append(f"| `{tool}` | {len(who)} |\n")
        lines.append("\n")
    return lines


def programs_section(invocations: list[dict]) -> list[str]:
    """PROG-03: how many acts one decision bought.

    `docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md` section 7 asks for the
    parent link before anything else, because *"without it no run can
    measure whether programmatic calling helped."* PROG-01 wrote the link
    and `world/evals/programs.py` reads it in full. This is the third
    consumer, and it is here for the reason WIRE-02 needed three: a number
    that only exists behind a command is a number nobody runs, and the
    report is what a person opens.

    Reported, never scored. Whether buying six acts with one model call is
    the finding or the confound is the operator's call
    (`docs/decisions/PROG-COST-MODEL.md`), and a report that graded it
    would be answering the question by asserting it.
    """
    programs: dict[str, dict] = {}
    for row in invocations:
        program_id = row.get("program_id")
        if not program_id:
            continue
        entry = programs.setdefault(
            program_id, {"rows": 0, "actor": row.get("agent_id"), "tool": None}
        )
        entry["rows"] += 1
        if row.get("program_step") == 0:
            entry["tool"] = row.get("tool_name")

    lines = ["## What one decision bought\n"]
    if not programs:
        lines.append(
            "**No programs ran.** Every call this town made was its own "
            "decision, so one model call bought one act - which is the "
            "figure every run before this one had by construction. The "
            "mechanism is held by the guards in `world/tools/tests`; what "
            "is unmeasured here is whether anybody reached for it.\n"
        )
        return lines

    acts = sum(max(entry["rows"] - 1, 0) for entry in programs.values())
    per = acts / len(programs)
    widest = max(programs.values(), key=lambda entry: entry["rows"])
    by_actor: dict[str, int] = {}
    for entry in programs.values():
        by_actor[entry["actor"]] = by_actor.get(entry["actor"], 0) + 1

    lines.append(
        f"- **{len(programs)} program(s)** bought **{acts} act(s)** - "
        f"**{per:.1f} acts per decision**, against 1.0 for a call made by hand\n"
    )
    lines.append(
        f"- **Widest** - `{widest['tool'] or 'unknown'}` ran "
        f"{max(widest['rows'] - 1, 0)} acts on one call\n"
    )
    lines.append(f"- **Residents who ran one** - {len(by_actor)}\n")
    lines.append(
        "\nWhether that ratio is the result or the confound is not this "
        "report's to say: `docs/decisions/PROG-COST-MODEL.md`.\n"
    )
    return lines


def housekeeping_section(rows: list[dict]) -> list[str]:
    """What the learning centre and the summariser cost each resident.

    INFRA-19. DESIGN section 4 says the learning centre is "metered on the
    agent's own wallet, scheduled during sleep ticks". The awake-interval
    batch (every 18 ticks, three game-hours) now runs it MID-MORNING, and
    run 23 shows single calls at 107,289 input tokens for $0.0405 - which
    against a $6 wallet is 0.7% of a life per batch.

    Build Spec 3.7 asks whether housekeeping should drain wallets at all.
    That is a decision, not a repair, and it is not one this report should
    make. What it can do is stop the question being theoretical: the
    figures below are what the choice actually costs, per resident, on the
    run in front of you.

    Read from `call_class`, which `broker/lanes.py` sets from the purpose
    `broker/purpose.py` worked out - so a run where classification failed
    shows zero background here rather than a wrong split, and the zero is
    itself the finding.
    """
    from collections import defaultdict

    out = ["## What housekeeping cost (INFRA-19)\n"]
    if not rows:
        out.append(
            "No classified calls in this run's trace, so the split between "
            "a resident's own thinking and the learning centre's cannot be "
            "computed. `call_class` is set by the broker on every row it "
            "writes, so an empty result here means the trace store was not "
            "reached, not that housekeeping was free.\n"
        )
        return out

    spent: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    calls: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        agent = str(row.get("payer_agent_id") or row.get("agent_id") or "unknown")
        kind = str(row.get("call_class") or "unknown")
        spent[agent][kind] += int(row.get("cents_debited") or 0)
        calls[agent][kind] += 1

    total_background = sum(a.get("background", 0) for a in spent.values())
    total_cognition = sum(a.get("cognition", 0) for a in spent.values())
    if total_background == 0:
        out.append(
            "**Nothing was billed as background.** Either no housekeeping "
            "ran, or it was not classified - `purpose_source` on the rows "
            "says which (CORE-30). It is not evidence that the learning "
            "centre is free.\n"
        )
        return out

    share = total_background / (total_background + total_cognition or 1)
    out.append(
        f"- **Of every cent the town spent on thinking, {share:.1%} went on "
        f"housekeeping** rather than on a resident's own turn "
        f"({total_background} cents against {total_cognition}).\n"
    )

    worst = sorted(
        spent.items(), key=lambda kv: -kv[1].get("background", 0)
    )[:8]
    out.append("\n| resident | own thinking | housekeeping | housekeeping calls |\n")
    out.append("|---|--:|--:|--:|\n")
    for agent, purse in worst:
        out.append(
            f"| {agent} | {purse.get('cognition', 0)}c | "
            f"{purse.get('background', 0)}c | "
            f"{calls[agent].get('background', 0)} |\n"
        )
    out.append(
        "\nDESIGN section 4 schedules this "
        "\"during sleep ticks\" and the awake-interval batch runs it in the "
        "morning; Build Spec 3.7 asks whether it should be billed to the "
        "resident at all. Both are open decisions - this table is what "
        "they cost.\n"
    )
    return out


def learning_section(calls: list[dict]) -> list[str]:
    """C1: model calls per resident-day, split by what the turn was for.

    The evaluation spec's headline cost metric. It has never appeared in a
    report, for three stacked reasons - `llm_calls.tick` was NULL (INFRA-02),
    `label_calls` had no caller, and `stakes_report` was not imported by the
    report at all. All three are fixed; this is where the number surfaces.
    """
    out = ["## What thinking cost, per resident-day (C1)\n"]
    if not calls:
        out.append(
            "**No model calls carried both an agent and a tick**, so C1 "
            "cannot be computed for this run. That is a defect in the "
            "record, not a quiet town - check that `llm_calls.tick` is "
            "populated (INFRA-02).\n"
        )
        return out

    from collections import defaultdict

    per_day: dict[int, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    counts: dict[tuple[int, str], int] = defaultdict(int)
    for call in calls:
        tick = call.get("tick")
        if tick is None:
            continue
        day = int(tick) // 144
        label = str(call.get("task_label") or "idle")
        per_day[day][label].add(call.get("agent_id"))
        counts[(day, label)] += 1

    if not counts:
        out.append(
            f"**{len(calls)} model calls, none of them carrying a tick.** "
            "C1 is uncomputable until INFRA-02's tick reaches the row.\n"
        )
        return out

    out.append("| Game day | What the turn was for | Calls | Residents | Calls per resident |")
    out.append("|---|---|---:|---:|---:|")
    for (day, label), n in sorted(counts.items()):
        residents = len(per_day[day][label]) or 1
        out.append(f"| {day} | {label} | {n} | {residents} | {n / residents:.1f} |")
    out.append(
        "\nFrom `llm_calls` joined to the run's tool calls by (agent_id, "
        "tick). The label is what the turn's first work tool named it, not "
        "a guess made before the turn ran.\n"
    )
    return out


RUN_STARTED_AT: float = 0.0
HOME_ROOT: str = ""


def engine_home_root() -> str:
    """The engine homes THIS run used: `--home-root`, the environment, the
    run manifest, then the historical default - in that order, once, in
    `main`. Every session-log read goes through here, so no section can
    quietly look in a different run's folder again."""
    return HOME_ROOT or os.environ.get("KASUKABE_HOME_ROOT") or "/tmp/kasukabe-agents"


def _trace_rows(path: str, sql: str, *params: Any) -> list[tuple]:
    """A read-only query that answers [] for a missing table or file."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _trace_manifest(path: str) -> dict[str, Any]:
    """This run's `run_manifest` row, with `env` parsed. {} if there is none."""
    rows = _trace_rows(path, "select run_id, started_at, env from run_manifest "
                             "order by started_at desc limit 1")
    if not rows:
        return {}
    run_id, started_at, env = rows[0]
    try:
        parsed = json.loads(env) if env else {}
    except ValueError:
        parsed = {}
    where = _trace_rows(path, "select checkpoint_dir from run_manifest "
                              "where run_id = ? limit 1", run_id)
    return {"run_id": run_id, "started_at": started_at, "env": parsed,
            "checkpoint_dir": (where[0][0] if where and where[0][0] else None)}


def trace_tool_counts(path: str) -> dict[str, int]:
    """Tool calls by tool, from this run's `tool_invocations`. {} if none."""
    manifest = _trace_manifest(path)
    rows = _trace_rows(path, "select tool, count(*) from tool_invocations "
                             "where run_id = ? group by 1", manifest.get("run_id"))
    if not rows:
        rows = _trace_rows(path, "select tool, count(*) from tool_invocations group by 1")
    return {str(tool).split(":", 1)[-1]: int(n) for tool, n in rows if tool}


def residents_who_acted(path: str) -> "dict[str, Any] | None":
    """This run's cast, and which of them made at least one tool call.

    None when the store has no cast or no tool_invocations, so the caller
    can fall back to session logs. Only residents are counted - a control
    business's scripted owner is not in `agents` and is not a resident.
    """
    cast = sorted(str(r[0]) for r in _trace_rows(path, "select id from agents"))
    if not cast or not _trace_rows(path, "select 1 from tool_invocations limit 1"):
        return None
    manifest = _trace_manifest(path)
    counts = dict(_trace_rows(path, "select agent_id, count(*) from tool_invocations "
                                    "where run_id = ? group by 1", manifest.get("run_id")))
    if not counts:
        counts = dict(_trace_rows(path, "select agent_id, count(*) from tool_invocations group by 1"))
    acted = [a for a in cast if counts.get(a)]
    return {
        "turns": len(cast),
        "acting": len(acted),
        "percent": round(100.0 * len(acted) / len(cast), 1) if cast else 0.0,
        "silent_residents": [a for a in cast if not counts.get(a)],
    }


def final_reading(path: str) -> dict[str, Any]:
    """What the world answered just before it stopped, if anybody asked.

    The roster, the refused intentions and the tools being served live only
    in the running world's memory: no table holds them, so a report written
    after the stop - which is every final report - printed them as "not
    recorded" or, worse, as a failed check. `run_genesis.capture_final_
    reading` now reads them once on the way down and writes them beside the
    checkpoints; the manifest says where that is.

    {} when no reading was taken, which is the honest answer for every run
    made before this existed.
    """
    where = _trace_manifest(path).get("checkpoint_dir")
    if not where:
        return {}
    file = Path(where) / "final-reading.json"
    try:
        body = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    readings = body.get("readings")
    if not isinstance(readings, dict) or not readings:
        return {}
    return {"tick": body.get("tick"), "captured_at": body.get("captured_at"), **readings}


def durable_view(path: str) -> dict[str, Any]:
    """What the trace store can say about the town after the world stops.

    The live routes this report reads (`/world/state`, `/world/authored`,
    `/world/utterances`) all go silent when the run ends, and the report used
    to print that silence as fact. This is the same town read from what was
    recorded. Anything the trace does not hold is said to be not recorded,
    never shown as zero: the trace `agents` table is not updated for dormancy
    or death, so those three counts are not guessed.
    """
    agents = _trace_rows(path, "select id, display_name, household_id from agents")
    if not agents:
        return {}
    manifest = _trace_manifest(path)
    names = {str(i): n for i, n, _ in agents if n}
    ticks = [r[0] for r in _trace_rows(path, "select max(tick) from tool_invocations")
             + _trace_rows(path, "select max(tick) from llm_calls") if r and r[0] is not None]
    tick = max(ticks) if ticks else None
    businesses = _trace_rows(path, "select count(*) from businesses")
    state = {
        "run_id": manifest.get("run_id") or "",
        "population": len(agents),
        "alive": "not recorded", "dormant": "not recorded", "dead": "not recorded",
        "tick": tick if tick is not None else "—",
        "game_day": (tick // 144) if isinstance(tick, int) else "—",
        "households": len({h for _, _, h in agents if h}),
        "businesses": businesses[0][0] if businesses else "—",
    }

    skills = []
    for agent_id, task, text, first_tick in _trace_rows(
        path, "select agent_id, task_type, skill_artifact_text, min(tick) from skills "
              "where skill_artifact_text is not null group by agent_id, task_type"
    ):
        try:
            body = json.loads(text) if text else {}
        except ValueError:
            body = {}
        steps = body.get("steps") or []
        holders = [a for (a,) in _trace_rows(
            path, "select distinct agent_id from skills where task_type = ?", task)]
        skills.append({
            "name": body.get("name") or task, "author_agent_id": agent_id,
            "created_tick": first_tick, "steps": len(steps),
            "discounts_tools": [s.get("ref") for s in steps
                                if isinstance(s, dict) and s.get("kind") == "tool" and s.get("ref")],
            "holders": holders or [agent_id], "governmental": False, "version": 1,
        })
    proficiency: dict[str, dict[str, int]] = {}
    repetitions: dict[str, dict[str, int]] = {}
    for agent_id, task, level, reps in _trace_rows(
        path, "select agent_id, task_type, max(proficiency_level), max(repetitions_observed) "
              "from skills group by agent_id, task_type"
    ):
        proficiency.setdefault(agent_id, {})[task] = int(level or 0)
        repetitions.setdefault(agent_id, {})[task] = int(reps or 0)
    tools = []
    for name, owner, composition, fee in _trace_rows(
        path, "select name, owner_agent_id, composition, patent_fee_paid_sim from tools_registry"
    ):
        try:
            steps = json.loads(composition) if composition else []
        except ValueError:
            steps = []
        tools.append({"tool_name": name, "creator_agent_id": owner,
                      "steps": [str(s) for s in steps], "version": 1, "patented": bool(fee)})
    utterances = []
    for speaker, listener, addressed, conv, place, content, tick_, created in _trace_rows(
        path, "select speaker_agent_id, listener_agent_id, addressed_agent_ids, conversation_id, "
              "location, content, tick, created_at from utterances order by tick, id"
    ):
        try:
            addressed_ids = json.loads(addressed) if addressed else []
        except ValueError:
            addressed_ids = []
        row = {"speaker_agent_id": speaker, "listener_agent_id": listener,
               "addressed_agent_ids": addressed_ids, "conversation_id": conv,
               "location_id": place, "content": content, "tick": tick_ or 0}
        if isinstance(created, (int, float)) and created > 1e9:
            row["created_at"] = created
        utterances.append(row)
    return {
        "state": state,
        "authored": {"skills": skills, "tools": tools, "proficiency": proficiency,
                     "repetitions": repetitions, "names": names},
        "utterances": utterances,
    }


def _run_started_at(args) -> float:
    """When this run began, from the manifest CORE-24 writes at boot.

    The trace store's own `run_manifest` row first. The JSON fallbacks below
    are the DEFAULT checkpoint directory, and run 26 checkpointed to
    `data/kasukabe-checkpoints-run26` - so its report was scoped by an older
    run's start time. The trace in `--db` is this run's by construction.
    """
    import datetime

    manifest = _trace_manifest(args.world_db or args.db)
    if manifest.get("started_at"):
        return float(manifest["started_at"])

    for path in (Path("data/kasukabe-checkpoints/run_manifest.json"),
                 Path("../data/kasukabe-checkpoints/run_manifest.json")):
        try:
            started = json.loads(path.read_text())["started_at"]
            return datetime.datetime.fromisoformat(started).timestamp()
        except Exception:  # noqa: BLE001 - no manifest is not a failure,
            continue
    print("run_report: no run manifest - session-log tallies will include "
          "EVERY run in this SEPTEMBER_HOME, not just this one",
          file=sys.stderr, flush=True)
    return 0.0


def belongs_to_this_run(record: "dict[str, Any]") -> bool:
    """Whether a session-log entry was written by the run being reported."""
    if not RUN_STARTED_AT:
        return True
    when = record.get("recorded_at")
    return not isinstance(when, (int, float)) or when >= RUN_STARTED_AT


SETTLEMENT_EVENTS = ("business.sale.completed", "business.purchase.completed", "economy.trade.settled")


def settled_by_conversation(events_db: str) -> dict[str, list[dict]]:
    """Every settled deal, filed under the conversation it was agreed in.

    A2A_CONFORMANCE §2, "a session's outcome". Since 2026-09-22 the three
    settlement events carry `conversation_id`, so the report can say, under
    each exchange, what it actually settled - which is N6 ("does what is
    agreed in speech become an act?") read straight off the page instead of
    matched by hand. {} for a trace written before the field existed.
    """
    try:
        conn = sqlite3.connect(f"file:{events_db}?mode=ro", uri=True)
        rows = conn.execute(
            "select type, payload from events where type in (?, ?, ?) order by tick",
            SETTLEMENT_EVENTS,
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    out: dict[str, list[dict]] = {}
    for kind, payload in rows:
        try:
            body = json.loads(payload) if isinstance(payload, str) else payload
        except (ValueError, TypeError):
            continue
        if not isinstance(body, dict) or not body.get("conversation_id"):
            continue
        seller = body.get("seller_agent_id") or body.get("seller_id")
        buyer = body.get("buyer_agent_id") or body.get("buyer_id")
        total = body.get("total_price_sim", body.get("price_sim"))
        out.setdefault(str(body["conversation_id"]), []).append({
            "seller": seller, "buyer": buyer, "item": body.get("item"),
            "quantity": body.get("quantity"), "total_sim": total,
        })
    return out


def justice_section(justice: "dict[str, Any] | None") -> list[str]:
    """GOV-10: DESIGN section 10's false-conviction rate, with a number.

    Read from the world (`/world/justice`, or the run's reading once it has
    stopped), because it is the only place that knows who really did it - no
    event carries the perpetrator, since residents perceive the events.

    Three different silences, kept apart: the world was never asked (None);
    nobody was convicted; convictions exist but none on a case the world
    witnessed, so none can be scored. Printing all three as "0%" would claim
    a just town on no evidence.
    """
    out = ["## Justice\n"]
    if not justice:
        out.append("The world was not asked for its case record in this run, so "
                   "the false-conviction rate is **not recorded**.\n")
        return out
    out.append(
        f"- **Cases** — {justice.get('cases', 0)} ({justice.get('thefts', 0)} thefts, "
        f"{justice.get('complaints', 0)} complaints); a name on "
        f"{justice.get('accused_named', 0)}; {justice.get('awaiting_ruling', 0)} awaiting a ruling"
    )
    out.append(
        f"- **Rulings** — {justice.get('convictions', 0)} convictions, "
        f"{justice.get('dismissed', 0)} dismissed"
    )
    rate = justice.get("false_conviction_rate")
    if not justice.get("convictions"):
        out.append("- **False-conviction rate** — nobody was convicted, so there is "
                   "no rate to give.\n")
    elif rate is None:
        out.append(f"- **False-conviction rate** — none of the "
                   f"{justice.get('convictions')} convictions was on a case the world "
                   "witnessed, so none can be scored against the truth.\n")
    else:
        out.append(f"- **False-conviction rate** — **{100 * rate:.0f}%**: "
                   f"{justice.get('false', 0)} of {justice.get('true', 0) + justice.get('false', 0)} "
                   f"scorable convictions named the wrong person "
                   f"({justice.get('unknowable', 0)} more could not be scored).\n")
    return out


def scenario_section(events_db: str) -> list[str]:
    """The run's own setup, from the `run.scenario.set` event it recorded.

    Empty for every run that had no scenario - which is every run before
    scenarios existed, and any run booted from flags alone.
    """
    try:
        conn = sqlite3.connect(f"file:{events_db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "select payload from events where type = 'system.scenario.set' "
                "order by rowid limit 1"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    if not rows:
        return []
    try:
        body = json.loads(rows[0][0])
    except (TypeError, json.JSONDecodeError):
        return []
    lines = ["## The situation this run was set up in\n"]
    for said in body.get("says") or []:
        lines.append(f"- {said}")
    path = body.get("path")
    if path:
        lines.append(f"\nFrom `{path}`.")
    lines.append("")
    return lines


def build(world: str, db: str, world_db: str | None = None) -> str:
    events_db = world_db or db
    state = get(world, "/world/state") or {}
    talk = get(world, "/world/utterances?limit=5000") or {}
    authored = get(world, "/world/authored") or {}
    vitals = get(world, "/world/vitals") or {}
    refusals = get(world, "/world/refusals") or {}
    justice = get(world, "/world/justice")
    money = spend(db)
    roster = get(world, "/agents") or get(world, "/agent-viewer/agents?limit=500") or []
    calls = api_calls(db)
    turn_detail = sessions_by_engine(db)
    pulse = calls_per_minute(db)
    preflight = get(world, "/world/preflight")
    invocations = invocations_for_labelling(
        (get(world, "/world/tool_invocations") or {}).get("invocations") or [], db
    )
    world_up = bool(state)
    durable = {} if world_up else durable_view(events_db)
    if durable:
        state = durable["state"]
        authored = {**durable["authored"], **{k: v for k, v in authored.items() if v}}
        if not talk.get("utterances"):
            talk = {"utterances": durable["utterances"]}
    reading = {} if world_up else final_reading(events_db)
    if reading:
        state = {**(state or {}), **(reading.get("state") or {})}
        roster = roster or reading.get("agents") or []
        vitals = vitals or reading.get("vitals") or {}
        justice = justice or reading.get("justice")
        preflight = preflight or reading.get("preflight")
        authored = {**authored, **{k: v for k, v in (reading.get("authored") or {}).items() if v}}
        if not talk.get("utterances"):
            talk = reading.get("utterances") or talk
    refusals_known = world_up or reading.get("refusals") is not None
    if not world_up:
        refusals = reading.get("refusals") or {}
    served_tools = (get(world, "/tools/registry") if world_up
                    else reading.get("registry"))
    names: dict[str, str] = authored.get("names") or {}

    def who(agent_id: str | None) -> str:
        if not agent_id:
            return "—"
        return names.get(agent_id) or agent_id

    L: list[str] = []
    add = L.append
    now = ist_now()

    run_id = state.get("run_id") or ""
    add("# Kasukabe run report\n")
    add(f"<!-- run-started: {run_id} -->\n")
    if world_up:
        add(f"*Generated {now}. Every figure is read from the running world.*\n")
    else:
        add(f"*Generated {now}. The world had stopped answering, so the figures "
            "below are read from this run's trace store; anything only the "
            "running world holds is marked as not recorded.*\n")
        if reading:
            add(f"*The roster, the refused intentions and the tools being served "
                f"are from the **final reading** this run took of itself at tick "
                f"`{reading.get('tick')}`, on the way down — those three live in "
                f"the world's memory and in no table.*\n")
    if run_id:
        add(f"*Run id `{run_id}` — this document belongs to one boot of the "
            f"world and is refreshed in place until that run ends.*\n")

    from world.run_checklist import checklist_section

    for line in checklist_section(events_db, {"justice": justice, "agents": roster}):
        add(line)

    for line in scenario_section(events_db):
        add(line)

    add("## The run\n")
    add("| | |")
    add("|---|---|")
    add(f"| Residents | **{state.get('population', '—')}** |")
    add(f"| Alive / dormant / dead | {state.get('alive','—')} / {state.get('dormant','—')} / {state.get('dead','—')} |")
    add(f"| Tick reached | **{state.get('tick','—')}** (game day {state.get('game_day','—')}) |")
    lost_instrumentation = money and not money["calls"] and town_was_busy(db)
    if lost_instrumentation:
        add("| Model calls | **not recorded** |")
        add("| Dispatch success | **not recorded** |")
        add("| Spend | **not recorded** |")
    elif money:
        refused_by_cap = money.get("cap_refusals", 0)
        dispatched = money["calls"] - refused_by_cap
        pct = (100.0 * money["ok"] / dispatched) if dispatched else 0.0
        add(f"| {_served_by(db)} | **{money['calls']:,}** |")
        add(f"| Dispatch success | **{pct:.1f}%** |")
        if refused_by_cap:
            add(f"| Refused by the spend cap | {refused_by_cap:,} — the governor "
                "ending the run, not a broken link |")
        add(f"| Spend | **${money['usd']:.2f}** |")
    acting = residents_who_acted(events_db)
    if acting is None:
        engine_turns = [
            {"agent_id": name, "tool_calls": data.get("tool_calls") or 0}
            for name, data in sessions(engine_home_root()).items()
        ]
        acting = acting_turn_rate([
            {"agent_id": e["agent_id"], "tool_calls": 1 if e["tool_calls"] else 0}
            for e in engine_turns
        ]) if engine_turns else None
    if acting:
        add(f"| Residents who acted | **{acting['acting']} of {acting['turns']}** "
            f"({acting['percent']:.0f}%) |")
        if acting["silent_residents"]:
            add(f"| Never touched the world | **{len(acting['silent_residents'])}**: "
                f"{', '.join(who(a) for a in acting['silent_residents'])} |")
    add(f"| Households / businesses | {state.get('households','—')} / {state.get('businesses','—')} |")
    add("")

    if lost_instrumentation:
        add(
            f"> **{MISSING_TRACE_MARKER}.** This town produced utterances and "
            f"memories, which are only ever produced by agent turns, so the "
            f"calls were made and billed - `llm_calls` is empty because the "
            f"record of them did not reach this file. The usual cause is a "
            f"broker left running across a delete or replacement of the trace "
            f"store, which keeps writing into a file that no longer has a name. "
            f"Ask the broker for the real figure: `GET /admin/spend`. Every "
            f"other number in this report is read from the world and is "
            f"unaffected.\n"
        )

    if money.get("failures"):
        add("Failures by which link broke:\n")
        for hop, n in money["failures"]:
            add(f"- `{hop}` × {n}")
        add("")

    skills = authored.get("skills") or []
    tools = authored.get("tools") or []
    add("## What the town invented\n")
    if not skills and not tools:
        add("**Nothing was authored.**\n")
        add("Not a failure of the machinery — `skillify` and `toolify` were "
            "available to every agent throughout. An agent is prompted to write "
            "a procedure down only after repeating one job several times, and "
            "nobody crossed that line. The repetition counts below say how "
            "close the town got.\n")
    else:
        add(f"**{len(skills)} skill(s) written, {len(tools)} tool(s) invented.**\n")
        if tools:
            add("### Tools invented with `toolify`\n")
            for t in tools:
                add(f"**`{t['tool_name']}`** — {who(t.get('creator_agent_id'))}"
                    f" (`{t.get('creator_agent_id')}`)  ")
                add(f"steps: {', '.join(t.get('steps') or []) or '—'}"
                    f" · version {t.get('version', 1)}"
                    f"{' · patented' if t.get('patented') else ''}\n")
        if skills:
            add("### Skills written with `skillify`\n")
            for s in skills:
                holders = s.get("holders") or []
                add(f"**{s['name']}** — {who(s.get('author_agent_id'))}"
                    f" (`{s.get('author_agent_id')}`), tick {s.get('created_tick')}  ")
                add(f"{s.get('steps')} steps · discounts "
                    f"{', '.join(s.get('discounts_tools') or []) or 'nothing'}"
                    f" · held by {len(holders)}"
                    f"{' · **standing order**' if s.get('governmental') else ''}"
                    f" · version {s.get('version', 1)}\n")

    prof = {k: v for k, v in (authored.get("proficiency") or {}).items() if v}
    reps = authored.get("repetitions") or {}
    add("## Learning by doing\n")
    if not prof:
        add("No agent earned proficiency in anything.\n")
    else:
        add("Proficiency is earned only by completing work — a skill nobody "
            "walks stays at the 1 its author was seeded with.\n")
        add("| Resident | Skill | Proficiency | Completions |")
        add("|---|---|---:|---:|")
        for agent_id, held in sorted(
            prof.items(), key=lambda kv: -max(kv[1].values() or [0])
        ):
            for skill, level in sorted(held.items(), key=lambda kv: -kv[1]):
                add(f"| {who(agent_id)} | {skill} | {level} | "
                    f"{(reps.get(agent_id) or {}).get(skill, 0)} |")
        add("")

    rows = talk.get("utterances") or []
    add("## The conversation\n")
    if not rows:
        add("Nobody spoke.\n")
    else:
        ex = threads(rows)
        threaded = sum(1 for t in ex if t.get("conversation_id"))
        add(f"**{len(rows)} utterances in {len(ex)} exchanges.** Grouped by the "
            f"conversation id the world assigned when the words were spoken "
            f"({threaded} of {len(ex)}); anything the world could not thread falls "
            "back to place and participants.\n")
        settled = settled_by_conversation(events_db)
        with_deal = sum(1 for t in ex if settled.get(str(t.get("conversation_id") or "")))
        said_why = sum(1 for u in rows if u.get("intent"))
        add(f"**{with_deal} of {len(ex)} exchanges settled a deal**, recorded under the "
            f"conversation it was agreed in. **{said_why} of {len(rows)} remarks said why** "
            "(an optional intent, kept on the record and never heard by anyone in the town).\n")
        for t in ex:
            heads = [who(p) for p in t["people"]]
            head = (", ".join(heads[:-1]) + " and " + heads[-1]) if len(heads) > 1 else heads[0]
            span = (f"t{t['first_tick']}"
                    if t["first_tick"] == t["last_tick"]
                    else f"t{t['first_tick']}–{t['last_tick']}")
            turns = len(t["lines"])
            add(f"### {head} — {t['place'] or 'somewhere'} · {span}"
                f" · {turns} turn{'' if turns == 1 else 's'}"
                f"{'' if turns > 1 else ' (no reply)'}\n")
            for i, u in enumerate(t["lines"]):
                prev = t["lines"][i - 1] if i else None
                is_reply = bool(
                    prev and prev.get("listener_agent_id")
                    and prev["listener_agent_id"] == u.get("speaker_agent_id")
                )
                stamp = u.get("said_at") or u.get("created_at")
                when = f"t{u.get('tick')}"
                if stamp:
                    when += " · " + ist(stamp, with_date=False)
                listener = u.get("listener_agent_id")
                to = f" → {who(listener)}" if listener else ""
                mark = "↳ **replies** " if is_reply else ""
                why = f" *(why: {u['intent']})*" if u.get("intent") else ""
                add(f"> {'  ' if is_reply else ''}`{when}` {mark}"
                    f"**{who(u.get('speaker_agent_id'))}**{to}: "
                    f"{u.get('content','')}{why}\n")
            deals = settled.get(str(t.get("conversation_id") or ""), [])
            if deals:
                add("**Settled in this conversation:** " + "; ".join(
                    f"{who(d['seller'])} → {who(d['buyer'])}, {d['quantity']} {d['item']} "
                    f"for {d['total_sim']} sim" for d in deals) + ".\n")

    ref = refusals.get("refusals") or []
    add("## What the town could not do\n")
    if not refusals_known:
        add("Refusals are held by the running world, which had stopped "
            "answering when this was written, so they are not recorded here. "
            "That is a missing reading, not an empty one.\n")
    elif not ref:
        add("No refused intentions. Nobody reached for something the world "
            "could not give them.\n")
    else:
        buckets: dict[str, dict[str, Any]] = {}
        for r in ref:
            key = r.get("unmet_requirement") or "unknown"
            b = buckets.setdefault(key, {"agents": set(), "n": 0, "tools": set()})
            b["agents"].add(r.get("agent_id"))
            b["n"] += 1
            if r.get("tool_name"):
                b["tools"].add(r["tool_name"])
        add("| What was missing | Agents | Attempts | Reaching for |")
        add("|---|---:|---:|---|")
        for key, b in sorted(buckets.items(), key=lambda kv: -kv[1]["n"]):
            add(f"| `{key}` | {len(b['agents'])} | {b['n']} | "
                f"{', '.join(sorted(b['tools'])) or '—'} |")
        add("")

    registry_view = get(world, "/tools/registry")
    if isinstance(registry_view, list):
        registry_tools = registry_view
    elif isinstance(registry_view, dict):
        registry_tools = registry_view.get("tools") or []
    else:
        registry_tools = []
    gated = gated_tool_names()
    served = [
        {"name": t.get("name"), "gated": t.get("name") in gated}
        for t in registry_tools
        if isinstance(t, dict) and t.get("name")
    ]
    if served:
        counts = trace_tool_counts(events_db) or tool_call_counts(engine_home_root())
        never = untouched_tools(
            served, counts, tried={r["tool_name"] for r in ref if r.get("tool_name")}
        )
        if never:
            add("## What nobody reached for\n")
            add(f"**{len(never)} of {len(served)} tools need no particular place, "
                "and were never called once.** A tool that exists, is visible to "
                "everyone and goes untouched all run is not a refusal - nobody "
                "tried it - and no refusal table can show it.\n")
            add(", ".join(f"`{t['name']}`" for t in never) + "\n")

    def _events(kind: str) -> list[dict]:
        try:
            conn = sqlite3.connect(f"file:{events_db}?mode=ro", uri=True)
            rows = conn.execute(
                "select payload, tick from events where type = ? order by tick", (kind,)
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return []
        out = []
        for payload, tick in rows:
            try:
                record = json.loads(payload) if isinstance(payload, str) else payload
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            record.setdefault("tick", tick)
            out.append(record)
        return out

    def _classified_calls(path: str) -> list[dict]:
        """Every billed call, with who paid and what it was for.

        INFRA-19. From the BROKER's store, which is where `llm_calls`
        lives - the same two-file distinction EVAL-01 was made of.
        """
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            rows = conn.execute(
                "select payer_agent_id, agent_id, call_class, cents_debited "
                "from llm_calls"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return []
        return [
            {
                "payer_agent_id": payer,
                "agent_id": agent,
                "call_class": call_class,
                "cents_debited": cents,
            }
            for payer, agent, call_class, cents in rows
        ]

    def _all_events() -> list[dict]:
        """Every recorded event, for the readers that select their own.

        EVAL-05. `time_spent` and `diffusion` each look at four or five
        event types, and `_events(kind)` takes one - handing them a list
        assembled type by type here would put those names in two places
        and is exactly how EVAL-01's five wrong names happened.

        Bounded by the same rule `is_worth_recording` already applies at
        write time: the noisy per-tick types are not in this table at all,
        so "every row" is thousands, not millions.
        """
        try:
            conn = sqlite3.connect(f"file:{events_db}?mode=ro", uri=True)
            rows = conn.execute("select payload, tick, type from events").fetchall()
            conn.close()
        except sqlite3.Error:
            return []
        out: list[dict] = []
        for payload, tick, kind in rows:
            try:
                record = json.loads(payload) if isinstance(payload, str) else payload
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            out.append({"type": kind, "tick": tick, "payload": record})
        return out

    def _transactions(reason: str) -> list[dict]:
        """Money that moved with no event behind it.

        Upkeep is charged as a transfer and publishes nothing, so an
        event-shaped read of it returns [] and the section prints a
        comfortable zero over real money. `transactions` is the only
        honest source for it (EVAL-01 / CORE-04).
        """
        try:
            conn = sqlite3.connect(f"file:{events_db}?mode=ro", uri=True)
            rows = conn.execute(
                "select amount, tick, from_agent_id from transactions "
                "where reason = ? order by tick",
                (reason,),
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return []
        return [
            {"amount": int(a or 0), "tick": t, "agent_id": f}
            for a, t, f in rows
        ]

    def _norm(records: list[dict], **aliases: str) -> list[dict]:
        """Fill the key a section reads from the key the emitter writes.

        The emitters were written before the sections and use their own
        names (`amount_sim`, `debtor_agent_id`). Renaming an emitter would
        break replay of every existing trace, so the report adapts instead
        - and does it in one place, named, rather than in six `.get`s.
        """
        for r in records:
            for want, have in aliases.items():
                if want not in r and have in r:
                    r[want] = r[have]
        return records

    for line in prices_section(_events("economy.price.posted")):
        add(line)
    for line in stakes_section(
        upkeep=_transactions("daily_upkeep"),
        head_tax=_transactions("tax"),
        head_tax_arrears=_norm(_events("economy.head_tax_debt.accrued"),
                               amount="amount_sim", agent_id="debtor_agent_id"),
        energy=_events("economy.energy.bought"),
        dormancies=_norm(_events("health.agent.dormant"),
                         cause="reason"),
    ):
        add(line)
    for line in wages_section(
        promised=_events("business.wage.promised"),
        paid=_events("business.wage.paid"),
    ):
        add(line)
    for line in time_section(_all_events()):
        add(line)
    for line in diffusion_section(_all_events()):
        add(line)
    for line in learning_section(_labelled_calls(db, invocations)):
        add(line)
    for line in programs_section(invocations):
        add(line)
    for line in housekeeping_section(_classified_calls(db)):
        add(line)

    for line in elections_section(db):
        add(line)

    if vitals:
        add("## The town itself\n")
        h = vitals.get("health") or {}
        b = vitals.get("built") or {}
        f = vitals.get("food") or {}
        add(f"- **Health** — {h.get('alive',0)} alive, {h.get('dormant',0)} dormant "
            f"(cannot afford to think), {h.get('dead',0)} died")
        add(f"- **Built** — {b.get('building_count',0)} buildings raised, "
            f"{b.get('business_count',0)} businesses, {b.get('household_count',0)} households")
        held = f.get("holdings") or {}
        total = sum(held.values())
        add(f"- **Food** — {total} units held across all owners"
            + (f" ({', '.join(f'{k} {v}' for k, v in sorted(held.items()))})" if held else "")
            + f", {len(f.get('zones') or [])} resource zones")
        t = vitals.get("treasury") or {}
        add(f"- **Treasury** — {t.get('balance_sim', '—')} sim\n")

    for line in justice_section(justice):
        add(line)

    add("## Every resident\n")
    people = roster if isinstance(roster, list) else (roster.get("agents") or [])
    if not people:
        add("The world reported no roster.\n")
    else:
        add("| # | Resident | Age | Occupation | Where | Alive | Dormant | Sim | USD ¢ | Skills |")
        add("|--:|---|--:|---|---|:-:|:-:|--:|--:|---|")
        for i, p in enumerate(sorted(people, key=lambda x: x.get("agent_id", "")), 1):
            aid = p.get("agent_id", "")
            held = (authored.get("proficiency") or {}).get(aid) or {}
            skills = ", ".join(f"{k} {v}" for k, v in sorted(held.items(), key=lambda kv: -kv[1]))
            dorm = (p.get("dormancy") or {}).get("is_dormant")
            add(f"| {i} | **{p.get('name', aid)}** `{aid}` | "
                f"{int(p.get('age_years') or 0)} | {p.get('occupation', '—')} | "
                f"{p.get('location_id', '—')} | {'✓' if p.get('alive') else '✗'} | "
                f"{'●' if dorm else '·'} | {p.get('sim_balance', '—')} | "
                f"{p.get('usd_wallet_cents', '—')} | {skills or '—'} |")
        add("")

    if turn_detail:
        add("## Every turn, in detail" + NL)
        live = sum(1 for turns in turn_detail.values() if any(t[9] for t in turns))
        add(f"**{live} of {len(turn_detail)} engines have completed at least one turn.** "
            "Each engine's most recent turns follow, newest first: what went in, "
            "what the model reasoned and said back, and what it then called. "
            "This is read from the trace store, which is the only place holding "
            "BOTH halves of a turn; *Sessions, per engine* below counts the same "
            "turns from each engine's own log and is the authority on how many "
            "there were." + NL)

        for aid in sorted(turn_detail):
            turns = turn_detail[aid]
            ok_turns = [t for t in turns if t[9]]
            tool_turns = [t for t in turns if t[4]]
            spent = sum(t[8] for t in turns)
            box = "x" if ok_turns else " "
            add(f"- [{box}] **{who(aid)}** (`{aid}`) — {len(turns)} turns shown, "
                f"{len(ok_turns)} succeeded, {len(tool_turns)} made a tool call, "
                f"${spent:.4f}")
            add("")
            add("  | # | When | Input | Model — reasoning &amp; output | Tool call | Stop | In/Out | Cost |")
            add("  |--:|---|---|---|---|---|--:|--:|")
            for i, (ts, purpose, prompt, said, tools, stop, tin, tout, cost, ok, err) in enumerate(turns, 1):
                when = ist(ts)
                shown_in = _cell(prompt, 90) or ("*(body truncated at the 64 KB cap)*" if not ok else "—")
                shown_out = _cell(said, 110) or "—"
                result = stop if ok else f"**{err or 'failed'}**"
                add(f"  | {i} | {when} | {shown_in} | {shown_out} | {tools or '—'} | "
                    f"{result} | {tin}/{tout} | ${cost:.6f} |")
            add("")

    if pulse:
        add("## API calls, minute by minute\n")
        add("Newest first. This is every request the town made to think.\n")
        add("| Minute | Calls | Succeeded | Spend |")
        add("|---|--:|--:|--:|")
        for minute, n, ok, usd in pulse[:60]:
            add(f"| {ist(minute)} | {n} | {ok or 0} | ${usd or 0:.4f} |")
        add("")

    if calls:
        add("### The most recent calls\n")
        add("| Time | Agent | Purpose | In | Out | Cost | ms | Result |")
        add("|---|---|---|--:|--:|--:|--:|---|")
        for (ts, aid, _model, purpose, tin, tout, cost, ms, ok, hop, err) in calls[:120]:
            result = "ok" if ok else f"**{err or 'failed'}** ({hop or 'untagged'})"
            add(f"| {ist(ts)} | {who(aid)} | {purpose} | {tin} | {tout} | "
                f"${cost:.6f} | {ms} | {result} |")
        add("")

    home_root = engine_home_root()
    sess = sessions(home_root)
    add("## Sessions, per engine\n")
    if not sess:
        add(f"No engine homes under `{home_root}`.\n")
    else:
        add("Every engine writes one line per tick it thinks, into its own "
            "`SEPTEMBER_HOME`. The trace store knows what each call *cost*; "
            "only this says what the agent was *thinking* when it made it.\n")
        add("| Engine | Resident | Sessions | Turns | Tool calls | Model | Log |")
        add("|---|---|--:|--:|--:|---|---|")
        for aid in sorted(sess):
            v = sess[aid]
            add(f"| `{aid}` | {who(aid)} | {v['sessions']} | {v['turns']} | "
                f"{v['tool_calls']} | {', '.join(v['models']) or '—'} | "
                f"{'✓' if v['log_exists'] else '**missing**'} |")
        add("")

        add("### Transcripts\n")
        add("`input` is what the world told the agent · `reasoning` is what "
            "it thought · `said` is what the town heard · `actions` are the "
            "tool calls that resulted, with parameters and what came back.\n")
        for aid in sorted(sess):
            v = sess[aid]
            if not v["recent"]:
                add(f"#### {who(aid)} `{aid}`\n")
                add("*Has not thought yet this run.*\n")
                continue
            shown = v["recent"]
            add(f"#### {who(aid)} `{aid}`\n")
            add(f"*{v['sessions']} session(s) this run"
                + (f", showing the most recent {len(shown)}"
                   if v["sessions"] > len(shown) else "")
                + f" · {v['turns']} turns · {v['tool_calls']} tool calls · "
                + f"log: `{v['log_path']}`*\n")
            for r in shown:
                st = r.get("done_status") or {}
                if not belongs_to_this_run(r):
                    continue
                when = ist(r.get("recorded_at"))
                add(f"**`{when}`** · {st.get('model_id', '—')} · "
                    f"{st.get('num_turns', 0)} turn(s) · "
                    f"{st.get('tool_calls_count', 0)} tool call(s) · "
                    f"exit `{st.get('exit_reason', '—')}`\n")
                add(f"> **input** — {_flat(r.get('input'), 500)}\n")
                reasoning = (r.get("reasoning") or "").strip()
                add(f"> **reasoning** — {_flat(reasoning, 700) if reasoning else '*(none)*'}\n")
                said = (r.get("output") or "").strip()
                add(f"> **said** — {_flat(said, 700) if said else '*(nothing — tool calls only)*'}\n")
                acts = r.get("actions") or []
                if not acts:
                    add("> **actions** — *none*\n")
                else:
                    for a in acts:
                        params = a.get("params")
                        shown = json.dumps(params, default=str) if params else ""
                        add(f"> **action** `{a.get('tool', '?')}` "
                            f"{_flat(shown, 220)}  ")
                        add(f"> ↳ {_flat(a.get('result'), 300)}\n")
            add("")

    add("## The instrument\n")
    add("| Part | State | Detail |")
    add("|---|:-:|---|")

    homes = Path(engine_home_root())
    logs = sorted(homes.glob("*/session_log.jsonl")) if homes.is_dir() else []
    fresh = 0
    newest = 0.0
    for f in logs:
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        newest = max(newest, m)
        if time.time() - m < 300:
            fresh += 1
    log_age = f"{int(time.time() - newest)}s ago" if newest else "never"
    add(f"| Session logs | {'✓' if fresh else '✗'} | "
        f"{len(logs)} agent logs under `{homes}`, {fresh} written in the last "
        f"5 min · newest {log_age} |")

    brains = sorted(homes.glob("*/brain.sqlite")) if homes.is_dir() else []
    total_mb = sum(f.stat().st_size for f in brains) // (1024 * 1024) if brains else 0
    add(f"| Per-agent `SEPTEMBER_HOME` | {'✓' if brains else '✗'} | "
        f"{len(brains)} own `brain.sqlite`, {total_mb} MB total |")

    obs = get(os.environ.get("OBSERVATORY_URL", "http://127.0.0.1:8500"), "/api/health")
    if obs and obs.get("available"):
        add(f"| Observatory | ✓ | dispatch {obs.get('dispatch_success', 0):.1%}, "
            f"{obs.get('engines_alive','—')} engines alive, "
            f"verdict *{obs.get('verdict','—')}* |")
    else:
        add("| Observatory | ✗ | not reachable on :8500 |")

    eng = get(world, "/world/engines") or {}
    down = eng.get("down") or []
    add(f"| Engines | {'✓' if eng.get('alive') == eng.get('expected') and eng.get('alive') else '✗'} | "
        f"{eng.get('alive','—')}/{eng.get('expected','—')} alive"
        + (f" · down: {', '.join(d['agent_id'] for d in down)}" if down else "") + " |")
    timed_out = eng.get("timed_out_ticks")
    if timed_out is not None:
        add(f"| Turns abandoned | {'✓' if not timed_out else '!'} | "
            f"{timed_out} turn(s) hit the {eng.get('tick_timeout_s','?')}s dispatch "
            f"limit and were discarded after paying for their calls |")

    if served_tools is None:
        add("| Authoring tools | — | not recorded: the world had stopped when this "
            "was written and no final reading was taken |")
    else:
        served = {t.get("name") for t in served_tools if isinstance(t, dict)}
        need = {"skillify", "toolify", "revise_skill", "revise_tool",
                "describe_primitives", "quote_action_cost"}
        missing = sorted(need - served)
        add(f"| Authoring tools | {'✓' if not missing else '✗'} | "
            f"{len(served)} tools served · "
            f"{'all authoring tools reachable' if not missing else 'MISSING ' + ', '.join(missing)} |")
    add("")

    if preflight and preflight.get("checks"):
        add("## Preflight at last check\n")
        add(f"**{preflight.get('passed')}/{preflight.get('total')} passed** — "
            f"{'ready' if preflight.get('ready') else '**NOT READY**'}\n")
        add("| | Check | Detail |")
        add("|:-:|---|---|")
        for c in preflight["checks"]:
            mark = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}.get(c.get("status"), "?")
            add(f"| {mark} | {c.get('name')} | {c.get('detail','')} |")
        add("")

    add("---\n")
    add("*Written by `world/run_report.py`, mixing LIVE world reads with the "
        "trace store. Generate it before stopping the run: once the world stops, "
        "live rows fall back to the trace, and refusals and who is alive, dormant "
        "or dead are marked not recorded. Before that fallback, `run-20-report.md` "
        "showed `Residents --` and \"Nobody spoke\" for a run that did both.*\n")
    return "\n".join(L) + "\n"


LIVE_MARKER = "<!-- LIVE REPORT BELOW - REGENERATED EVERY REFRESH, DO NOT EDIT -->"


def splice(out: Path, generated: str) -> str:
    """The file's hand-written preamble, then the freshly generated report.

    A file with no marker yet gets one appended after whatever it already
    holds - so pointing this at an existing document adopts it rather than
    destroying it, which matters the first time somebody runs this against
    a file they spent an afternoon writing.
    """
    try:
        existing = out.read_text(encoding="utf-8")
    except OSError:
        existing = ""

    preamble = ""
    if LIVE_MARKER in existing:
        preamble = existing.split(LIVE_MARKER)[0].rstrip() + "\n\n"
    elif existing.strip():
        preamble = existing.rstrip() + "\n\n"

    return preamble + LIVE_MARKER + "\n\n" + generated


def world_answers(world: str) -> bool:
    """Is the world still serving the live reads this report is made of?"""
    return get(world, "/world/state") is not None


def write_report(out: "Path", build_text, *, world_answers=None) -> bool:
    """Build the report and write it - unless that would blank a live one.

    `world_answers` is None for a report generated by hand after a run, which
    is documented to read a world that is no longer there. In `--watch` it is
    set, and then a write needs the world answering both BEFORE the build and
    AFTER it.

    Run 26. On "spend cap reached - writing the final report" the watcher
    regenerated while the world was shutting down, so every live row came back
    empty ("Nobody spoke", "No agent earned proficiency", "Alive / dormant /
    dead - / - / -") and the 659-line live snapshot from four minutes earlier
    was overwritten with a 519-line blank one. The module docstring's own rule
    is "generate the report before stopping the run"; the tool built to honour
    it broke it on its last write. Checking afterwards as well as before is
    what catches a world that stops answering halfway through a build.
    """
    if world_answers is not None and not world_answers():
        print("world is not answering - keeping the last live snapshot rather "
              "than overwriting it with blank rows", flush=True)
        return False
    text = build_text()
    if world_answers is not None and not world_answers():
        print("world stopped answering while the report was being built - "
              "discarding it and keeping the last live snapshot", flush=True)
        return False
    out.write_text(splice(out, text), encoding="utf-8")
    print(f"wrote {out} ({len(text.splitlines())} lines)", flush=True)
    return True


def run_is_over(world: str, db: str) -> bool:
    """Has this run finished, either by cap or by the world going away?

    Two signals, because they mean different things and both end the run:
    the spend governor tripping (the intended ending) and the world no
    longer answering (an unintended one). Either way the document should be
    closed off with a final write rather than left mid-refresh.
    """
    state = get(world, "/world/state")
    if state is None:
        return True

    cap = os.environ.get("SPEND_CAP_USD")
    if not cap:
        return False
    try:
        ceiling = float(cap)
    except ValueError:
        return False
    return spend(db).get("usd", 0.0) >= ceiling


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--world", default=DEFAULT_WORLD)
    ap.add_argument("--db", default=DEFAULT_DB,
                    help="the BROKER's trace store - llm_calls live here")
    ap.add_argument("--since", type=float, default=None,
                    help=("unix timestamp this run began. Session logs are "
                          "appended to and never cleared, so without this "
                          "every tally read from one is the sum of every run "
                          "the machine has ever done. Read from the run "
                          "manifest when not given."))
    ap.add_argument("--world-db", default=None,
                    help=("the WORLD's trace store - events, utterances and "
                          "transactions live here (TRACE_SQLITE_PATH). Run 24 "
                          "reported 'Nobody posted a price' over two real "
                          "posts and a completed sale because these two were "
                          "assumed to be one file. Defaults to --db."))
    ap.add_argument("--home-root", default=None,
                    help=("the engine homes this run used (KASUKABE_HOME_ROOT). "
                          "Read from the run manifest when not given; run 26's "
                          "report guessed a default folder and reported run 25's "
                          "cast."))
    ap.add_argument("-o", "--out", default=None,
                    help="write to this file instead of docs/runs/run-N-report.md")
    ap.add_argument("--watch", type=int, metavar="SECONDS", nargs="?", const=300,
                    help="rewrite on an interval so a long run cannot lose its "
                         "history (bare --watch means every 5 minutes)")
    args = ap.parse_args(argv[1:])

    out = Path(args.out) if args.out else run_doc_for(args.world)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"run report -> {out}", flush=True)

    def once(*, live_only: bool = False) -> bool:
        global RUN_STARTED_AT, HOME_ROOT
        RUN_STARTED_AT = args.since or _run_started_at(args)
        HOME_ROOT = (
            args.home_root
            or os.environ.get("KASUKABE_HOME_ROOT")
            or (_trace_manifest(args.world_db or args.db).get("env") or {}).get("KASUKABE_HOME_ROOT")
            or ""
        )
        return write_report(
            out,
            lambda: build(args.world, args.db, args.world_db),
            world_answers=(lambda: world_answers(args.world)) if live_only else None,
        )

    try:
        once(live_only=bool(args.watch))
    except Exception as exc:  # noqa: BLE001
        print(f"report failed, will retry: {exc}", file=sys.stderr, flush=True)
        if not args.watch:
            return 1
    if args.watch:
        while True:
            time.sleep(args.watch)
            try:
                once(live_only=True)
                if run_is_over(args.world, args.db):
                    if world_answers(args.world):
                        print("spend cap reached — writing the final report", flush=True)
                        once(live_only=True)
                    else:
                        print(
                            "the run is over and the world has stopped answering "
                            "- the last live snapshot stands",
                            flush=True,
                        )
                    return 0
            except Exception as exc:  # noqa: BLE001 - a reporter must not die
                print(f"report failed, will retry: {exc}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
