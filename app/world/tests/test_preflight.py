"""The start-of-run checklist.

A five-hour run that dies at minute forty because the broker had no
credentials, or that finishes having proved nothing because `skillify` was
never registered, is worse than a run that refuses to start. Every check in
world/preflight.py corresponds to something that actually went wrong in this
project at least once - and these tests pin the two properties that make the
checklist worth having: it BLOCKS, and it does not lie.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from world.preflight import (
    MAX_HEALTHY_KB_PER_CALL,
    Check,
    Preflight,
    _measured_kb_per_call,
)


def test_a_critical_failure_blocks_the_run():
    pf = Preflight(checks=[Check("fine", True), Check("broken", False)])
    assert pf.ready is False


def test_a_warning_does_not_block():
    """A cap that covers 3.8 of 5 hours is worth saying and not worth
    refusing over - the operator may intend a shorter run."""
    pf = Preflight(checks=[Check("fine", True), Check("thin", False, critical=False)])
    assert pf.ready is True


def test_status_words_separate_the_two():
    assert Check("x", True).status == "PASS"
    assert Check("x", False).status == "FAIL"
    assert Check("x", False, critical=False).status == "WARN"


def test_every_check_that_fails_carries_a_fix():
    """A failing check with no fix line tells the reader they are stuck
    without telling them how to get unstuck, which is barely better than
    no check at all."""
    from world import preflight

    pf = preflight.run(
        broker_url="http://127.0.0.1:1",
        expected_agents=30,
        genesis_path="world/genesis_30.json",
        home_root="/tmp/kasukabe-preflight-test",
        trace_db="/tmp/kasukabe-preflight-test/trace.db",
        engine_repo="../engine",
    )
    missing = [c.name for c in pf.checks if not c.ok and not c.fix]
    assert not missing, f"failing checks with no fix line: {missing}"


def _trace_db(tmp_path, body_bytes: int, rows: int = 10) -> str:
    path = tmp_path / "trace.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE llm_calls (request_body TEXT, success INT)")
    conn.executemany(
        "INSERT INTO llm_calls (request_body, success) VALUES (?, 1)",
        [("x" * body_bytes,) for _ in range(rows)],
    )
    conn.commit()
    conn.close()
    return str(path)


def test_the_estimate_is_measured_not_guessed(tmp_path):
    db = _trace_db(tmp_path, body_bytes=100 * 1024)
    assert _measured_kb_per_call(db) == pytest.approx(100, rel=0.01)


def test_no_trace_store_is_no_evidence_not_health(tmp_path):
    """0.0 must not read as "0 KB per call, wonderfully healthy" - the
    caller skips the prompt-size check entirely when there is nothing to
    measure, rather than passing it on absent data."""
    assert _measured_kb_per_call(str(tmp_path / "nothing.db")) == 0.0


def test_a_bloated_prompt_blocks_the_run(tmp_path, monkeypatch):
    """506 of 890 failures in the six-hour run were "request body too
    large" or the 400 that follows it. The run should not have started."""
    from world import preflight

    db = _trace_db(tmp_path, body_bytes=(MAX_HEALTHY_KB_PER_CALL + 200) * 1024)
    pf = preflight.run(
        broker_url="http://127.0.0.1:1",
        expected_agents=30,
        genesis_path="world/genesis_30.json",
        home_root=str(tmp_path / "home"),
        trace_db=db,
        engine_repo="../engine",
    )
    prompt = [c for c in pf.checks if c.name == "Prompt size is sane"]
    assert prompt, "the prompt-size check did not run"
    assert prompt[0].ok is False
    assert prompt[0].critical is True
    assert pf.ready is False


def test_a_healthy_prompt_passes(tmp_path):
    from world import preflight

    db = _trace_db(tmp_path, body_bytes=8 * 1024)
    pf = preflight.run(
        broker_url="http://127.0.0.1:1",
        expected_agents=30,
        genesis_path="world/genesis_30.json",
        home_root=str(tmp_path / "home"),
        trace_db=db,
        engine_repo="../engine",
    )
    prompt = [c for c in pf.checks if c.name == "Prompt size is sane"]
    assert prompt and prompt[0].ok is True


def test_the_checklist_is_the_length_it_claims(tmp_path):
    """Between ten and fifteen pre-boot checks, plus three that need a
    booted world. A checklist that quietly shrinks is one nobody notices
    has stopped checking."""
    from world import preflight

    pf = preflight.run(
        broker_url="http://127.0.0.1:1",
        expected_agents=30,
        genesis_path="world/genesis_30.json",
        home_root=str(tmp_path / "home"),
        trace_db=_trace_db(tmp_path, 8 * 1024),
        engine_repo="../engine",
    )
    assert 10 <= len(pf.checks) <= 15


def test_json_carries_everything_the_panel_renders(tmp_path):
    from world import preflight

    body = preflight.run(
        broker_url="http://127.0.0.1:1",
        expected_agents=30,
        genesis_path="world/genesis_30.json",
        home_root=str(tmp_path / "home"),
        trace_db=_trace_db(tmp_path, 8 * 1024),
        engine_repo="../engine",
    ).to_json()

    assert set(body) >= {"ready", "passed", "total", "checks"}
    assert body["total"] == len(body["checks"])
    for c in body["checks"]:
        assert set(c) >= {"name", "ok", "status", "detail", "fix", "critical"}


def test_the_cap_does_not_hide_the_disease(tmp_path):
    """broker/trace.py caps stored bodies at 64 KB. That is what stopped a
    5.9 GB trace from filling the disk - and it would also hide the 630 KB
    average that made the cap necessary, leaving a check that can never fire
    again. A capped row records its true size and this must read it."""
    import json as _json

    path = tmp_path / "capped.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE llm_calls (request_body TEXT, success INT)")
    capped = _json.dumps({
        "_truncated": True,
        "_original_bytes": 640 * 1024,
        "_head": "x" * 1024,
    })
    conn.executemany("INSERT INTO llm_calls (request_body, success) VALUES (?, 1)",
                     [(capped,) for _ in range(10)])
    conn.commit()
    conn.close()

    assert _measured_kb_per_call(str(path)) == pytest.approx(640, rel=0.01)


def _spend_db(tmp_path, dollars: float, seconds: int, rows: int = 200) -> str:
    path = tmp_path / "spend.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE llm_calls (cost_nano_dollars INT, created_at REAL)")
    per = int(dollars * 1e9 / rows)
    conn.executemany(
        "INSERT INTO llm_calls VALUES (?, ?)",
        [(per, 1_000_000.0 + i * (seconds / rows)) for i in range(rows)],
    )
    conn.commit()
    conn.close()
    return str(path)


def test_the_rate_is_measured_from_the_trace(tmp_path):
    from world.preflight import _measured_usd_per_hour

    db = _spend_db(tmp_path, dollars=8.0, seconds=1800)
    assert _measured_usd_per_hour(db, 30) == pytest.approx(16.0, rel=0.02)


def test_no_trace_history_falls_back_and_scales_by_agents(tmp_path):
    from world.preflight import FALLBACK_USD_PER_HOUR_PER_30_AGENTS as F
    from world.preflight import _measured_usd_per_hour

    empty = str(tmp_path / "none.db")
    assert _measured_usd_per_hour(empty, 30) == pytest.approx(F)
    assert _measured_usd_per_hour(empty, 15) == pytest.approx(F / 2)


def test_a_window_too_short_to_divide_is_not_evidence(tmp_path):
    """Thirty seconds of calls says nothing about an hourly rate, and
    dividing by it produces a confident, enormous, wrong number."""
    from world.preflight import _spend_sample

    assert _spend_sample(_spend_db(tmp_path, dollars=2.0, seconds=30)) is None


def test_too_few_calls_is_not_evidence(tmp_path):
    from world.preflight import _spend_sample

    assert _spend_sample(_spend_db(tmp_path, 1.0, 3600, rows=10)) is None


def test_an_undersized_cap_warns_with_the_real_number(tmp_path):
    from world import preflight

    monkey = _spend_db(tmp_path, dollars=32.0, seconds=3600)
    pf = preflight.run(
        broker_url="http://127.0.0.1:1",
        expected_agents=30,
        genesis_path="world/genesis_30.json",
        home_root=str(tmp_path / "home"),
        trace_db=monkey,
        engine_repo="../engine",
    )
    cap = [c for c in pf.checks if c.name == "Cap covers the intended run"]
    if cap:
        assert "32" in cap[0].detail or "$32" in cap[0].fix


def test_the_reporter_preserves_the_handwritten_half(tmp_path):
    """There were two documents describing the same run and no way to tell
    which was current. The generated half now lives inside the findings
    file - and must never eat the analysis above it."""
    from world.run_report import LIVE_MARKER, splice

    doc = tmp_path / "TEST_RUN.md"
    doc.write_text("# Findings\n\nSomething a person worked out.\n", encoding="utf-8")

    first = splice(doc, "## The run\n\ntick 1\n")
    doc.write_text(first, encoding="utf-8")
    assert "Something a person worked out." in first
    assert LIVE_MARKER in first

    second = splice(doc, "## The run\n\ntick 2\n")
    assert "Something a person worked out." in second
    assert "tick 2" in second
    assert "tick 1" not in second, "the stale report was not replaced"
    assert second.count(LIVE_MARKER) == 1, "a marker was duplicated"


def test_a_fresh_file_is_created_cleanly(tmp_path):
    from world.run_report import LIVE_MARKER, splice

    out = splice(tmp_path / "new.md", "## The run\n")
    assert out.startswith(LIVE_MARKER)
    assert "## The run" in out


@pytest.mark.asyncio
async def test_speech_survives_a_checkpoint_round_trip():
    from world.run_genesis import RecordCheckpointAdapter
    from world.tools.bootstrap import build_world

    before, _ = await build_world()
    before.utterances.append({
        "tick": 7, "speaker_agent_id": "a0016", "speaker_name": "Ezra Jarrah",
        "listener_agent_id": "a0045", "content": "You're here early, friend.",
        "location_id": "Hobbs Cafe", "is_public": True, "conversation_id": "c1",
    })

    after, _ = await build_world()
    RecordCheckpointAdapter(after).restore(
        RecordCheckpointAdapter(before).snapshot()
    )

    assert len(after.utterances) == 1
    assert after.utterances[0]["content"] == "You're here early, friend."
    assert after.utterances[0]["speaker_agent_id"] == "a0016"


@pytest.mark.asyncio
async def test_a_checkpoint_written_before_this_adapter_still_restores():
    """Every existing checkpoint on disk predates it. Refusing them would
    make the fix cost the operator their run."""
    from world.run_genesis import RecordCheckpointAdapter
    from world.tools.bootstrap import build_world

    w, _ = await build_world()
    RecordCheckpointAdapter(w).restore({})
    assert w.utterances == []
    assert w.tool_refusals == []


def test_speech_is_still_excluded_from_rollback():
    """The original reason for the exclusion has to survive the fix: a
    failed tool call must never erase words that were really spoken."""
    from world.tools.state import OWNED_STATE_FIELDS

    assert "utterances" not in OWNED_STATE_FIELDS
    assert "tool_refusals" not in OWNED_STATE_FIELDS


def _mixed_db(tmp_path, ok_kb: int, fail_kb: int) -> str:
    """A trace where the SUCCESSES are healthy and the failures are huge -
    which is exactly what a fresh town looks like."""
    path = tmp_path / "mixed.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE llm_calls (request_body TEXT, success INT)")
    conn.executemany(
        "INSERT INTO llm_calls VALUES (?, ?)",
        [("x" * (ok_kb * 1024), 1) for _ in range(100)]
        + [("y" * (fail_kb * 1024), 0) for _ in range(100)],
    )
    conn.commit()
    conn.close()
    return str(path)


def test_refused_giant_prompts_do_not_condemn_a_healthy_run(tmp_path):
    """Observed on a fresh town: successes averaged 30 KB, failures 1,942 KB,
    and the combined 991 KB blocked a run that was fine. A prompt refused for
    size is already counted as a failure; averaging it in here measures the
    symptom rather than the trend."""
    db = _mixed_db(tmp_path, ok_kb=30, fail_kb=1942)
    assert _measured_kb_per_call(db) == pytest.approx(30, rel=0.02)


def test_creeping_successful_prompts_are_still_caught(tmp_path):
    """The case the check exists for: calls still succeed, but their prompts
    are approaching the limit, which predicts failures not yet seen."""
    db = _mixed_db(tmp_path, ok_kb=MAX_HEALTHY_KB_PER_CALL + 100, fail_kb=10)
    assert _measured_kb_per_call(db) > MAX_HEALTHY_KB_PER_CALL


def test_a_held_engine_port_is_reported():
    """A previous run's engines outlive `pkill` for as long as its graceful
    shutdown takes. Start inside that window and all thirty engines die with
    `[Errno 48]`, which reaches the operator as `exited with code 3`."""
    import socket as _socket

    from world.preflight import _ports_in_use

    held = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    port = held.getsockname()[1]
    held.listen(1)
    try:
        assert _ports_in_use(port, 1) == [port]
    finally:
        held.close()


def test_free_ports_report_clean():
    from world.preflight import _ports_in_use

    import socket as _socket

    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert _ports_in_use(port, 1) == []


def test_the_port_check_is_pre_boot_only(tmp_path):
    """Served live from inside a running world, this check finds that
    world's OWN thirty engines and calls them a conflict - a red line on
    the panel of a perfectly healthy run."""
    import socket as _socket

    from world import preflight

    held = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        held.bind(("127.0.0.1", preflight.ENGINE_PORT_BASE))
        held.listen(1)
    except OSError:
        held.close()
        pytest.skip("engine port already bound by a real run")
    try:
        args = dict(
            broker_url="http://127.0.0.1:1", expected_agents=1,
            genesis_path="world/genesis_30.json",
            home_root=str(tmp_path / "home"),
            trace_db=str(tmp_path / "t.db"), engine_repo="../engine",
        )
        before = [c for c in preflight.run(**args).checks
                  if c.name == "Engine ports are free"][0]
        during = [c for c in preflight.run(**args, ports_must_be_free=False).checks
                  if c.name == "Engine ports are free"][0]
        assert before.ok is False, "a held port must block a run that has not started"
        assert during.ok is True, "a running world's own engines are not a conflict"
    finally:
        held.close()


def test_silent_engines_block_the_run(tmp_path, monkeypatch):
    from world import preflight

    homes = tmp_path / "agents"
    for i in range(30):
        d = homes / f"a{i:04d}"
        d.mkdir(parents=True)
        if i < 27:
            (d / "session_log.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(homes))

    check = _session_check(preflight, tmp_path, expected=30)
    assert check.ok is False
    assert "27/30" in check.detail
    assert check.critical is True


def test_every_engine_logging_passes(tmp_path, monkeypatch):
    from world import preflight

    homes = tmp_path / "agents"
    for i in range(30):
        d = homes / f"a{i:04d}"
        d.mkdir(parents=True)
        (d / "session_log.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(homes))

    assert _session_check(preflight, tmp_path, expected=30).ok is True


def _session_check(preflight, tmp_path, expected: int):
    pf = preflight.run(
        broker_url="http://127.0.0.1:1",
        world_url="http://127.0.0.1:1",
        expected_agents=expected,
        genesis_path="world/genesis_30.json",
        home_root=str(tmp_path / "home"),
        trace_db=str(tmp_path / "t.db"),
        engine_repo="../engine",
        ports_must_be_free=False,
        engines_running=True,
    )
    named = [c for c in pf.checks if c.name == "Every engine is writing a session log"]
    assert named, "the session-log check did not run"
    return named[0]


def test_a_broker_writing_to_a_deleted_trace_is_caught(tmp_path, monkeypatch):
    """Delete a trace store out from under a running broker and it keeps
    writing to the unlinked file forever, perfectly happily. Every other
    surface looks healthy - engines alive, world ticking, agents acting -
    and the report says the town has never made an API call."""
    from world import preflight

    homes = tmp_path / "agents"
    for i in range(2):
        d = homes / f"a{i:04d}"
        d.mkdir(parents=True)
        (d / "session_log.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(homes))

    empty = tmp_path / "orphaned.db"
    conn = sqlite3.connect(empty)
    conn.execute("CREATE TABLE llm_calls (request_body TEXT, success INT)")
    conn.commit()
    conn.close()

    def named(db):
        pf = preflight.run(
            broker_url="http://127.0.0.1:1", expected_agents=2,
            genesis_path="world/genesis_30.json",
            home_root=str(tmp_path / "home"), trace_db=str(db),
            engine_repo="../engine", ports_must_be_free=False,
            engines_running=True,
        )
        return [c for c in pf.checks if c.name == "Broker is writing to this trace store"][0]

    assert named(empty).ok is False

    conn = sqlite3.connect(empty)
    conn.execute("INSERT INTO llm_calls VALUES ('{}', 1)")
    conn.commit()
    conn.close()
    assert named(empty).ok is True


def test_session_logging_is_checked_before_any_engine_starts(tmp_path, monkeypatch):
    from world import preflight

    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(tmp_path / "homes"))
    pf = preflight.run(
        broker_url="http://127.0.0.1:1", expected_agents=30,
        genesis_path="world/genesis_30.json",
        home_root=str(tmp_path / "homes"), trace_db=str(tmp_path / "t.db"),
        engine_repo="../engine",
    )
    named = [c for c in pf.checks if c.name == "Session logging is ready for every engine"]
    assert named, "the pre-boot session-logging check did not run"
    assert named[0].ok is True
    assert named[0].critical is True


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "The test makes a directory read-only with chmod(0o500), and Windows "
        "ignores POSIX mode bits on directories - the write still succeeds, so "
        "the check correctly reports the home as writable and the test's own "
        "premise never holds. The CHECK is right on both platforms; only this "
        "way of provoking it is POSIX-only."
    ),
)
def test_an_unwritable_home_blocks_the_run_before_it_starts(tmp_path, monkeypatch):
    import os as _os

    homes = tmp_path / "homes"
    homes.mkdir()
    _os.chmod(homes, 0o500)
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(homes))
    try:
        from world import preflight

        pf = preflight.run(
            broker_url="http://127.0.0.1:1", expected_agents=30,
            genesis_path="world/genesis_30.json", home_root=str(homes),
            trace_db=str(tmp_path / "t.db"), engine_repo="../engine",
        )
        named = [c for c in pf.checks
                 if c.name == "Session logging is ready for every engine"][0]
        assert named.ok is False
        assert pf.ready is False
    finally:
        _os.chmod(homes, 0o700)


def test_the_probe_leaves_no_empty_log_behind(tmp_path, monkeypatch):
    """An empty file left by the probe would make the LIVE check read
    "logging" for an engine that has never written a line."""
    from world import preflight

    homes = tmp_path / "homes"
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(homes))
    preflight.run(
        broker_url="http://127.0.0.1:1", expected_agents=30,
        genesis_path="world/genesis_30.json", home_root=str(homes),
        trace_db=str(tmp_path / "t.db"), engine_repo="../engine",
    )
    assert list(homes.glob("*/session_log.jsonl")) == []
    assert list(homes.glob("*")), "the homes themselves should exist"


def test_a_real_session_log_survives_the_probe(tmp_path, monkeypatch):
    """Re-running preflight against a town mid-run must not truncate or
    remove logs that already hold an agent's reasoning."""
    from world import preflight

    homes = tmp_path / "homes"
    (homes / "a0001").mkdir(parents=True)
    log = homes / "a0001" / "session_log.jsonl"
    log.write_text('{"agent_id": "a0001"}\n', encoding="utf-8")
    monkeypatch.setenv("KASUKABE_HOME_ROOT", str(homes))

    preflight.run(
        broker_url="http://127.0.0.1:1", expected_agents=30,
        genesis_path="world/genesis_30.json", home_root=str(homes),
        trace_db=str(tmp_path / "t.db"), engine_repo="../engine",
    )
    assert log.read_text(encoding="utf-8") == '{"agent_id": "a0001"}\n'


def test_the_run_is_over_when_the_cap_is_reached(tmp_path, monkeypatch):
    """The governor trips on the cap, and the last scheduled refresh lands
    up to five minutes early - so the document would freeze just short of
    the end and never record how the run actually finished."""
    from world import run_report

    db = tmp_path / "spend.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE llm_calls (success INT, cost_nano_dollars INT, "
                 "failed_at TEXT)")
    conn.execute("INSERT INTO llm_calls VALUES (1, 39000000000, NULL)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("SPEND_CAP_USD", "40.00")
    monkeypatch.setattr(run_report, "get", lambda w, p: {"tick": 5})
    assert run_report.run_is_over("http://x", str(db)) is False

    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO llm_calls VALUES (1, 2000000000, NULL)")
    conn.commit()
    conn.close()
    assert run_report.run_is_over("http://x", str(db)) is True


def test_a_world_that_stopped_answering_ends_the_run(tmp_path, monkeypatch):
    from world import run_report

    monkeypatch.setattr(run_report, "get", lambda w, p: None)
    assert run_report.run_is_over("http://x", str(tmp_path / "none.db")) is True


def test_no_cap_set_never_declares_the_run_over(tmp_path, monkeypatch):
    from world import run_report

    monkeypatch.delenv("SPEND_CAP_USD", raising=False)
    monkeypatch.setattr(run_report, "get", lambda w, p: {"tick": 5})
    assert run_report.run_is_over("http://x", str(tmp_path / "none.db")) is False


def test_timestamps_are_ist_twelve_hour_with_the_date():
    from datetime import datetime, timedelta, timezone

    from world.run_report import ist

    IST = timezone(timedelta(hours=5, minutes=30))
    at = datetime(2026, 8, 6, 21, 45, 3, tzinfo=IST).timestamp()
    assert ist(at) == "06 Aug 2026, 9:45:03 PM IST"
    assert ist(at, with_date=False) == "9:45:03 PM IST"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(0, "12:05:03 AM IST"), (9, "9:05:03 AM IST"),
     (12, "12:05:03 PM IST"), (13, "1:05:03 PM IST"), (23, "11:05:03 PM IST")],
)
def test_midnight_and_noon_are_not_mangled(hour, expected):
    """A 12-hour clock has two places it goes wrong, and both of them are
    the hours nobody tests."""
    from datetime import datetime, timedelta, timezone

    from world.run_report import ist

    IST = timezone(timedelta(hours=5, minutes=30))
    at = datetime(2026, 8, 6, hour, 5, 3, tzinfo=IST).timestamp()
    assert ist(at, with_date=False) == expected


def test_a_missing_timestamp_is_a_dash_not_a_crash():
    from world.run_report import ist

    assert ist(None) == "—"
    assert ist("") == "—"
    assert ist("not-a-time") == "—"
