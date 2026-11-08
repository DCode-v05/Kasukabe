"""The report watcher must not overwrite a live report with blank rows.

Run 26. `run_report --watch` printed "spend cap reached - writing the final
report" and regenerated while the world was shutting down. Every live read
came back empty, so the report said "Nobody spoke", "No agent earned
proficiency" and "Alive / dormant / dead - / - / -" about a town that had
spoken 37 times, and the 659-line live snapshot written four minutes
earlier was replaced by a 519-line blank one. The module's own rule is
"generate the report before stopping the run"; the tool built to honour it
broke it on its last write.

The guard is `write_report(..., world_answers=...)`: in watch mode a write
needs the world answering before the build AND after it, so a world that
stops answering halfway through is caught too.
"""

from __future__ import annotations

import ast
import inspect

from world import run_report


def test_a_world_that_is_not_answering_does_not_overwrite(tmp_path):
    out = tmp_path / "run-26-report.md"
    out.write_text("THE 659-LINE LIVE SNAPSHOT", encoding="utf-8")

    wrote = run_report.write_report(out, lambda: "BLANK ROWS", world_answers=lambda: False)

    assert wrote is False
    assert out.read_text(encoding="utf-8") == "THE 659-LINE LIVE SNAPSHOT"


def test_a_world_that_stops_answering_mid_build_is_discarded(tmp_path):
    out = tmp_path / "run-26-report.md"
    out.write_text("THE 659-LINE LIVE SNAPSHOT", encoding="utf-8")
    answers = iter([True, False])

    wrote = run_report.write_report(
        out, lambda: "HALF-BLANK ROWS", world_answers=lambda: next(answers)
    )

    assert wrote is False
    assert out.read_text(encoding="utf-8") == "THE 659-LINE LIVE SNAPSHOT"


def test_a_live_world_is_written(tmp_path):
    out = tmp_path / "run-26-report.md"

    wrote = run_report.write_report(out, lambda: "A LIVE REPORT", world_answers=lambda: True)

    assert wrote is True
    assert "A LIVE REPORT" in out.read_text(encoding="utf-8")


def test_a_report_made_by_hand_after_a_run_still_writes(tmp_path):
    """Documented behaviour, kept: generating after the world is gone reads
    a world that is no longer there, and says so - it is not refused."""
    out = tmp_path / "run-26-report.md"

    wrote = run_report.write_report(out, lambda: "AFTER THE FACT", world_answers=None)

    assert wrote is True


def test_every_watch_mode_write_goes_through_the_guard():
    """Parsed, not grepped: no bare `once()` may remain in `main`."""
    tree = ast.parse(inspect.getsource(run_report.main))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "once"
    ]
    assert calls, "main no longer writes through once()"
    for call in calls:
        assert any(kw.arg == "live_only" for kw in call.keywords), (
            f"a once() at line {call.lineno} writes without the live-world guard"
        )
