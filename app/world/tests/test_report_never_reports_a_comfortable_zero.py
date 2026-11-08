"""A run report must not print $0.00 for a run that spent real money.

Run 16 (20 residents, 31 Aug 2026) spent $4.01 against a $4.00 cap and
its report said:

    | Bedrock calls | **0** |
    | Dispatch success | **0.0%** |
    | Spend | **$0.00** |

Every one of those was read correctly from `llm_calls`, which was empty
because the broker had been left running across a `rm` of the store and
was writing into an unlinked inode. The reporter had no way to know
that - but it did have the rest of the same database in front of it,
showing 20 residents, 46 utterances, 574 transactions and 352 memories.

A town that talked, traded and remembered did not do it for free. Zero
calls beside that activity is not a measurement, it is a missing
measurement, and the two must never render the same way: "$0.00" reads
as a cheap run and gets filed as good news. That is the failure this
whole suite exists to prevent - the green dashboard over the silent
loss.

`world/run_report.py` is the artifact a human reads to decide whether a
run worked, so a plausible wrong number there is worth more than a
crash.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.run_report import MISSING_TRACE_MARKER, build, spend

WORLD_WITH_ACTIVITY = {
    "tick": 68,
    "game_day": 0,
    "game_hour": 8,
    "game_minute": 0,
    "population": 20,
    "alive": 20,
    "dormant": 0,
    "dead": 0,
    "households": 4,
    "businesses": 4,
    "buildings": 0,
    "crime_cases": 0,
    "run_id": "1788179628-9dce8bb2",
}


def _store(tmp_path: Path, *, utterances: int, calls: int) -> str:
    """A trace store shaped like run 16's: a busy town, and a call table
    that may or may not have been written to.

    Built from the real schema rather than a hand-written subset - a
    report test on an invented table shape proves the report reads the
    invention."""
    from supabase.config import SupabaseConfig
    from supabase.sqlite_client import SqliteClient

    path = tmp_path / "run.db"
    client = SqliteClient(SupabaseConfig(sqlite_path=str(path)))
    conn = client.conn

    def insert(table: str, **given: object) -> None:
        """Insert a row filling every NOT NULL column the live schema
        declares, so this fixture tracks the schema instead of a snapshot
        of it taken the day it was written."""
        columns = list(conn.execute(f"PRAGMA table_info({table})"))
        row: dict[str, object] = {}
        for _, name, kind, notnull, default, pk in columns:
            if name in given:
                row[name] = given[name]
            elif notnull and default is None:
                row[name] = 0 if kind in ("INTEGER", "REAL") else ""
        keys = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"insert into {table} ({keys}) values ({marks})", list(row.values()))

    # Residents first: utterances and calls carry foreign keys to them,
    # and a report test whose town has no inhabitants is not a test of
    # anything this report renders.
    for n in range(20):
        insert("agents", id=f"n{n:02d}", slug=f"n{n:02d}",
               display_name=f"Resident {n}", sex="female", usd_wallet_cents=600,
               sim_balance=3000)
    for i in range(utterances):
        insert("utterances", id=i + 1, speaker_agent_id=f"n{i % 20:02d}",
               conversation_id=f"conv{i // 3}", location="home:h_mill",
               content="we should mill the grain today", is_public=1,
               tick=40 + i, created_at="2026-08-31T18:05:00Z")
    for i in range(calls):
        insert("llm_calls", id=f"c{i}", agent_id=f"n{i % 20:02d}",
               payer_agent_id=f"n{i % 20:02d}", model="moonshotai.kimi-k2.5",
               requested_model="moonshotai.kimi-k2.5", call_class="agent_turn",
               purpose="auto:agent_turn", request_body="{}", input_tokens=100,
               output_tokens=20, cost_nano_dollars=20_000_000, cents_debited=2,
               latency_ms=900, rate_limit_wait_ms=0, success=1,
               created_at="2026-08-31T18:05:00Z", tick=40 + i)
    conn.commit()
    conn.close()
    return str(path)


def test_spend_reads_a_populated_store_correctly(tmp_path: Path) -> None:
    """The control: with calls present the numbers must be the real ones."""
    db = _store(tmp_path, utterances=46, calls=200)
    result = spend(db)
    assert result["calls"] == 200
    assert result["usd"] == pytest.approx(200 * 0.02)


def test_an_empty_call_table_is_reported_as_missing_not_as_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incident. A town with utterances and no calls has lost its
    instrumentation, and the report has to say which."""
    import world.run_report as rr

    monkeypatch.setattr(rr, "get", lambda base, path: dict(WORLD_WITH_ACTIVITY)
                        if path.endswith("state") else {})
    db = _store(tmp_path, utterances=46, calls=0)
    # An empty home root, so this reads THIS report's own text and not
    # whatever the last real run left in /tmp/kasukabe-agents. Run 21's
    # logs contain an agent's own tool result reading "$0.00 billed", which
    # is a resident being told what its turn cost - a true sentence in a
    # quoted transcript - and it failed an assertion about the money table.
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(tmp_path / "no-engines"))

    text = build("http://127.0.0.1:8100", db)

    # The money table specifically. Scanning the whole document catches
    # every "$0.00" a resident ever said out loud, which is not what this
    # is about.
    table = text.split("## The run", 1)[-1].split("##", 1)[0]
    assert "$0.00" not in table, (
        "a run whose town spoke 46 times is not a $0.00 run; printing the "
        "zero hides a lost trace store behind a plausible number"
    )
    assert MISSING_TRACE_MARKER in text, (
        "the report must name the missing measurement, not omit the row"
    )


def test_a_genuinely_idle_world_is_not_accused_of_losing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction, and the reason this cannot just be "warn
    whenever calls == 0": a world that booted and did nothing has an
    empty call table honestly."""
    import world.run_report as rr

    idle = dict(WORLD_WITH_ACTIVITY, tick=0, alive=20)
    monkeypatch.setattr(rr, "get", lambda base, path: dict(idle)
                        if path.endswith("state") else {})
    db = _store(tmp_path, utterances=0, calls=0)

    text = build("http://127.0.0.1:8100", db)
    assert MISSING_TRACE_MARKER not in text, (
        "an idle world has nothing to have lost"
    )
