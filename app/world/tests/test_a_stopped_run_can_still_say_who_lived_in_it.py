"""The 50-agent run's report could not say who lived in the town.

`teambox-50-100usd-2026-09-16`: 50 residents, 9,998 calls, $100.40, and
its report says "The world reported no roster", "Refusals ... are not
recorded here", "Authoring tools MISSING describe_primitives, skillify,
toolify" for a run in which three residents DID call `skillify`, and
"Quick Harvest ... held by 1" for a skill its own learning table shows in
two pairs of hands. Every one of those is the same defect: a final report
is written after the world stops, and the world is the only thing that
was ever asked.

Some of it the trace can answer, and `durable_view` already does. The
rest - the roster, the refused intentions, which tools were being served
- lives only in the running world's memory and dies with it, so it is
READ while the world still answers, once, on the way down, and written
beside the checkpoints. A reading taken a second before the stop is the
truth; "not recorded" is what the report says when nobody took one.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig

APP = Path(__file__).resolve().parents[2]
SCHEMA = APP / "supabase" / "sqlite_schema.sql"


@pytest_asyncio.fixture
async def booted(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    await runner.attach_persistence()
    runner.build_world_app()
    return runner, tmp_path


@pytest.mark.asyncio
async def test_a_run_reads_itself_once_before_it_stops(booted) -> None:
    runner, tmp_path = booted

    await runner.shutdown()

    reading = tmp_path / "checkpoints" / "final-reading.json"
    assert reading.is_file(), (
        "the world answered right up to the shutdown and nobody asked it "
        "anything, so the report has to say 'not recorded'"
    )
    body = json.loads(reading.read_text(encoding="utf-8"))
    assert body["readings"]["agents"], "the roster is the whole point"
    for route in ("state", "authored", "refusals", "registry"):
        assert route in body["readings"], route


@pytest.mark.asyncio
async def test_the_reading_says_when_it_was_taken(booted) -> None:
    """A reading from tick 3 of a run that reached tick 194 is a different
    claim from one taken at the end, and the reader must be able to tell."""
    runner, tmp_path = booted

    await runner.shutdown()

    body = json.loads((tmp_path / "checkpoints" / "final-reading.json").read_text())
    assert isinstance(body.get("tick"), int)
    assert body.get("captured_at")


@pytest.mark.asyncio
async def test_a_failed_reading_does_not_stop_the_shutdown(booted, monkeypatch) -> None:
    """Describing a run must never be able to break ending it."""
    runner, _ = booted
    import world.run_genesis as rg

    async def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(rg, "capture_final_reading", boom)
    await runner.shutdown()


def test_the_manifest_says_where_the_run_put_its_files(tmp_path) -> None:
    """The report finds the reading through the manifest, which is the one
    thing in the trace that knows where this run's files went."""
    from world.run_manifest import build_manifest

    manifest = build_manifest(
        run_id="r", config=GenesisRunnerConfig(
            mock_mode=True, checkpoint_dir=tmp_path / "cp",
            world_mode="embedded", world_port=0),
        app_root=APP, engine_root=None, started_at=1.0, environ={},
    )
    assert manifest["checkpoint_dir"] == str(tmp_path / "cp")


def _a_stopped_run(tmp_path, *, reading: dict | None) -> tuple[str, str]:
    """A trace from a finished run, and a checkpoint dir beside it."""
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for agent_id, slug, name, sex in (("n01", "ivo", "Ivo Pemberton", "male"),
                                      ("n02", "jovana", "Jovana Ravensworth", "female")):
        conn.execute(
            "insert into agents (id, slug, display_name, sex, birth_tick, created_tick) "
            "values (?, ?, ?, ?, 0, 0)", (agent_id, slug, name, sex))
    body = json.dumps({"name": "Quick Harvest", "steps": [{"kind": "tool", "ref": "harvest"}]})
    conn.execute(
        "insert into skills (agent_id, task_type, proficiency_level, repetitions_observed, "
        "skill_artifact_text, tick) values ('n01', 'Quick Harvest', 73, 32, ?, 80)", (body,))
    conn.execute(
        "insert into skills (agent_id, task_type, proficiency_level, repetitions_observed, "
        "taught_by_agent_id, tick) values ('n02', 'Quick Harvest', 52, 16, 'n01', 120)")
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    conn.execute(
        "insert into run_manifest (run_id, started_at, checkpoint_dir) values ('r', 1.0, ?)",
        (str(checkpoints),))
    conn.commit()
    conn.close()
    if reading is not None:
        (checkpoints / "final-reading.json").write_text(json.dumps(reading), encoding="utf-8")
    return str(db), str(checkpoints)


READING = {
    "captured_at": 1.0, "tick": 194,
    "readings": {
        "state": {"run_id": "r", "population": 2, "alive": 2, "dormant": 37, "dead": 0, "tick": 194},
        "agents": [{"agent_id": "n01", "display_name": "Ivo Pemberton", "location_id": "street"},
                   {"agent_id": "n02", "display_name": "Jovana Ravensworth", "location_id": "farm"}],
        "authored": {}, "vitals": {},
        "refusals": {"refusals": [{"agent_id": "n01", "tool": "world:hire_employee",
                                   "unmet_requirement": "no business to hire into", "tick": 90}]},
        "registry": [{"name": "skillify"}, {"name": "toolify"}, {"name": "describe_primitives"},
                     {"name": "revise_skill"}, {"name": "revise_tool"}, {"name": "quote_action_cost"}],
    },
}


def test_a_report_written_after_the_stop_still_names_the_residents(tmp_path) -> None:
    from world.run_report import build

    db, _ = _a_stopped_run(tmp_path, reading=READING)
    text = build("http://127.0.0.1:1", db, db)

    assert "The world reported no roster." not in text
    assert "Ivo Pemberton" in text and "Jovana Ravensworth" in text


def test_the_refusals_survive_the_stop(tmp_path) -> None:
    """"What the town could not do" is the section that says which verbs
    the world would not give anybody - and it was blank for every final
    report this project has ever written."""
    from world.run_report import build

    db, _ = _a_stopped_run(tmp_path, reading=READING)
    text = build("http://127.0.0.1:1", db, db)

    assert "no business to hire into" in text
    assert "not recorded here" not in text


def test_the_report_says_the_reading_is_a_reading(tmp_path) -> None:
    from world.run_report import build

    db, _ = _a_stopped_run(tmp_path, reading=READING)
    text = build("http://127.0.0.1:1", db, db)

    assert "final reading" in text.lower()


def test_authoring_tools_are_not_called_missing_when_nobody_asked(tmp_path) -> None:
    """The 50-agent report's instrument section says the town was served 0
    tools and was MISSING `skillify` - in a run where three residents
    called `skillify` and it worked. Nothing was missing; the world had
    stopped, and an unanswered question was printed as a failed check."""
    from world.run_report import build

    db, _ = _a_stopped_run(tmp_path, reading=None)
    text = build("http://127.0.0.1:1", db, db)

    assert "MISSING" not in text
    assert "not recorded" in text


def test_a_reading_that_was_taken_reports_the_tools_it_found(tmp_path) -> None:
    from world.run_report import build

    db, _ = _a_stopped_run(tmp_path, reading=READING)
    text = build("http://127.0.0.1:1", db, db)

    assert "MISSING" not in text
    assert "all authoring tools reachable" in text


def test_who_holds_a_skill_is_read_not_assumed(tmp_path) -> None:
    """`durable_view` recorded holders as `[the author]` - a guess, and in
    the 50-agent run a wrong one: `Quick Harvest` was taught to a second
    resident who walked it 16 times, and the report said "held by 1"."""
    from world.run_report import durable_view

    db, _ = _a_stopped_run(tmp_path, reading=None)
    view = durable_view(db)

    quick = [s for s in view["authored"]["skills"] if s["name"] == "Quick Harvest"]
    assert quick, view["authored"]["skills"]
    assert sorted(quick[0]["holders"]) == ["n01", "n02"]
