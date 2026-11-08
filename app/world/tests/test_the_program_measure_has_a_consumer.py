"""PROG-03: the acts-per-decision figure reaches somebody who will read it.

Two failures this project has already shipped, and this file exists so
programmatic tool calling does not become the third of each.

**ENG-16 / WIRE-02 - the instrument with no consumer.** Counters were put
on the engine's link to the world and served on `GET /health`, and nothing
read them: the runner polled that endpoint once at startup and looked only
at the status code. The audit's own words are *"a fix that reaches nobody
is indistinguishable from no fix"*, and it took three consumers to close
it. PROG-01's parent link has the same exposure - a column nobody groups
by is a column that may as well be absent - so this asserts a real reader
exists at each of the three places somebody might look: the report a human
opens, the command an operator runs, and the metrics table two runs are
compared in.

**EVAL-01 - the reader keyed to names nothing writes.** `prices_section`
read `business_id` and `price_sim` from a payload carrying `holder_id` and
`ask_sim`, and printed `| 42 | None | bread | None |` over eleven real
posts while a type-level guard passed. So this does not check that a
function exists. It takes the dict `/world/tool_invocations` really
serves, hands it to the section function, and reads the output.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from world.evals.programs import main as programs_eval
from world.evals.programs import read as programs_read
from world.run_report import programs_section


def _served(records: list) -> list[dict]:
    """Exactly the shape `/world/tool_invocations` returns.

    Kept in one place and derived from the record objects, so a field
    renamed on the record and not on the route fails here rather than in
    a report three weeks later.
    """
    return [
        {
            "agent_id": record.actor_id,
            "tick": record.tick,
            "tool_name": record.tool_name,
            "program_id": record.program_id,
            "program_step": record.program_step,
        }
        for record in records
    ]


@pytest.mark.asyncio
async def test_the_report_renders_what_the_route_actually_serves():
    """The whole path in miniature, with real records.

    Built through the real registry rather than from invented rows: rows
    written by the test are rows that agree with the test, which is the
    trap `test_the_price_table_reads_a_real_price` was written about
    after the unit test and the code agreed with each other and both
    disagreed with the world.
    """
    from world.tools.bootstrap import build_world
    from world.tools.tests.conftest import spawn_agent

    world, registry = await build_world(genesis_tick=0, seed=99)
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    await registry.execute(
        world,
        "n01",
        "toolify",
        {
            "name": "morning_round",
            "description": "look, then count what is owed",
            "steps": [
                {"tool_name": "look_around"},
                {"tool_name": "check_debts"},
            ],
        },
    )
    world.tool_invocations.clear()
    await registry.execute(world, "n01", "morning_round", {})

    rendered = "".join(programs_section(_served(world.tool_invocations)))

    assert "No programs ran" not in rendered, (
        "a program ran and the section says none did - the reader is keyed "
        "to a field the route does not serve"
    )
    assert "None" not in rendered, (
        f"a column rendered as the string 'None':\n{rendered}"
    )
    assert "morning_round" in rendered, (
        "the widest program is reported without naming the tool, which "
        "means program_step 0 did not identify the wrapper"
    )
    assert "2 act(s)" in rendered and "2.0 acts per decision" in rendered


@pytest.mark.asyncio
async def test_a_run_with_no_programs_says_so_rather_than_nothing():
    """Silence and zero are different answers.

    A section that emitted nothing when nobody wrote a program would read
    as a section that was never called, which is how `noop_sink` swallowed
    politics for 23 runs without anybody noticing.
    """
    rendered = "".join(programs_section([
        {"agent_id": "n01", "tick": 1, "tool_name": "rest",
         "program_id": None, "program_step": None},
    ]))
    assert "No programs ran" in rendered
    assert "one model call bought one act" in rendered


def test_the_operator_command_exists_and_is_reachable():
    """`python -m world.evals programs` - the second consumer.

    `character.py` and `judges.py` sat with no `__main__` long enough for
    two documents to describe them as built (EVAL-09). A subcommand is
    cheap; a measure nobody can run is not a measure.
    """
    from world.evals.__main__ import main as evals_main

    with pytest.raises(SystemExit) as exited:
        evals_main(["programs", "--help"])
    assert exited.value.code == 0


def test_the_measure_records_itself_so_two_runs_can_be_compared(tmp_path):
    """The third consumer: `eval_metrics`, per EVAL-07.

    Every evaluation this project ran before that finding is gone except
    where somebody pasted a number into markdown, so a measure that only
    prints has already been the losing side of this argument once.

    Run against a real trace store with the real schema, and the rows are
    counted afterwards. A source-text check would pass on a file that
    names the metrics in a comment and inserts nothing, which is the
    guard-that-cannot-fail pattern this repository polices.
    """
    import sqlite3

    schema = Path(__file__).resolve().parents[2] / "supabase" / "sqlite_schema.sql"
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.executescript(schema.read_text(encoding="utf-8"))
    conn.executemany(
        "insert into tool_invocations "
        "(invocation_id, agent_id, tick, tool, namespace, program_id, program_step) "
        "values (?, ?, ?, ?, 'public', ?, ?)",
        [
            ("i1", "n01", 4, "morning_round", "program-aaa", 0),
            ("i2", "n01", 4, "look_around", "program-aaa", 1),
            ("i3", "n01", 4, "check_debts", "program-aaa", 2),
            ("i4", "n02", 5, "rest", None, None),
        ],
    )
    conn.commit()
    conn.close()

    assert programs_eval(str(db), str(db)) == 0

    conn = sqlite3.connect(db)
    recorded = {
        row[0]
        for row in conn.execute(
            "select metric_name from eval_metrics where metric_name like 'prog_%'"
        )
    }
    conn.close()
    assert recorded == {
        "prog_acts_per_decision",
        "prog_adoption_residents",
        "prog_ceilings_held",
        "prog_median_sim_writers",
    }, f"the measure printed numbers and stored {sorted(recorded)}"


def test_the_headline_figure_is_the_one_the_audit_asked_for(tmp_path):
    """Two acts on one decision reads as 2.0, not as 3 rows.

    The wrapper is a decision, not an act, and an off-by-one here is an
    off-by-one in the only number the whole feature is argued on.
    """
    import sqlite3

    schema = Path(__file__).resolve().parents[2] / "supabase" / "sqlite_schema.sql"
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.executescript(schema.read_text(encoding="utf-8"))
    conn.executemany(
        "insert into tool_invocations "
        "(invocation_id, agent_id, tick, tool, namespace, program_id, program_step) "
        "values (?, ?, ?, ?, 'public', ?, ?)",
        [
            ("j1", "n01", 4, "wrapper", "program-bbb", 0),
            ("j2", "n01", 4, "look_around", "program-bbb", 1),
            ("j3", "n01", 4, "check_debts", "program-bbb", 2),
        ],
    )
    conn.commit()
    found = programs_read(conn)
    conn.close()

    assert found.programs == 1
    assert found.acts == 2
    assert found.acts_per_decision == 2.0


@pytest.mark.asyncio
async def test_the_adoption_figure_comes_from_the_filing_itself():
    """P2 reads a stamp, and this is the stamp being made.

    `has_control_flow` decides what counts as a program, `toolify` writes
    its answer into `agent.tool.invented`, and the eval reads it. One
    decision in one place. The alternative - re-parsing the stored
    composition downstream - is two readers of the same tool that can
    disagree, which is EVAL-01 with the roles swapped.
    """
    from world.tools.bootstrap import build_world
    from world.tools.tests.conftest import spawn_agent

    world, registry = await build_world(genesis_tick=0, seed=7)
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    plain = await registry.execute(
        world, "n01", "toolify",
        {
            "name": "plain_round",
            "description": "two reads, no control flow",
            "steps": [{"tool_name": "look_around"}, {"tool_name": "check_debts"}],
        },
    )
    guarded = await registry.execute(
        world, "n01", "toolify",
        {
            "name": "guarded_round",
            "description": "a read, then a rest only if nothing is owed",
            "steps": [
                {"tool_name": "check_debts", "bind": "debts"},
                {
                    "tool_name": "rest",
                    "when": {"left": "@debts.due_today_sim", "op": "==", "right": 0},
                },
            ],
        },
    )

    def stamp(outcome) -> bool:
        filed = [e for e in outcome.events if e.type == "agent.tool.invented"]
        assert len(filed) == 1
        return bool(filed[0].payload["has_control_flow"])

    assert stamp(plain) is False
    assert stamp(guarded) is True, (
        "a filing with a `when` on a step was not recorded as having "
        "control flow, so the adoption figure will read zero on a town "
        "that took the feature up"
    )
