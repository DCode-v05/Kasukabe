"""Seven of the sixty-one tools served to every resident are an
assistant's, and describe a world that does not exist.

MEASURED, in data/loop5.db, on the last cognition call of the run:

    61 tools served: 52 world, 9 engine
    engine-tool schema bytes: 6,887 of 41,569

The nine are registered by `Thread.get_or_create_thread` (frozen, Law 1)
onto the same ToolRegistry the sim built. Two of them earn their place -
`recall_session_log` and `recall_memories` are a resident remembering its
own life. The other seven are the product this engine was built for,
handed verbatim to a miner:

    ask_user          "Ask the user a question and wait for their answer.
                       Pick a format ... radio buttons ... checkboxes"
    request_replan    "expand your operational scope beyond the current
                       goals"
    gap_resolution    "request user approval, or request asset connections"
    report_checkpoint "Report progress on a mission goal ... from your
                       mission brief"
    list_content      "what content is available in the task's content
                       store"
    search_content    "Search within stored content by text or regex"
    tool_search       "Some external tools are loaded as lightweight stubs"

There is no user, no operational scope, no approval, no asset connection,
no mission brief, no task and no content store. This is the operator's
point exactly - the prompts "were written for engine running as an
assistant" - and it survives in the tool schemas rather than in the soul,
which is the one place we already checked. The system prompt itself is
correct: `soul_seed.py` writes a real persona and the Law of Kasukabe,
and says "never as a generic assistant" in as many words.

WHAT IT COSTS. 6,887 bytes of schema on every cognition call, describing
machinery a resident cannot use. Residents do reach for it: across three
runs, `request_replan` was called 4 times and `list_content` once - five
turns of five lives spent on tools that could never have worked.

WHY IT IS FIXED IN src/sim/. thread.py is frozen. It registers these onto
`self.tools`, a plain dict on the registry the sim owns and passes in, so
the seam removes them after the thread is built and before the first turn
- no change to frozen code, and a tool that comes back gets caught here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[3] / "engine" / "src"
SURFACE = ENGINE / "sim" / "tool_surface.py"
BOOT = ENGINE / "sim" / "pinned_boot.py"
THREAD = ENGINE / "engine_core" / "thread.py"


def surface_names(constant: str) -> set[str]:
    """The literal string set assigned to a module constant."""
    tree = ast.parse(SURFACE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = getattr(node, "targets", None) or ([node.target] if hasattr(node, "target") else [])
        for target in targets:
            if isinstance(target, ast.Name) and target.id == constant:
                return {
                    element.value
                    for element in ast.walk(node.value)
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
    raise AssertionError(f"{constant} is not defined in {SURFACE}")


NOT_A_RESIDENTS = (
    "ask_user",
    "request_replan",
    "gap_resolution",
    "report_checkpoint",
    "list_content",
    "search_content",
    "tool_search",
)


@pytest.mark.parametrize("name", NOT_A_RESIDENTS)
def test_the_assistants_tools_are_pruned(name: str) -> None:
    assert name in surface_names("NOT_A_RESIDENTS_TOOL"), (
        f"{name} describes a user, a mission or a task, none of which exist "
        "in Kasukabe, and it is still served to every resident every turn"
    )


@pytest.mark.parametrize("name", ("recall_session_log", "recall_memories"))
def test_remembering_your_own_life_is_kept(name: str) -> None:
    """The two engine tools a resident genuinely has a use for. A prune
    list that took these would be trading one wrong surface for another."""
    assert name not in surface_names("NOT_A_RESIDENTS_TOOL")


def test_no_world_tool_is_ever_pruned() -> None:
    """The town's own tools are the point. If a prefix ever slipped into
    this list it would silently disarm the world."""
    assert not any(name.startswith("world_") for name in surface_names("NOT_A_RESIDENTS_TOOL"))


def test_the_prune_runs_after_the_thread_is_built() -> None:
    """Gate 1: wired to a live call site, and in the only order that
    works. thread.get_or_create_thread REGISTERS these, so pruning before
    it would remove nothing and pass every unit test.
    """
    source = BOOT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in ("get_or_create_thread", "prune_to_the_towns_tools"):
                lines.setdefault(name, node.lineno)
    assert "prune_to_the_towns_tools" in lines, (
        "pinned_boot never prunes, so every resident is still handed the "
        "assistant's tools"
    )
    assert lines["prune_to_the_towns_tools"] > lines["get_or_create_thread"], (
        "pruning before get_or_create_thread removes nothing: that call is "
        "what registers them"
    )


def test_the_frozen_engine_really_does_register_them() -> None:
    """The control. If thread.py stops registering these, this file is
    pruning nothing and somebody should find out rather than keep a dead
    list forever."""
    source = THREAD.read_text(encoding="utf-8")
    for registrar in (
        "register_ask_user_tool",
        "register_replan_tool",
        "register_gap_resolution_tool",
        "register_list_content_tool",
    ):
        assert registrar in source, (
            f"{registrar} is gone from thread.py - re-measure what is actually "
            "served before trusting NOT_A_RESIDENTS_TOOL"
        )
