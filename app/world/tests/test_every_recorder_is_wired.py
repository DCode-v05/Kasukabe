"""§3.5 / §4.1 — a recorder nothing calls is the project's signature bug.

TraceStore carries eight writers. Three were wired into the genesis run
and four were not, so `skills`, `tools_registry`, `memories` and
`businesses` took zero rows however the town behaved. Two skills were
authored on the audited run and none survived the process.

This is the same failure as the utterances table, in code written to fix
the utterances table.

The check parses each method's AST with its docstring stripped: an earlier
audit reported `record_event` as wired because the string appears in the
prose explaining why it is deliberately left out - the exact anti-pattern
the spec's §8.1 lists.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from world.run_genesis import GenesisRunner
from world.wiring import TraceStore

DELIBERATELY_UNWIRED: set[str] = set()


def _code(fn) -> str:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    body = tree.body[0].body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0].value, "value", None), str)):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def _wired_source() -> str:
    """The functions a genesis run uses to write to the trace store.

    `record_manifest` was added under INFRA-09, and the addition is
    deliberately narrow: it is called BY `attach_persistence`, one line
    in, and exists as its own method only because
    `test_a_run_says_what_it_was` needs to drive it without booting a
    uvicorn server.

    Widening this list is how a guard like this stops meaning anything,
    so the rule for adding to it is: the function must be reached from a
    genesis run unconditionally, and it must be NAMED here rather than
    matched by a pattern. `drain_tool_invocations` (EVAL-25) is the
    second entry added under that rule: the tick loop calls it every
    tick, and `test_what_a_resident_called_is_written_down` is what
    holds the tick loop to it - this guard covers the recorder having a
    caller, not that caller having one.
    """
    return "\n".join(_code(fn) for fn in (
        GenesisRunner.attach_persistence,
        GenesisRunner.seed_genesis,
        GenesisRunner.record_manifest,
        GenesisRunner.drain_tool_invocations,
    ))


def _recorders() -> list[str]:
    return sorted(
        name for name, _ in inspect.getmembers(TraceStore, inspect.isfunction)
        if name.startswith(("record_", "seed_"))
    )


def test_the_audit_reads_code_not_comments() -> None:
    """The check itself must not be fooled the way the first audit was.

    `record_event` was the canary here: it appeared in the prose explaining
    why it was left out, and an earlier audit read that prose as wiring. It
    is real code now, so it can no longer serve. The canary below is a
    string that exists only inside a docstring in `attach_persistence` - if
    the AST strip breaks, it reappears and this fails.
    """
    stripped = _wired_source()
    assert "21 GB" not in stripped, (
        "a phrase that exists only in a docstring survived the AST strip - "
        "the audit is reading prose again, which is how record_event was "
        "reported as wired while it was not"
    )
    assert "record_transaction_if_new" in stripped, (
        "the AST strip removed real code, not just docstrings"
    )


@pytest.mark.parametrize("recorder", _recorders())
def test_every_recorder_is_called_or_deliberately_excluded(recorder: str) -> None:
    if recorder in DELIBERATELY_UNWIRED:
        pytest.skip(f"{recorder} is excluded on purpose - see module docstring")
    assert recorder in _wired_source(), (
        f"TraceStore.{recorder} exists and nothing in the genesis run calls it. "
        "Whatever it records is lost when the process exits."
    )


def test_the_exclusion_list_names_only_real_methods() -> None:
    """An exclusion for a method that no longer exists would silently
    forgive a recorder that was renamed."""
    assert DELIBERATELY_UNWIRED <= set(_recorders())
