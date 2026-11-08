"""Where the evidence is, and which of it counts.

Two bodies of evidence already exist and neither cost anything to make:

  1. 520 recorded session files under `docs/runs/series/*/sessions/*.jsonl`.
     One line per resident turn, carrying the perception the resident was
     given, what it wrote, every tool call it made with the arguments and
     the world's actual answer, and how the turn ended. This is a real
     model's real output distribution, already paid for.

  2. The trace databases under `app/data/*.db`.

NOT ALL OF IT COUNTS. `app/data/kasukabe.db` is the default path, so the
test suite writes into it; its last runs carry `mock_mode = 1` and it has
accumulated 89 manifests. Reading verb reach out of it would count tools
"called" by fixtures and would miss tools the paid runs never touched.
A mock run is evidence about the harness, not about the town, so this
module labels every database and lets the caller ask for one or the other
rather than quietly blending them.

The same caution applies in the other direction: a database with no
`tool_invocations` table is not evidence that no tool was invoked. It is
a database written before that recorder was wired. `Evidence.looked_in`
carries what was actually searched so an UNPROVEN row can say where it
looked.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "app" / "data"
SERIES = REPO / "docs" / "runs" / "series"

TOOL_PREFIXES = ("world:", "world_")


def bare_tool_name(name: str) -> str:
    """The registry's spelling of a name the model used."""
    for p in TOOL_PREFIXES:
        if name.startswith(p):
            return name[len(p) :]
    return name


@dataclass(frozen=True)
class RecordedTurn:
    """One resident turn, exactly as it happened."""

    path: Path
    agent_id: str
    perception: str
    output: str
    actions: tuple[dict, ...]
    exit_reason: str
    tool_calls_count: int
    model_id: str

    @property
    def series(self) -> str:
        return self.path.parent.parent.name

    def called(self) -> Iterator[tuple[str, str]]:
        """`(tool, status)` for every call in this turn, registry-spelled."""
        for a in self.actions:
            tool = a.get("tool")
            if isinstance(tool, str) and tool:
                yield bare_tool_name(tool), str(a.get("status", "")).lower()


def recorded_turns(limit: int | None = None) -> Iterator[RecordedTurn]:
    """Every recorded turn in the series corpus.

    Malformed lines are skipped rather than raised on: these files are
    append-only run artifacts, and a truncated last line from a killed run
    is normal. Skipping is safe here because nothing downstream treats
    absence as proof - it only ever weakens a PROVEN into an UNPROVEN.
    """
    seen = 0
    for path in sorted(SERIES.glob("*/sessions/*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            done = d.get("done_status") or {}
            actions = d.get("actions") or []
            yield RecordedTurn(
                path=path,
                agent_id=str(d.get("agent_id", "")),
                perception=str(d.get("input", "")),
                output=str(d.get("output", "")),
                actions=tuple(a for a in actions if isinstance(a, dict)),
                exit_reason=str(done.get("exit_reason", "")),
                tool_calls_count=int(done.get("tool_calls_count") or 0),
                model_id=str(done.get("model_id", "")),
            )
            seen += 1
            if limit is not None and seen >= limit:
                return


@dataclass
class RunDatabase:
    """A trace database and what kind of evidence it is."""

    path: Path
    mock: bool
    agents: int
    llm_calls: int
    tables: frozenset[str] = field(default_factory=frozenset)

    @property
    def name(self) -> str:
        return self.path.name

    def has(self, table: str) -> bool:
        return table in self.tables

    def rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as c:
            try:
                return list(c.execute(sql, params))
            except sqlite3.Error:
                return []


def _count(c: sqlite3.Connection, table: str) -> int:
    try:
        return int(c.execute(f'select count(*) from "{table}"').fetchone()[0])
    except sqlite3.Error:
        return 0


def run_databases() -> list[RunDatabase]:
    """Every trace database, labelled.

    `mock` is read from the manifest where there is one. A database whose
    manifests are all `mock_mode = 1` is the suite's scratch space. Where
    there is no manifest at all the fallback is "did anybody pay for
    this?" - a run that burned real money is a real run.
    """
    out: list[RunDatabase] = []
    for path in sorted(DATA.glob("*.db")):
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as c:
                tables = frozenset(
                    n for (n,) in c.execute(
                        "select name from sqlite_master where type='table'"
                    )
                )
                modes = [
                    m for (m,) in c.execute("select mock_mode from run_manifest")
                ] if "run_manifest" in tables else []
                if modes:
                    mock = all(bool(m) for m in modes)
                else:
                    paid = c.execute(
                        "select count(*) from llm_calls "
                        "where coalesce(cost_nano_dollars,0) > 0"
                    ).fetchone() if "llm_calls" in tables else (0,)
                    mock = not (paid and paid[0])
                out.append(
                    RunDatabase(
                        path=path,
                        mock=mock,
                        agents=_count(c, "agents"),
                        llm_calls=_count(c, "llm_calls"),
                        tables=tables,
                    )
                )
        except sqlite3.Error:
            continue
    return out


def real_run_databases() -> list[RunDatabase]:
    """The databases that are evidence about the town rather than the suite."""
    return [d for d in run_databases() if not d.mock and d.llm_calls > 0]
