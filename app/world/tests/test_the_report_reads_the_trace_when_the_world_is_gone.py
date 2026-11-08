"""A report written after the world stops reads the trace, not the silence.

Run 26's report, as generated:

    | Residents | - |
    | Alive / dormant / dead | - / - / - |
    | Dispatch success | 96.6% |
    | Residents who acted | 0 of 23 (0%) |
    | Never touched the world | 23: a0047, a0064, a0176, n01, n02, ... n20 |
    Failures by which link broke: broker-internal x 26
    **Nothing was authored.**

Every line was wrong, for four separate reasons:

1. "Residents who acted" counted engine HOMES found through an environment
   variable the reporter had not been given, defaulting to another run's
   folder - so it named run 25's cast (n01-n20) and three older homes.
   Run 26 had six residents and 452 tool calls.
2. It was scoped by a start time read from the DEFAULT checkpoint
   directory, not this run's.
3. Residents, ticks, households, skills, tools and speech were read only
   from the live world, which had stopped, and the silence was printed as
   fact. All of it was sitting in the trace store.
4. All 26 "broker-internal" failures were SpendCapExceededError - the
   governor ending the run as designed, counted as a broken link.

Built on the real trace schema (the same `SqliteClient` the run writes
through), because a report test on an invented table shape proves only that
the report reads the invention.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import world.run_report as rr

HOME_ROOT = "/tmp/kasukabe-agents-run26"


def _store(tmp_path: Path, *, cap_refusals: int = 26) -> str:
    from supabase.config import SupabaseConfig
    from supabase.sqlite_client import SqliteClient

    path = tmp_path / "run26.db"
    conn = SqliteClient(SupabaseConfig(sqlite_path=str(path))).conn

    def insert(table: str, **given: object) -> None:
        columns = list(conn.execute(f"PRAGMA table_info({table})"))
        row: dict[str, object] = {}
        for _, name, kind, notnull, default, pk in columns:
            if name in given:
                row[name] = given[name]
            elif notnull and default is None:
                row[name] = 0 if kind in ("INTEGER", "REAL") else ""
        conn.execute(
            f"insert into {table} ({', '.join(row)}) values ({', '.join('?' for _ in row)})",
            list(row.values()),
        )

    insert("run_manifest", run_id="run-26", started_at=1789022402.5,
           env=json.dumps({"KASUKABE_HOME_ROOT": HOME_ROOT}))
    cast = (("a0040", "Ulrike Greenhow", "h0146"),
            ("a0043", "Jovana Ravensworth", "h0075"),
            ("a0090", "Farid Ravensworth", "h0075"))
    for agent_id, name, household in cast:
        insert("agents", id=agent_id, slug=agent_id, display_name=name, sex="female",
               household_id=household, usd_wallet_cents=200, sim_balance=5000)
    for agent_id in ("a0040", "a0043"):
        for k in range(3):
            insert("tool_invocations", invocation_id=f"{agent_id}-{k}", run_id="run-26",
                   agent_id=agent_id, tick=40 + k, tool="harvest")
    # Farid's only call is from an EARLIER run in the same file. Scoped to
    # this run's id he did nothing, and the report must say so.
    insert("tool_invocations", invocation_id="old", run_id="run-25",
           agent_id="a0090", tick=10, tool="say")
    insert("skills", agent_id="a0043", task_type="harvest_wheat", proficiency_level=48,
           repetitions_observed=14, tick=83,
           skill_artifact_text=json.dumps({"name": "harvest_wheat",
                                           "steps": [{"order": 1, "kind": "tool", "ref": "harvest"}]}))
    insert("skills", agent_id="a0043", task_type="farming", proficiency_level=53,
           repetitions_observed=17, tick=131)
    insert("tools_registry", id="tool-harvest_maintain-113", name="harvest_maintain",
           namespace="public", owner_agent_id="a0043", composition='["harvest", "eat"]',
           status="active", registered_tick=113)
    insert("utterances", id=1, speaker_agent_id="a0090", listener_agent_id="a0043",
           conversation_id="c1", location="Kasukabe Farmland",
           content="Well. This is not where I expected to find you.",
           is_public=1, tick=186, created_at="2026-09-10T07:04:09Z")
    for business_id in ("b_grocery", "b_pharmacy", "b_construction"):
        insert("businesses", id=business_id, name=business_id, business_type="shop",
               owner_agent_id="a0040")
    for i in range(100):
        insert("llm_calls", id=f"ok{i}", agent_id="a0040", payer_agent_id="a0040",
               model="moonshotai.kimi-k2.5", requested_model="moonshotai.kimi-k2.5",
               call_class="agent_turn", purpose="auto:agent_turn", request_body="{}",
               input_tokens=100, output_tokens=20, cost_nano_dollars=1_000_000,
               cents_debited=0, latency_ms=900, rate_limit_wait_ms=0, success=1,
               created_at="2026-09-10T06:45:00Z", tick=40)
    for i in range(cap_refusals):
        insert("llm_calls", id=f"cap{i}", agent_id="a0043", payer_agent_id="a0043",
               model="moonshotai.kimi-k2.5", requested_model="moonshotai.kimi-k2.5",
               call_class="agent_turn", purpose="auto:agent_turn", request_body="{}",
               input_tokens=0, output_tokens=0, cost_nano_dollars=0, cents_debited=0,
               latency_ms=0, rate_limit_wait_ms=0, success=0,
               error_type="SpendCapExceededError", failed_at="broker-internal",
               created_at="2026-09-10T07:04:17Z", tick=186)
    conn.commit()
    return str(path)


@pytest.fixture
def world_is_gone(monkeypatch, tmp_path):
    """No world answers, and the environment points at the WRONG run's homes -
    exactly the reporter run 26 had."""
    monkeypatch.setattr(rr, "get", lambda base, path: None)
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(tmp_path / "run-25-homes"))
    rr.RUN_STARTED_AT = 0.0
    rr.HOME_ROOT = ""
    yield
    rr.RUN_STARTED_AT = 0.0
    rr.HOME_ROOT = ""


def _report(tmp_path: Path) -> str:
    return rr.build("http://127.0.0.1:8100", _store(tmp_path))


def _run_table(text: str) -> str:
    return text.split("## The run", 1)[-1].split("## ", 1)[0]


def test_who_acted_is_this_runs_cast_and_this_runs_calls(tmp_path, world_is_gone):
    table = _run_table(_report(tmp_path))

    assert "| Residents who acted | **2 of 3** (67%) |" in table, table
    assert "Farid Ravensworth" in table, "the resident who did nothing this run is not named"
    assert "n01" not in table and "a0176" not in table, "another run's homes leaked in"


def test_the_live_rows_are_read_from_the_trace(tmp_path, world_is_gone):
    table = _run_table(_report(tmp_path))

    assert "| Residents | **3** |" in table, table
    assert "| Tick reached | **186** (game day 1) |" in table, table
    assert "| Households / businesses | 2 / 3 |" in table, table


def test_what_the_trace_cannot_answer_is_said_not_guessed(tmp_path, world_is_gone):
    """Dormancy and death are not recorded after shutdown - `agent.died` is
    not a recorded event type and the trace agents table is never updated
    for either. "not recorded" is honest; "-" read as zero is not."""
    table = _run_table(_report(tmp_path))

    assert "| Alive / dormant / dead | not recorded / not recorded / not recorded |" in table


def test_what_was_invented_learned_and_said_survives(tmp_path, world_is_gone):
    text = _report(tmp_path)

    assert "Nothing was authored" not in text
    assert "1 skill(s) written, 1 tool(s) invented" in text
    assert "harvest_maintain" in text
    # Scoped to their own sections: the report's closing note quotes both
    # phrases as history, and a whole-document check reads the quotation.
    learning = text.split("## Learning by doing", 1)[1].split("## ", 1)[0]
    conversation = text.split("## The conversation", 1)[1].split("## What the town could not do", 1)[0]
    assert "No agent earned proficiency" not in learning
    assert "farming" in learning
    assert "Nobody spoke" not in conversation
    assert "not where I expected to find you" in conversation


def test_a_spend_cap_refusal_is_not_a_broken_link(tmp_path, world_is_gone):
    text = _report(tmp_path)
    table = _run_table(text)

    assert "| Dispatch success | **100.0%** |" in table, table
    assert "Refused by the spend cap | 26" in table
    assert "broker-internal" not in text.split("## What the town invented", 1)[0]


def test_refusals_are_said_to_be_unrecorded_not_absent(tmp_path, world_is_gone):
    text = _report(tmp_path)

    assert "No refused intentions" not in text
    assert "not recorded here" in text


def test_the_header_does_not_claim_a_running_world(tmp_path, world_is_gone):
    text = _report(tmp_path)

    assert "Every figure is read from the running world" not in text
    assert "trace store" in text.split("## The run", 1)[0]


def test_the_run_is_scoped_by_its_own_manifest(tmp_path):
    db = _store(tmp_path)

    assert rr._run_started_at(SimpleNamespace(db=db, world_db=None)) == pytest.approx(1789022402.5)


def test_the_home_root_comes_from_the_manifest_when_nobody_gives_one(tmp_path, monkeypatch):
    monkeypatch.delenv("KASUKABE_HOME_ROOT", raising=False)
    monkeypatch.setattr(rr, "get", lambda base, path: None)
    db = _store(tmp_path)

    rr.main(["run_report", "--db", db, "-o", str(tmp_path / "report.md")])

    try:
        assert rr.engine_home_root() == HOME_ROOT
    finally:
        rr.HOME_ROOT = ""
        rr.RUN_STARTED_AT = 0.0


def test_an_explicit_home_root_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "get", lambda base, path: None)
    db = _store(tmp_path)

    rr.main(["run_report", "--db", db, "--home-root", "/elsewhere", "-o", str(tmp_path / "r.md")])

    try:
        assert rr.engine_home_root() == "/elsewhere"
    finally:
        rr.HOME_ROOT = ""
        rr.RUN_STARTED_AT = 0.0


def test_a_live_world_is_still_read_live(tmp_path, monkeypatch):
    """The fallback is for a world that has stopped; a live run is unchanged."""
    live_state = {"population": 20, "alive": 19, "dormant": 1, "dead": 0,
                  "tick": 90, "game_day": 0, "households": 4, "businesses": 4}
    monkeypatch.setattr(rr, "get", lambda base, path: dict(live_state)
                        if path.endswith("state") else {})
    rr.RUN_STARTED_AT = 0.0

    table = _run_table(rr.build("http://127.0.0.1:8100", _store(tmp_path)))

    assert "| Residents | **20** |" in table
    assert "| Alive / dormant / dead | 19 / 1 / 0 |" in table
