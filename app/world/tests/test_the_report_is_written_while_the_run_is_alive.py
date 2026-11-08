"""A report only written at the end is unreadable for the hours that matter.

On the team box the machine stops after four hours whether the run has
finished or not, so "at the end" may be a long way off or may never arrive at
all. A run that has been filming and spending for three hours should be
answerable *now*, not once it is over.

The same generator writes both. What makes it safe to point at a live trace is
that every connection `world/run_report.py` opens is `mode=ro` - it cannot
lock or disturb the database the run is writing to. These tests pin that,
because a future edit that opens the trace read-write would turn the live
report from a convenience into a way to corrupt a paid run.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
LAUNCHER = APP / "run-scenario.sh"
REPORT = APP / "world" / "run_report.py"


def test_every_trace_the_report_opens_is_read_only() -> None:
    """The safety property the whole feature rests on."""
    source = REPORT.read_text(encoding="utf-8")
    connects = re.findall(r"sqlite3\.connect\((.*?)\)", source, re.S)

    assert connects, "no sqlite connections found in run_report.py"
    for call in connects:
        assert "mode=ro" in call, (
            f"run_report opens a trace without mode=ro: {call.strip()[:90]} - "
            "the live report would then be able to disturb a running trace"
        )


def test_the_launcher_refreshes_the_report_while_the_run_goes() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")

    assert "REPORT_EVERY" in text, "no refresh interval"
    assert re.search(r'REPORT_EVERY="\$\{REPORT_EVERY:-120\}"', text), (
        "the default refresh should be 120s and overridable"
    )
    loop = text[text.index("REPORT_EVERY="): text.index("LIVE_REPORT=$!")]
    assert "world.run_report" in loop


def test_the_report_is_swapped_in_atomically() -> None:
    """Somebody is refreshing report.md while this writes it. Writing in place
    shows them half a file; `mv` within a directory is atomic, so they see the
    old one or the new one."""
    text = LAUNCHER.read_text(encoding="utf-8")
    loop = text[text.index("REPORT_EVERY="): text.index("LIVE_REPORT=$!")]

    assert "--out \"$OUT/.report.next\"" in loop, "the loop writes report.md directly"
    assert 'mv -f "$OUT/.report.next" "$OUT/report.md"' in loop


def test_a_failed_pass_keeps_the_last_good_report() -> None:
    """Mid-run the trace holds a half-finished story and the generator may
    not like it. That must cost one refresh, not the report."""
    text = LAUNCHER.read_text(encoding="utf-8")
    loop = text[text.index("REPORT_EVERY="): text.index("LIVE_REPORT=$!")]

    assert loop.count("if ") >= 1, "the move is not conditional on the generator succeeding"
    move = loop.index("mv -f")
    guard = loop.rindex("if ", 0, move)
    assert "world.run_report" in loop[guard:move], (
        "the mv is not guarded by the generator's exit status"
    )


def test_the_live_loop_stops_before_the_final_report_is_written() -> None:
    """Otherwise the loop's next mv lands on top of the final report and the
    run ends holding one written before it finished."""
    text = LAUNCHER.read_text(encoding="utf-8")

    killed = text.index('kill "$LIVE_REPORT"')
    final = text.rindex("world.run_report")
    assert killed < final, "the live report is still running when the final one is written"
