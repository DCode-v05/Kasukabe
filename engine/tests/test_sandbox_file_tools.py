"""Tests for the write_file and file_edit sandbox tools.

Covers handler parameter validation and the `executor.edit_file`
replacement semantics: uniqueness enforcement when `replace_all=False`,
`replace_all=True` multi-substitution, and the missing-file and
not-found error paths. Uses a real SandboxExecutor with a temp
workspace (no mocks).
"""

from __future__ import annotations

import pytest

from src.sandbox.executor import SandboxExecutor
from src.sandbox.tools import build_sandbox_tools


def executor() -> SandboxExecutor:
    return SandboxExecutor()


def make_handler(name: str, executor: SandboxExecutor):
    for tool in build_sandbox_tools(executor):
        if tool.name == name:
            return tool.handler
    raise KeyError(f"Sandbox tool {name!r} not registered")


@pytest.mark.asyncio
async def test_write_file_clean_content_succeeds() -> None:
    ex = executor()
    handler = make_handler("write_file", ex)
    result = await handler({"path": "hello.txt", "content": "Hello, world!"})
    assert "Error" not in result
    assert (ex.workspace / "hello.txt").read_text() == "Hello, world!"


@pytest.mark.asyncio
async def test_write_file_empty_content_succeeds() -> None:
    ex = executor()
    handler = make_handler("write_file", ex)
    result = await handler({"path": "empty.txt", "content": ""})
    assert "Error" not in result


@pytest.mark.asyncio
async def test_write_file_missing_path_returns_error() -> None:
    ex = executor()
    handler = make_handler("write_file", ex)
    result = await handler({"content": "some content"})
    assert result == "Error: path is required"


@pytest.mark.asyncio
async def test_file_edit_clean_replacement_succeeds() -> None:
    ex = executor()
    (ex.workspace / "cfg.txt").write_text("old value here", encoding="utf-8")
    handler = make_handler("file_edit", ex)
    result = await handler(
        {
            "path": "cfg.txt",
            "old_string": "old value",
            "new_string": "new value",
        }
    )
    assert "Error" not in result
    assert (ex.workspace / "cfg.txt").read_text() == "new value here"


@pytest.mark.asyncio
async def test_file_edit_replace_all_substitutes_every_occurrence() -> None:
    ex = executor()
    (ex.workspace / "rep.txt").write_text("a a a", encoding="utf-8")
    handler = make_handler("file_edit", ex)
    result = await handler(
        {
            "path": "rep.txt",
            "old_string": "a",
            "new_string": "b",
            "replace_all": True,
        }
    )
    assert "Error" not in result
    assert (ex.workspace / "rep.txt").read_text() == "b b b"


@pytest.mark.asyncio
async def test_file_edit_non_unique_old_string_errors_without_replace_all() -> None:
    ex = executor()
    (ex.workspace / "dup.txt").write_text("x x x", encoding="utf-8")
    handler = make_handler("file_edit", ex)
    result = await handler(
        {
            "path": "dup.txt",
            "old_string": "x",
            "new_string": "y",
        }
    )
    assert "not unique" in result
    assert "3 matches" in result
    assert (ex.workspace / "dup.txt").read_text() == "x x x"


@pytest.mark.asyncio
async def test_file_edit_unique_old_string_succeeds_without_replace_all() -> None:
    ex = executor()
    (ex.workspace / "uniq.txt").write_text("alpha beta gamma", encoding="utf-8")
    handler = make_handler("file_edit", ex)
    result = await handler(
        {
            "path": "uniq.txt",
            "old_string": "beta",
            "new_string": "BETA",
        }
    )
    assert "Error" not in result
    assert (ex.workspace / "uniq.txt").read_text() == "alpha BETA gamma"


@pytest.mark.asyncio
async def test_file_edit_old_string_not_found_returns_error() -> None:
    ex = executor()
    (ex.workspace / "g.txt").write_text("content", encoding="utf-8")
    handler = make_handler("file_edit", ex)
    result = await handler(
        {
            "path": "g.txt",
            "old_string": "not present",
            "new_string": "replacement",
        }
    )
    assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_file_edit_missing_file_returns_error() -> None:
    ex = executor()
    handler = make_handler("file_edit", ex)
    result = await handler(
        {
            "path": "does_not_exist.txt",
            "old_string": "x",
            "new_string": "y",
        }
    )
    assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_file_edit_missing_path_returns_error() -> None:
    ex = executor()
    handler = make_handler("file_edit", ex)
    result = await handler({"old_string": "x", "new_string": "y"})
    assert result == "Error: path is required"


@pytest.mark.asyncio
async def test_file_edit_missing_old_string_returns_error() -> None:
    ex = executor()
    (ex.workspace / "h.txt").write_text("content", encoding="utf-8")
    handler = make_handler("file_edit", ex)
    result = await handler({"path": "h.txt", "new_string": "replacement"})
    assert result == "Error: old_string is required"
