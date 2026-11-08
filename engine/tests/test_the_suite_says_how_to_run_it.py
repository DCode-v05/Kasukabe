"""A suite that cannot start says what is missing.

`EVAL-19`. `uv sync` installs `[project.dependencies]` and NOT
`[project.optional-dependencies].dev`, which is where pytest,
pytest-asyncio and the rest of the test tooling live. So
`cd engine && uv run pytest` failed inside `tests/conftest.py` at
`import pytest_asyncio`, with a traceback that named the missing module
and nothing about what to do about it.

`STAKES_TIME_COMPOUNDING.md` section 7 reports "159 passed" without
naming the command that produced it, so a reader who cannot reproduce
that number has no way to tell a broken checkout from a missing flag -
and the flag is not guessable from the error.

Raised rather than skipped, deliberately: a suite that cannot load its
own fixtures must not report success.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
CONFTEST = ENGINE / "tests" / "conftest.py"


def test_the_missing_tooling_is_handled_at_all() -> None:
    source = CONFTEST.read_text(encoding="utf-8")
    assert "except ModuleNotFoundError" in source, (
        "EVAL-19: the conftest imports its test tooling unguarded, so a "
        "plain `uv sync` checkout fails with a traceback that says which "
        "module is missing and nothing about how to get it"
    )


def test_the_message_names_the_command() -> None:
    """The whole value of the fix. "No module named pytest_asyncio" is
    already what the interpreter says; what a reader needs is the flag."""
    source = CONFTEST.read_text(encoding="utf-8")
    handler = source[source.index("except ModuleNotFoundError"):]
    handler = handler[: handler.index("from pathlib import Path")]
    assert "--extra dev" in handler, (
        "EVAL-19: the failure does not name `uv sync --extra dev`, which is "
        "the one thing the reader cannot work out from the error"
    )
    assert "uv run pytest" in handler


def test_it_raises_rather_than_skipping() -> None:
    """A suite that silently skips its own fixtures reports a green run
    over nothing - this project has shipped exactly that shape twice."""
    source = CONFTEST.read_text(encoding="utf-8")
    handler = source[source.index("except ModuleNotFoundError"):]
    handler = handler[: handler.index("from pathlib import Path")]
    assert "raise ModuleNotFoundError" in handler
    assert "pytest.skip" not in handler


def test_the_extras_the_message_names_actually_exist() -> None:
    """A message pointing at an extra that is not declared sends the
    reader in a circle."""
    pyproject = (ENGINE / "pyproject.toml").read_text(encoding="utf-8")
    extras = re.search(
        r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)", pyproject, re.S
    )
    assert extras, "the engine declares no optional dependencies at all"
    assert re.search(r"^dev\s*=", extras.group(1), re.M), (
        "EVAL-19: the conftest tells the reader to run `--extra dev` and "
        "pyproject.toml declares no `dev` extra"
    )


@pytest.mark.parametrize("tool", ["pytest", "pytest-asyncio"])
def test_the_tooling_is_in_that_extra(tool: str) -> None:
    pyproject = (ENGINE / "pyproject.toml").read_text(encoding="utf-8")
    dev = pyproject[pyproject.index("dev = ["):]
    dev = dev[: dev.index("]")]
    assert tool in dev, (
        f"EVAL-19: {tool} is not in the `dev` extra, so following the "
        "message this conftest prints would not fix the failure"
    )
