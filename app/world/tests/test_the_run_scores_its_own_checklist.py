"""A run scores its own checklist.

docs/GOALS.md's standing rule is that every run report opens with which boxes
it ticked. That was done by hand, from the report's prose. `world/run_checklist.py`
scores every run-decidable item from the trace - one query each - and the
report opens with the table. These tests build a small trace in which some
things happened and some did not, and hold the scorer to both.
"""

from __future__ import annotations

import inspect
import json
import sqlite3

from world.run_checklist import checklist_section, score


def _trace(path) -> str:
    db = sqlite3.connect(path)
    db.executescript("""
        create table events (type text, payload text, tick integer);
        create table llm_calls (agent_id text, tick integer, purpose text, success integer,
                                input_tokens integer, cost_nano_dollars integer, created_at text,
                                request_body text);
        create table tool_invocations (agent_id text, tick integer, tool text, namespace text,
                                       owner_id text, program_id text);
        create table transactions (reason text, from_pool text);
        create table utterances (speaker_agent_id text, listener_agent_id text, conversation_id text,
                                 content text, tick integer);
    """)
    for agent in ("a1", "a2"):
        for tick in (40, 100, 200, 300):
            db.execute("insert into llm_calls values (?,?,?,?,?,?,?,?)",
                       (agent, tick, "auto:agent_turn", 1, 9000, 10_000_000, f"t{tick}", "hello"))
            db.execute("insert into tool_invocations values (?,?,?,?,?,?)", (agent, tick, "eat", "public", None, None))
    db.execute("insert into tool_invocations values ('a1', 300, 'set_tax_rate', 'government', null, null)")
    db.executemany("insert into utterances values (?,?,?,?,?)", [
        ("a1", "a2", "c1", "Eight timber for 560?", 99),
        ("a2", "a1", "c1", "Done, 560.", 99),
    ])
    events = [
        ("economy.trade.settled", {"conversation_id": "c1", "seller_agent_id": "a1", "buyer_agent_id": "a2",
                                   "item": "timber", "quantity": 8, "price_sim": 560}, 100),
        ("politics.candidacy.declared", {"agent_id": "a1"}, 50),
        ("politics.vote.cast", {"voter_agent_id": "a2"}, 300),
        ("politics.election.tallied", {"pm_agent_id": "a1", "minister_agent_ids": []}, 432),
    ]
    for kind, body, tick in events:
        db.execute("insert into events values (?,?,?)", (kind, json.dumps(body), tick))
    db.executemany("insert into transactions values (?,?)", [("tax", "agent"), ("wage", "treasury")])
    db.commit()
    db.close()
    return str(path)


def _verdicts(tmp_path) -> dict[str, str]:
    return {r["item"]: r["verdict"] for r in score(_trace(tmp_path / "trace.db"))}


def test_what_happened_is_held(tmp_path) -> None:
    v = _verdicts(tmp_path)

    assert v["GOV-1"] == "held", "an election was tallied with a PM"
    assert v["GOV-2"] == "held", "a2 voted and never stood"
    assert v["GOV-3"] == "held", "a seat used a government tool"
    assert v["G4"] == "held", "560 was said in c1 before the trade settled"
    assert v["ECON-11"] == "held", "tax was taken and the treasury paid a wage"
    assert v["X17"] == "held"
    assert v["ECON-4"] == "held", "eight meals over two residents"


def test_what_did_not_happen_is_not(tmp_path) -> None:
    v = _verdicts(tmp_path)

    for item in ("GOV-5", "GOV-7", "GOV-8", "ECON-10", "X16", "X15"):
        assert v[item] == "not held", item


def test_an_empty_trace_claims_nothing(tmp_path) -> None:
    sqlite3.connect(tmp_path / "empty.db").close()

    rows = score(str(tmp_path / "empty.db"))

    assert not any(r["verdict"] == "held" and r["item"] in ("GOV-1", "G1", "G6", "ECON-2") for r in rows)


def test_the_report_opens_with_it(tmp_path) -> None:
    from world import run_report

    source = inspect.getsource(run_report.build)
    assert source.index("checklist_section(") < source.index('add("## The run\\n")'), (
        "the checklist must open the report (GOALS.md's standing rule)"
    )
    text = "\n".join(checklist_section(_trace(tmp_path / "trace.db")))
    assert text.startswith("## The checklist, scored from this run")
    assert "| GOV-1 |" in text
