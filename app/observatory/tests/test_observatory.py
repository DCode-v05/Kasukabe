"""The observatory, including every way it is allowed to be unhelpful.

A monitoring service that 500s when the thing it monitors is unhealthy is
exactly backwards, so most of these tests are about the degraded paths: no
trace file, no world, a database older than the column being read. Each must
produce a page that says what is wrong, not an error.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from observatory import gaps, health, hops
from observatory.app import create_app

SCHEMA_WITH_HOP = """
create table llm_calls (
    id text primary key, agent_id text, success integer,
    cost_nano_dollars integer, error_type text, error_message text, failed_at text
);
"""
SCHEMA_WITHOUT_HOP = """
create table llm_calls (
    id text primary key, agent_id text, success integer,
    cost_nano_dollars integer, error_type text, error_message text
);
"""


def make_db(path, schema=SCHEMA_WITH_HOP, rows=()):
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    for row in rows:
        conn.execute(
            f"insert into llm_calls values ({','.join('?' * len(row))})", row
        )
    conn.commit()
    conn.close()
    return str(path)


# ── health ────────────────────────────────────────────────────────────


def test_a_healthy_run_reads_as_believable(tmp_path):
    db = make_db(tmp_path / "t.db", rows=[
        (f"c{i}", "a1", 1, 1000, None, None, None) for i in range(95)
    ] + [(f"f{i}", "a1", 0, 0, "UpstreamError", "boom", "bedrock") for i in range(5)])
    conn = sqlite3.connect(db)
    snapshot = health.read_health(conn)
    assert snapshot.dispatch_success == 0.95
    assert snapshot.believable is True


def test_a_broken_run_says_so_before_anything_else(tmp_path):
    """39% was the real number from a live run, and it was a stale process
    holding port 8100 - not a finding about society. The page has to say that
    itself rather than let a reader draw conclusions from it."""
    db = make_db(tmp_path / "t.db", rows=[
        (f"c{i}", "a1", 1, 1000, None, None, None) for i in range(39)
    ] + [(f"f{i}", "a1", 0, 0, "UpstreamError", "boom", "ledger") for i in range(61)])
    conn = sqlite3.connect(db)
    snapshot = health.read_health(conn)

    assert snapshot.believable is False
    assert "NOT believable" in snapshot.verdict
    assert "Fix the instrument" in snapshot.verdict


def test_spend_comes_from_nano_dollars_not_rounded_cents(tmp_path):
    """A run of cheap calls would report $0.00 off cents_debited while really
    having spent dollars."""
    db = make_db(tmp_path / "t.db", rows=[
        (f"c{i}", "a1", 1, 4_000_000, None, None, None) for i in range(500)
    ])
    conn = sqlite3.connect(db)
    assert health.read_health(conn).spend_usd == pytest.approx(2.0)


def test_no_calls_is_not_a_failing_run(tmp_path):
    db = make_db(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    snapshot = health.read_health(conn)
    assert snapshot.dispatch_success == 0.0
    assert "nothing to judge" in snapshot.verdict


# ── hops ──────────────────────────────────────────────────────────────


def test_failures_group_by_the_link_that_broke(tmp_path):
    db = make_db(tmp_path / "t.db", rows=[
        ("a", "a1", 0, 0, "LedgerUnavailableError", "ledger down", "ledger"),
        ("b", "a2", 0, 0, "LedgerUnavailableError", "ledger down", "ledger"),
        ("c", "a1", 0, 0, "UpstreamError", "throttled", "bedrock"),
        ("d", "a1", 1, 100, None, None, None),
    ])
    conn = sqlite3.connect(db)
    rows = {h.hop: h for h in hops.failures_by_hop(conn)}

    assert rows["ledger"].failures == 2
    assert rows["ledger"].agents == 2
    assert rows["bedrock"].failures == 1
    assert "ledger" in rows and set(rows) == {"ledger", "bedrock"}, "successes excluded"


def test_untagged_failures_are_reported_not_hidden(tmp_path):
    """A page showing '0 bedrock failures' off rows that cannot record the
    answer is confidently wrong, and the reader cannot tell."""
    db = make_db(tmp_path / "t.db", rows=[
        ("a", "a1", 0, 0, "X", "old", None),
        ("b", "a1", 0, 0, "X", "new", "bedrock"),
    ])
    conn = sqlite3.connect(db)
    assert hops.untagged_share(conn) == 0.5


# ── gaps ──────────────────────────────────────────────────────────────


def test_a_gap_gets_a_name_and_a_headcount():
    found = gaps.summarise([
        {"agent_id": "a1", "tool_name": "treat", "unmet_requirement": "actor:skill medicine", "detail": "needs medicine"},
        {"agent_id": "a2", "tool_name": "treat", "unmet_requirement": "actor:skill medicine", "detail": "needs medicine"},
        {"agent_id": "a3", "tool_name": "treat", "unmet_requirement": "actor:skill medicine", "detail": "needs medicine"},
    ])
    assert len(found) == 1
    assert found[0].agents == 3 and found[0].attempts == 3
    assert found[0].tools == ["treat"]


def test_a_missing_verb_outranks_a_busier_gap():
    """One agent hammering an impossible call 400 times is a stuck agent; a
    town reaching for a verb that does not exist is a thing to invent."""
    found = gaps.summarise(
        [{"agent_id": "a1", "tool_name": "pray", "unmet_requirement": "location:temple"}] * 400
        + [{"agent_id": f"a{i}", "tool_name": "repair", "unmet_requirement": "no-such-tool:repair"}
           for i in range(4)]
    )
    assert found[0].unmet_requirement == "no-such-tool:repair"
    assert found[0].is_missing_verb is True
    assert found[1].attempts == 400, "the busy one is still there, just not first"


def test_headcount_beats_attempt_count():
    found = gaps.summarise(
        [{"agent_id": "loop", "tool_name": "x", "unmet_requirement": "funds"}] * 200
        + [{"agent_id": f"a{i}", "tool_name": "y", "unmet_requirement": "location:mill"}
           for i in range(9)]
    )
    assert found[0].unmet_requirement == "location:mill", "9 agents beat 1 agent x200"


# ── the service, and its degraded paths ───────────────────────────────


def test_the_page_serves(tmp_path):
    client = TestClient(create_app(make_db(tmp_path / "t.db"), "http://127.0.0.1:59999"))
    assert client.get("/").status_code == 200
    assert client.get("/health").json()["service"] == "observatory"


def test_a_missing_trace_database_is_explained_not_a_500(tmp_path):
    client = TestClient(create_app(str(tmp_path / "nope.db"), "http://127.0.0.1:59999"))
    body = client.get("/api/health").json()
    assert body["available"] is False
    assert "no trace database" in body["why"]


def test_an_older_database_says_it_cannot_attribute_failures(tmp_path):
    """Rather than reporting every hop as zero, which would read as 'nothing
    is broken' off a table that cannot answer the question."""
    db = make_db(tmp_path / "old.db", schema=SCHEMA_WITHOUT_HOP,
                 rows=[("a", "a1", 0, 0, "X", "boom")])
    client = TestClient(create_app(db, "http://127.0.0.1:59999"))
    body = client.get("/api/hops").json()

    assert body["available"] is False
    assert "failed_at" in body["why"]
    assert body["hops"] == []


def test_an_unreachable_world_is_explained_not_a_500(tmp_path):
    client = TestClient(create_app(make_db(tmp_path / "t.db"), "http://127.0.0.1:59999"))
    body = client.get("/api/gaps").json()
    assert body["available"] is False
    assert "not answering" in body["why"]


def test_the_trace_database_is_opened_read_only(tmp_path):
    """Enforced by SQLite, not by our good behaviour: the observatory must not
    be able to edit the run it is observing, even by mistake."""
    from observatory.app import open_trace_db

    conn = open_trace_db(make_db(tmp_path / "t.db"))
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("insert into llm_calls values ('x','a',1,0,null,null,null)")
