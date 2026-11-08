"""Two runs of one scenario must not land in the same folder.

`fifty_from_day_two.json` hardcoded `report.out`, and the launcher preferred
that over the run's name - so every run of that scenario, on any day, wrote its
report and its six videos into `docs/runs/series/fifty-day2`. A second run
overwrote the first one's evidence, and the folder said nothing about when
either happened.

The name now carries the date and the folder follows the name.

That change makes a second, sharper hazard worth guarding: a RESUME that finds
nothing to resume used to start a FRESH run with a FRESH budget while the
operator believed they were continuing one. Dated names are longer and easier
to mistype - and the team box stops runs overnight, so resuming the next
morning without naming the run computes today's date, matches nothing, and
would have silently started the $60 over.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
LAUNCHER = APP / "run-scenario.sh"
SCENARIO = APP / "world" / "scenarios" / "fifty_from_day_two.json"


def test_the_default_run_name_carries_todays_date() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")

    assert re.search(r'NAME="\$\{2:-\$SC_NAME-\$\(date \+%Y%m%d\)\}"', text), (
        "the default run name no longer dates itself"
    )


def test_the_folder_follows_the_name_not_the_scenario() -> None:
    """`SC_OUT` winning is what sent every run of a scenario to one folder."""
    text = LAUNCHER.read_text(encoding="utf-8")
    out_line = next(l for l in text.splitlines() if l.startswith("OUT="))

    assert "$NAME" in out_line
    assert "SC_OUT" not in out_line, (
        "the scenario's hardcoded folder still beats the run name"
    )


def test_the_fifty_agent_scenario_does_not_pin_a_folder() -> None:
    body = json.loads(SCENARIO.read_text(encoding="utf-8"))

    assert "out" not in (body.get("report") or {}), (
        "fifty_from_day_two.json pins report.out again - every run of it would "
        "share one folder"
    )


def test_the_run_still_knows_what_it_is_for() -> None:
    """Dating the folder must not have disturbed the things the run is about."""
    body = json.loads(SCENARIO.read_text(encoding="utf-8"))

    assert body["world"]["budget_cap_usd"] == 60.0
    assert body["world"]["start_tick"] == 324, "game-day 2 at dawn"
    assert body["charter"]["compulsory_voting"] is True
    # eight since the workshop and talk books were added, so the skills and
    # tools the town invents are filmed alongside the map and the chats
    assert len(body["recording"]["views"]) == 8
    assert {v["name"] for v in body["recording"]["views"]} >= {
        "town-with-buildings", "town-without-buildings", "workshop-skills-tools",
    }


def test_a_resume_with_nothing_to_resume_is_refused() -> None:
    """Live, because this one costs $60 when it is wrong. The launcher is run
    for real with a name that does not exist; it must refuse before starting
    anything."""
    result = subprocess.run(
        ["./run-scenario.sh", "world/scenarios/fifty_from_day_two.json",
         "a-name-that-does-not-exist-9f3c", "4"],
        cwd=APP, env={"RESUME": "1", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())},
        capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 2, f"expected a refusal, got {result.returncode}"
    assert "no run called" in result.stdout
    assert "start the budget again" in result.stdout


def test_the_refusal_names_the_runs_that_could_be_continued() -> None:
    """A refusal the reader cannot act on is just a stop. The likely cause is
    a mistyped name, so the right names are printed."""
    text = LAUNCHER.read_text(encoding="utf-8")
    block = text[text.index("REFUSED: RESUME=1"): text.index("RESUME_FLAG=\"\"\n    echo \"   resuming")]

    assert "ls -1 data/*.db" in block, "the refusal does not list the runs that exist"
    assert "Pass the name as the second argument" in block


def test_the_lock_is_released_when_a_resume_is_refused() -> None:
    """The refusal happens after the lock is taken. A leaked lock would block
    the corrected command that follows it."""
    text = LAUNCHER.read_text(encoding="utf-8")
    trap = text.index("trap cleanup EXIT INT TERM")
    refusal = text.index("REFUSED: RESUME=1")

    assert trap < refusal, "the resume guard runs before the cleanup trap is set"
