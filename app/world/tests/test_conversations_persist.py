"""A run that talks must leave a record of having talked.

run_genesis is a second composition root: it builds its world through
tools/bootstrap.build_world() and never calls wiring.build_world_server(),
so TraceStore - the only writer of `agents`, `utterances` and
`transactions` - was never constructed for a genesis run. Measured on a
live 10-resident run: 14 utterances behind /world/utterances, 6 of them
reciprocated by-name answers, and 0 rows in the utterances table. The
conversations existed only in a bounded in-memory tail and died with the
process, so a report written afterwards could truthfully say it found no
conversations while the town had been talking the whole time.
"""

from __future__ import annotations

import inspect

from world.run_genesis import GenesisRunner


def test_boot_attaches_world_side_persistence() -> None:
    source = inspect.getsource(GenesisRunner.boot)
    assert "attach_persistence" in source, (
        "a genesis run wrote no world rows at all - the report reads the "
        "trace store, and nothing was ever putting the town in it"
    )


def test_persistence_is_attached_on_the_resume_path_too() -> None:
    """A resumed run holds conversations as much as a fresh one, and
    seed_genesis needs the store to exist before it seeds `agents`."""
    source = inspect.getsource(GenesisRunner.boot)
    attach = source.index("attach_persistence")
    branch = source.index("self.checkpoint_store.latest()")
    assert attach < branch, (
        "attach_persistence must run before the resume/seed branch, or a "
        "resumed run records nothing"
    )


def test_genesis_seeds_the_agents_rows_every_other_row_hangs_off() -> None:
    """utterances.speaker_agent_id and listener_agent_id are both
    `references agents (id)`. Recording speech without seeding agents
    leaves every row orphaned."""
    source = inspect.getsource(GenesisRunner.seed_genesis)
    assert "seed_agent" in source


def test_the_subscriber_records_speech_and_money() -> None:
    source = inspect.getsource(GenesisRunner.attach_persistence)
    assert "record_utterance_if_new" in source
    assert "record_transaction_if_new" in source
    assert 'subscribe("*"' in source


def _code_only(func) -> str:
    """The function's actual statements, with its docstring and comments
    removed. Asserting against raw source matches the prose explaining a
    decision as readily as the code implementing it - a test that passes
    because of what a comment says is not testing anything.
    """
    import ast

    tree = ast.parse(inspect.getsource(func).lstrip())
    body = tree.body[0].body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def test_it_does_not_mirror_every_event_payload() -> None:
    """The `events` write must stay bounded, not stay absent.

    This test used to assert that the string "record_event" did not appear
    in `attach_persistence`. That is the shape of test the run-23 review
    called out: it asserted source text, it could not fail for the right
    reason, and what it was actually protecting - "do not write 21 GB" -
    it enforced by writing NOTHING. Every genesis run left `events` empty,
    and everything that reads that table reported on emptiness (EVAL-02).

    So it now asserts the property instead of the spelling: the events that
    fire per resident per tick are excluded, and the ones that carry a
    decision are not. `is_worth_recording` is the single rule both halves
    come from.
    """
    from world.run_genesis import is_worth_recording

    assert not is_worth_recording("agent.moved")
    assert not is_worth_recording("agent.perceived")

    assert is_worth_recording("economy.trade.settled")
    assert is_worth_recording("politics.election.tallied")

    code = _code_only(GenesisRunner.attach_persistence)
    assert "record_utterance_if_new" in code
    assert "record_transaction_if_new" in code


def test_a_recorder_that_will_not_open_does_not_kill_the_run() -> None:
    source = inspect.getsource(GenesisRunner.attach_persistence)
    assert "logger.warning" in source, (
        "a run must not die because its recorder would not open, and must "
        "not go quiet about it either - silence here looks exactly like "
        "the bug this method exists to fix"
    )


def test_the_recorder_writes_to_disk_not_to_memory() -> None:
    """get_client() with no config falls through to
    SupabaseConfig.from_env(), whose sqlite_path defaults to ":memory:".

    Attaching the recorder to that is worse than not attaching it at all:
    every insert succeeds, nothing warns, and the rows vanish with the
    process - the exact symptom (an empty utterances table after a
    talkative run) this whole method exists to fix. Measured that way
    once already: persistence reported "attached", zero write failures
    logged, and data/kasukabe.db had no utterances table in it.
    """
    code = _code_only(GenesisRunner.attach_persistence)
    assert "TRACE_SQLITE_PATH" in code, (
        "the recorder must be pointed at the same trace store the rest of "
        "this runner names, or it silently records into memory"
    )
    assert "sqlite_path" in code
    assert "get_client()" not in code


def test_it_uses_the_same_trace_store_the_preflight_checks() -> None:
    """preflight checks disk headroom and writability against
    TRACE_SQLITE_PATH with the same default. If these two ever name
    different files, the run checks one store and writes to another.
    """
    import inspect as _inspect

    from world import run_genesis as rg

    runner_src = _inspect.getsource(rg)
    assert runner_src.count('os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db")') >= 2
