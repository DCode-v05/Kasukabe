"""A run that keeps no session logs cannot be re-audited, ever.

The per-turn session logs are written by the seam into each engine's home
(`<home_root>/<agent>/session_log.jsonl`). The homes are archived into
`engine-homes.tar.gz` and then the run directory is what anybody actually
reads. A launcher that does not copy them out has thrown away the only
turn-by-turn record of a run it just paid for - and the loss is invisible,
because the report, the log and the video are all still there.

THREE RUNS WERE LOST THIS WAY. `proof/reach.py` decides whether a verb was
ever reached by reading `docs/runs/series/*/sessions/*.jsonl`, and scored
`final6`, `teambox50` and `election8` as unauditable for want of a copy
step. `election8`'s files were recovered afterwards, out of the archive
they had been sitting in the whole time. The other two are gone.

`run-scenario.sh` was fixed when that was noticed. `run-election.sh` - the
launcher for the twenty- and fifty-resident runs, which are the expensive
ones - was not, and that is what this test exists to stop recurring.

It reads the scripts rather than running them: these launchers spend money
and take hours, so the assertion is about what the script will do, not
about doing it. That is a source-text check, and it is the one shape of
source-text check this package allows - not "does a function exist" (which
a comment satisfies) but "does this shell script contain the copy loop",
where the text IS the behaviour because shell has no other layer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]

#: Every launcher that drives a run to completion and owns a run directory.
#: `run20.sh` and `run50.sh` are absent where they only `exec` another one -
#: the test follows the exec instead.
LAUNCHERS = ("run-election.sh", "run-scenario.sh", "runseries.sh")


def _script(name: str) -> str:
    return (APP / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_the_launcher_copies_the_session_logs_out(launcher: str) -> None:
    script = _script(launcher)

    assert "session_log.jsonl" in script, (
        f"{launcher} never copies session_log.jsonl out of the engine homes. "
        "The run will finish, write a report and a video, and leave nothing "
        "that can be read turn by turn - which is how final6 and teambox50 "
        "became permanently unauditable."
    )
    assert "sessions" in script, (
        f"{launcher} mentions the session log but not the sessions/ folder "
        "proof/reach.py reads"
    )


@pytest.mark.parametrize("launcher", ("run20.sh", "run50.sh"))
def test_the_thin_wrappers_reach_a_launcher_that_keeps_them(launcher: str) -> None:
    """`run20.sh` is one line. What matters is where it lands."""
    script = _script(launcher)

    if "session_log.jsonl" in script:
        return  # keeps them itself, like run50.sh does
    execed = [name for name in LAUNCHERS if name in script]
    assert execed, (
        f"{launcher} neither keeps session logs nor hands off to a launcher "
        "that does"
    )
    for name in execed:
        assert "session_log.jsonl" in _script(name), (
            f"{launcher} hands off to {name}, which does not keep them either"
        )
