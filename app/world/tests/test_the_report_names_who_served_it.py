"""The report claimed Bedrock served a run that Bedrock never touched.

"| Bedrock calls | 4,775 |" was written when Bedrock was the only place a
call could go. With an OpenAI lane it is a claim, and on the fifty-resident
run a false one: every call was served by `gpt-6-luna` and the table said
otherwise. A reader checking whether ONLY_MODEL had held would have read that
row and concluded it had not - the row exists to answer exactly that question
and was answering it backwards.

The label is now read from the trace. `llm_calls.model` is what actually
served; a provider name compiled in years ago is not.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from world.run_report import _served_by

SCHEMA = "create table llm_calls (id integer primary key, model text, cost_nano_dollars integer);"


def _db(tmp_path: Path, models: list[str]) -> str:
    p = tmp_path / "t.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    c.executemany("insert into llm_calls (model, cost_nano_dollars) values (?,?)",
                  [(m, 1000) for m in models])
    c.commit(); c.close()
    return str(p)


def test_one_model_is_named_outright(tmp_path: Path) -> None:
    label = _served_by(_db(tmp_path, ["gpt-6-luna"] * 5))

    assert "gpt-6-luna" in label
    assert "Bedrock" not in label


def test_a_bedrock_only_run_still_says_so(tmp_path: Path) -> None:
    """Naming the real model is not the same as never saying Bedrock. A run
    Kimi served should read as Kimi's."""
    label = _served_by(_db(tmp_path, ["moonshotai.kimi-k2.5"] * 3))

    assert "moonshotai.kimi-k2.5" in label


def test_several_models_are_counted_not_guessed(tmp_path: Path) -> None:
    """A run that fanned out across providers should say so in the row a
    reader checks first, rather than name whichever happened to lead."""
    label = _served_by(_db(tmp_path, ["gpt-6-luna"] * 4 + ["deepseek.v3.2"] * 2))

    assert "2 models" in label
    assert "gpt-6-luna" not in label, "naming only the leader hides the overflow"


def test_a_label_never_fails_the_report(tmp_path: Path) -> None:
    """A report that cannot be written because a label could not be computed
    is worse than a vague label."""
    assert _served_by(str(tmp_path / "does-not-exist.db")) == "Model calls"


def test_an_empty_trace_says_nothing_it_cannot_know(tmp_path: Path) -> None:
    assert _served_by(_db(tmp_path, [])) == "Model calls"


def test_the_row_uses_it(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1].joinpath("run_report.py").read_text()

    assert "| {_served_by(db)} |" in source, "the run table still hardcodes a provider"
    assert "| Bedrock calls |" not in source, "a hardcoded Bedrock label survives"
