"""A tool the seam registers and never grants is silently uncallable.

`thread.py`'s `check_permission()` denies by default. A tool registered
onto the thread with no matching `PermissionRule` is therefore offered in
every schema, reached for by the model, and refused before its handler
ever runs.

IT FAILS IN THE QUIETEST WAY THIS ENGINE HAS. The denial happens before
the handler, so no `ToolExecution` result phase fires, so `session_log`
writes the call with no `status` at all. A row with no outcome does not
look like a failure to anything that reads it: the turn was billed, the
resident was answered nothing, and the trace records only that a call was
made.

THREE TOOLS, THREE TIMES.

  `recall_session_log`  session_log.py's own comment: "exactly the gap
                        that silently swallowed this tool's first real
                        test (no ToolExecution 'result' phase ever fired;
                        the call was denied before the handler ran)"
  `recall_memories`     memory_stream.py: "a registered tool with no rule
                        is silently never callable"
  `search_memory`       registered, never granted. 29 calls across
                        civic12 and the two 50-resident runs, every one
                        denied. Found by `proof/reach.py`, which refuses
                        to score a call whose row carries no outcome
                        rather than counting silence as refusal.

The third one is the sharp one. `search_memory` was called ZERO times in
run 25, diagnosed as a reaching problem, and fixed with a hint naming the
verb at the moment it would help. The hint worked - the reach arrived -
and the payoff went into a missing rule.

Two modules wrote the lesson down and the third still shipped without it,
which is why this is a test and not a comment.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1] / "src" / "sim"
PINNED_BOOT = SIM / "pinned_boot.py"

THREAD_TOOL_MODULES = ("session_log", "memory_stream", "memory_query")


def _module(name: str) -> ast.Module:
    return ast.parse((SIM / f"{name}.py").read_text(encoding="utf-8"))


def _assigned_names(tree: ast.Module) -> set[str]:
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _function_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.mark.parametrize("module", THREAD_TOOL_MODULES)
def test_every_thread_tool_module_declares_an_allow_rule(module: str) -> None:
    """The rule itself has to exist."""
    names = _assigned_names(_module(module))
    rules = {n for n in names if n.endswith("_ALLOW_RULE")}

    assert rules, (
        f"{module}.py registers a tool onto the thread and declares no "
        "*_ALLOW_RULE. check_permission() denies by default, so that tool "
        "is offered in every schema and refused before its handler runs - "
        "and the refusal writes no status, so nothing downstream will tell "
        "you. See this file's docstring for the three times it has happened."
    )


@pytest.mark.parametrize("module", THREAD_TOOL_MODULES)
def test_every_allow_rule_is_installed_from_the_boot_path(module: str) -> None:
    """And `pinned_boot` has to actually call the installer.

    A rule nobody installs is the same defect one step further along, and
    would read even more convincingly in review: the constant is right
    there in the file.
    """
    installers = {
        n for n in _function_names(_module(module)) if n.startswith("install_")
    }
    assert installers, f"{module}.py declares a rule and no install_* function"

    boot = PINNED_BOOT.read_text(encoding="utf-8")
    called = {n for n in installers if f"{n}(thread)" in boot}

    assert called, (
        f"{module}.py declares {sorted(installers)} and pinned_boot.py calls "
        "none of them. The rule exists, the tool is registered, and the "
        "model's calls are still denied before the handler."
    )
