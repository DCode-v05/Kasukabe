"""SG8: no instrument without a consumer.

The checklist's proposed guarantee, made standing. Three quantities shipped
computed-and-unread before this existed - `false_conviction_stats` was one:
written with GOV-02, correct, tested, and called by nothing, so the town's
flagship justice metric could not appear in any report (GOV-10).

The guard is a ratchet over names. A public function in `world/` whose name
says it measures something (`*_stats`, `*_rate`, `*_index`, `*_metric(s)`,
`*_gini`, `*_share`, `*_reading`, `*_summary`, `*_census`) must be read by
code that is not a test - a real reference in the syntax tree, not a
mention in a comment or docstring - or be an HTTP route (a decorated
function is served, so it has a reader by construction). A new metric with
no reader fails here, the day it is written.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORLD = Path(__file__).resolve().parents[1]
MEASURES = re.compile(r"_(stats|rate|index|metric|metrics|gini|share|reading|summary|census)$")


def _sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in WORLD.rglob("*.py")
        if "tests" not in path.relative_to(WORLD).parts and not path.name.startswith("test_")
    }


def _instruments(sources: dict[Path, str]) -> dict[str, tuple[Path, bool]]:
    found: dict[str, tuple[Path, bool]] = {}
    for path, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not node.name.startswith("_") and MEASURES.search(node.name)):
                found[node.name] = (path, bool(node.decorator_list))
    return found


def _references(sources: dict[Path, str]) -> dict[str, int]:
    """Every name code actually uses - not what a comment or docstring says.

    Text search would count `wiring.py`'s docstring sentence about
    `false_conviction_stats()` as a reader, which is exactly the illusion
    this guard exists to break.
    """
    counts: dict[str, int] = {}
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.alias):
                name = node.asname or node.name.rsplit(".", 1)[-1]
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


def _unread(sources: dict[Path, str] | None = None) -> list[str]:
    sources = sources if sources is not None else _sources()
    used = _references(sources)
    return sorted(
        f"{name} ({home.relative_to(WORLD)})"
        for name, (home, served) in _instruments(sources).items()
        if not served and not used.get(name)
    )


def test_every_instrument_has_a_reader() -> None:
    unread = _unread()
    assert not unread, (
        "computed and read by nothing - wire it into the report or a route, or "
        f"delete it: {unread}"
    )


def test_the_guard_would_have_caught_the_one_it_is_named_for() -> None:
    """Take `justice_reading` (GOV-10's reader) away and the metric it is
    named for is unread again - despite the docstring in `wiring.py` that
    still talks about it."""
    sources = _sources()
    institutions = WORLD / "tools" / "primitives" / "institutions.py"
    without_reader = dict(sources)
    without_reader[institutions] = re.sub(
        r"def justice_reading\(.*?(?=\ndef build_specs)", "", sources[institutions], flags=re.S
    )
    for path in list(without_reader):
        if path != institutions:
            without_reader[path] = without_reader[path].replace("justice_reading", "_gone")

    unread = _unread(without_reader)

    assert any(u.startswith("false_conviction_stats") for u in unread), unread
