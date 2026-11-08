"""Six cameras whose errors go to /dev/null are six ways to lose a run.

`record_ui.py` starts Chrome with `stdout=DEVNULL, stderr=DEVNULL`. That is
right for a working camera - Chrome is chatty - and it means a MISSING Chrome
is silent. Six recorders then start, six view directories are created empty,
and the operator finds out hours later, when the world is gone and the $60 is
spent, that the run everyone waited for was never filmed.

It has already half-happened: on the first 20-resident run five of the six
cameras decided the world had died nine ticks in and quit, which is how
`--watch-misses 120` came to be. That failure at least left logs.

So the launcher asks once, before it starts anything, at the one place all six
recorders route through. These tests pin the two halves of that: the question
is asked of `record_ui.py` itself rather than re-implemented, and the refusal
names a fix that works on a box with no root.
"""

from __future__ import annotations

import re
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[2] / "run-scenario.sh"
RECORDER = Path(__file__).resolve().parents[2] / "scripts" / "record_ui.py"


def _launcher() -> str:
    return LAUNCHER.read_text()


def test_the_launcher_checks_before_it_films() -> None:
    """The check has to come before the recorders start, or it is a report
    rather than a precondition."""
    text = _launcher()
    check = text.index("REFUSED: the scenario asks for video")
    launch = text.index("scripts/record_ui.py --out")
    assert check < launch, "the refusal is printed after the cameras have started"


def test_it_asks_the_recorder_and_does_not_re_implement_the_search() -> None:
    """`find_chrome` knows about four binary names, two Windows paths and
    Playwright's cache. A second copy of that list in shell would drift from
    it, and the drift would show up as a refusal on a box that can in fact
    record."""
    text = _launcher()
    assert "from record_ui import find_chrome, find_ffmpeg" in text
    for reimplemented in ("google-chrome-stable", "ms-playwright", "chromium-browser"):
        assert reimplemented not in text, (
            f"the launcher searches for {reimplemented} itself; ask find_chrome()"
        )


def test_the_refusal_names_a_fix_that_needs_no_root() -> None:
    """There is no sudo on the team box. `apt install` is not a fix there, and
    a refusal the reader cannot act on is just a stop."""
    text = _launcher()
    fix = text[text.index("REFUSED: the scenario asks for video"):]
    fix = fix[: fix.index("exit 2")]

    assert "playwright install chromium" in fix
    assert "imageio-ffmpeg" in fix
    assert "sudo" not in fix and "apt" not in fix


def test_both_fallbacks_the_refusal_promises_actually_exist() -> None:
    """The advice is only true if `find_chrome` looks in Playwright's cache and
    `find_ffmpeg` falls back to the wheel. If either stops doing so, the
    refusal starts telling people to install something it will not then find."""
    recorder = RECORDER.read_text()
    assert "ms-playwright" in recorder, "find_chrome no longer looks where the refusal points"
    assert "imageio_ffmpeg" in recorder, "find_ffmpeg no longer falls back to the wheel"


def test_no_video_is_a_real_escape_hatch() -> None:
    """The refusal offers NO_VIDEO=1. If that only skipped the check and still
    launched the cameras, the advice would loop the reader straight back into
    the failure it was meant to avoid."""
    text = _launcher()
    assert 'NO_VIDEO=1 $0 $*' in text, "the refusal must show how to proceed without video"

    # the launch site guards on it too, not just the check
    launch_region = text[text.index('PORT=9600'): text.index("scripts/record_ui.py --out")]
    assert re.search(r'if \[ "\$\{NO_VIDEO:-\}" = "1" \]', launch_region), (
        "NO_VIDEO skips the check but not the cameras"
    )


def test_a_scenario_with_no_views_is_not_forced_to_have_a_camera() -> None:
    """Most scenarios in the tree record a single view or none. The check is for
    the multi-view case the 50-resident run asks for; making it universal would
    refuse runs that never wanted video."""
    text = _launcher()
    guard = 'if [ -n "$SC_VIEWS" ] && [ "${NO_VIDEO:-}" != "1" ]; then'

    assert guard in text
    assert text.index(guard) < text.index("REFUSED: the scenario asks for video")
