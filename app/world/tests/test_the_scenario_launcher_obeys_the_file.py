"""`run-scenario.sh` runs what the scenario file says, and refuses what it must.

The launcher is the only part of a run nobody unit-tests until it costs money,
so the things it must not get wrong are checked here as text: that it hands the
scenario to the runner instead of re-deciding the run in bash, that it keeps
the rotated-key guard the paid launchers have, and that it takes its cap, its
camera and its output directory from the file. A second parser for the same
file, in shell, is how a report ends up naming a scenario the run did not do.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from world.scenario import Scenario

APP = Path(__file__).resolve().parents[2]
LAUNCHER = APP / "run-scenario.sh"
SCENARIO = APP / "world" / "scenarios" / "election_at_dawn_8.json"


@pytest.fixture(scope="module")
def script() -> str:
    return LAUNCHER.read_text()


def test_it_is_executable() -> None:
    assert LAUNCHER.exists() and LAUNCHER.stat().st_mode & 0o111, "chmod +x run-scenario.sh"


def test_the_key_gate_is_gone(script: str) -> None:
    """It used to refuse without `CONFIRM_KEY_ROTATED=1`, and the gate never
    checked a key - nothing here can, without spending money. It only made
    the operator say the words, and `docs/runs/series/*/KEY-NOT-ROTATED.txt`
    records two runs where the words were untrue and the run went ahead.
    Removed rather than left to be answered wrong a third time."""
    assert "CONFIRM_KEY_ROTATED" not in script


def test_it_hands_the_scenario_to_the_runner(script: str) -> None:
    assert '--scenario "$SCENARIO"' in script
    for decided_by_the_file in ("--start-at-dawn", "--max-ticks", "--genesis-path"):
        assert decided_by_the_file not in script, (
            f"{decided_by_the_file} in the launcher would fight the scenario file"
        )


def test_it_reads_the_file_once_through_python(script: str) -> None:
    assert "from world.scenario import Scenario" in script
    for from_the_file in ("SC_CAP", "SC_URL", "SC_STOP", "SC_OUT", "SC_COUNT_TICK"):
        assert from_the_file in script, f"{from_the_file} is read from the scenario, then unused"


def test_it_keeps_the_evidence_a_run_needs(script: str) -> None:
    assert 'cp "$SCENARIO" "$OUT/scenario.json"' in script, "the report must name the run's own setup"
    assert "world.run_report" in script and "record_ui.py" in script
    assert 'already exists' in script, "an existing trace must not be overwritten (SG7)"


def test_the_shipped_election_scenario_is_the_one_that_fits_the_budget() -> None:
    """The numbers the budget was agreed on: 8 residents, tick 36 to 150, cap $19.

    At the measured $2.27-2.88 a resident-day that is $14.40-18.30, and the run
    crosses the day boundary at tick 144 - the first tax charged and the first
    wages payable - which is what makes it worth more than the election alone.
    """
    scenario = Scenario.load(str(SCENARIO))
    cast = json.loads((APP / "world" / "genesis_8_funded.json").read_text())

    assert scenario.world.start_tick == 36 and scenario.world.stop_tick == 150
    assert scenario.world.budget_cap_usd == 19.0
    assert scenario.election_phase_ticks()[3] == 108, "counted with 42 ticks of the day left"
    assert scenario.world.stop_tick > 144, "the run must cross the day boundary"

    residents = cast["population"]
    ticks = scenario.world.stop_tick - scenario.world.start_tick
    for rate, ceiling in ((2.27, 15.0), (2.88, 19.0)):
        assert residents * ticks / 144 * rate <= ceiling

    wallets = cast["usd_ledger"]["agent_wallets_cents"]
    assert wallets / residents / 100 >= ticks / 144 * 2.88, "a wallet must outlast the run"
    assert wallets + cast["usd_ledger"]["treasury_cents"] == 100_000, "the $1,000 invariant"


def test_it_keeps_the_session_files_a_re_audit_needs(script: str) -> None:
    """Three runs are recorded as unauditable for want of one copy step.

    `proof/corpus.py` reads `docs/runs/series/*/sessions/*.jsonl`. The
    engines write exactly that, one per resident, into their homes - and
    the launcher archived the homes into a tarball and put nothing where
    the audit looks. election8, final6 and teambox50 all carry "no
    per-turn session files; this run cannot be re-audited", and for two of
    them the evidence was inside the tarball the whole time.
    """
    assert 'mkdir -p "$OUT/sessions"' in script
    assert "session_log.jsonl" in script, "the file the engines actually write"


TRIAL = APP / "world" / "scenarios" / "a_trial_day_two_20.json"
SCRIPT_TEXT = (APP / "run-scenario.sh").read_text(encoding="utf-8")


def test_the_launcher_films_every_view_the_file_names(script: str) -> None:
    """One recording shows one face of a run, and afterwards nobody can ask
    the others - the world is gone. The map with roofs on is a different
    question from the map with them off, and both are different from the
    group chats, the vote and the money.

    Each view is its own headless Chrome and needs a DevTools port of its
    own, or the second attaches to the first's browser and films the same
    view twice. The profile needs no flag - `record_ui.py` mkdtemps one
    per process. The failure is silent either way: a run that filmed one
    view and thought it filmed six.
    """
    assert "$SC_VIEWS" in script, "the launcher never reads the view list"
    assert '--port "$PORT"' in script, "two Chromes cannot share one DevTools port"
    assert 'PORT=$((PORT + 1))' in script, "every view needs a port of its own"
    assert "$OUT/video/$VNAME" in script, "each view needs its own frames directory"


def test_a_scenario_with_no_views_still_records_as_it_always_did(script: str) -> None:
    """Every scenario written before this names one `recording.url` and no
    views. They must keep filming exactly as they did."""
    assert 'if [ -n "$SC_VIEWS" ]; then' in script
    assert 'else' in script.split('if [ -n "$SC_VIEWS" ]; then')[1], (
        "no fallback: the older scenarios would film nothing"
    )


def test_every_camera_is_stopped_and_waited_for(script: str) -> None:
    """A recorder killed without being waited on loses the encode, which is
    how run 27's video was lost - the frames were there and the mp4 was
    never written."""
    assert 'for R in $RECORDERS; do kill -INT "$R"' in script
    assert 'for R in $RECORDERS; do wait "$R"' in script


def test_the_trial_films_with_and_without_buildings_and_the_chats() -> None:
    """The three the operator named by hand, plus the vote this run exists
    to hold."""
    scenario = Scenario.load(str(TRIAL))
    views = {v["name"]: v["url"] for v in scenario.recording["views"]}

    assert "roofs=on" in views["town-with-buildings"]
    assert "roofs=off" in views["town-without-buildings"]
    assert "book=groupchat" in views["live-chats"]
    assert "book=election" in views["election"]
    assert len({v["name"] for v in scenario.recording["views"]}) == 6, "six distinct views"


def test_the_trial_holds_its_election_on_game_day_two() -> None:
    """"Day 2 is when the election has to happen."

    144 ticks to a game-day, so day 2 runs from tick 288 to 431. The run
    starts at dawn on day 2 rather than living through days 0 and 1 to get
    there, because twenty residents for two whole days costs about $69 at
    the rate the 30-resident run measured and the cap for this one is $20.
    """
    scenario = Scenario.load(str(TRIAL))
    counted = scenario.election_phase_ticks()[3]

    assert 288 <= scenario.world.start_tick < 432, "the run starts on day 2"
    assert 288 <= counted < 432, f"the vote is counted on day {counted // 144}"
    assert scenario.world.stop_tick > counted, "the run must outlast its own election"


def test_the_trial_fits_the_cap_it_declares() -> None:
    """At the three rates real runs have measured. A run that cannot afford
    to reach its own vote is a scenario that has not been read."""
    scenario = Scenario.load(str(TRIAL))
    cast = json.loads((APP / "world" / "genesis_20_funded.json").read_text())

    residents = cast["population"]
    assert residents == 20
    to_the_vote = scenario.election_phase_ticks()[3] - scenario.world.start_tick
    whole_run = scenario.world.stop_tick - scenario.world.start_tick

    for rate in (1.72, 2.28, 2.49):
        assert residents * to_the_vote / 144 * rate <= scenario.world.budget_cap_usd, (
            f"at ${rate}/resident-day the vote is never reached"
        )
    assert whole_run > to_the_vote - scenario.world.start_tick, (
        "the run must outlast its own election"
    )
    assert residents * whole_run / 144 * 1.72 > scenario.world.budget_cap_usd, (
        "the tick range is affordable, so the clock would stop this run "
        "before the budget did and the cap would never be tested"
    )


def test_the_trial_opens_a_town_that_has_something_in_it() -> None:
    """The point of this run: the seeded economy, live. A cast with empty
    shelves would be testing the old world with a new scenario."""
    cast = json.loads((APP / "world" / "genesis_20_funded.json").read_text())

    assert all(a.get("opening_stock") for a in cast["agents"]), "a resident wakes hungry"
    assert cast["construction_company"].get("opening_stock", {}).get("timber"), "the yard is bare"
    for shop in cast["small_businesses"]:
        if shop["business_type"] == "pharmacy":
            continue
        assert shop.get("opening_stock"), f"{shop['business_id']} opens empty"
        assert shop.get("opening_prices"), f"{shop['business_id']} opens with no price board"

def test_every_recorder_flag_the_launcher_passes_actually_exists() -> None:
    """The launcher and the recorder must agree on the flag names.

    They did not. The first 20-resident run passed
    `--remote-debugging-port` and `--user-data-dir`, which are CHROME's own
    flags and not `record_ui.py`'s - so all six cameras died on argument
    parsing within a second, and the run carried on spending with nothing
    being filmed. The test written beside it asserted the strings the
    LAUNCHER contained, so it passed while being wrong about both.

    This reads the recorder's real parser instead. A flag renamed on either
    side now fails here rather than at the start of a paid run.
    """
    import argparse
    import re
    import runpy

    source = (APP / "scripts" / "record_ui.py").read_text(encoding="utf-8")
    known = set(re.findall(r'add_argument\("(--[a-z-]+)"', source))
    assert known, "could not read record_ui.py's own arguments"

    calls = re.findall(r'"\$PY" -u scripts/record_ui\.py(.*?)&\n', SCRIPT_TEXT, re.S)
    assert calls, "no record_ui.py invocation found in the launcher"
    used = {f for call in calls for f in re.findall(r"(--[a-z-]+)", call)}
    for flag in used:
        assert flag in known, (
            f"run-scenario.sh passes {flag} and record_ui.py does not accept it - "
            f"it takes {sorted(known)}"
        )


def test_a_camera_does_not_quit_on_a_busy_world(script: str) -> None:
    """`--watch-misses` defaults to 3, which is a hair trigger.

    The watch exists so a camera stops when the world does. With six
    Chromes asking at once, five of the six decided the world had died nine
    ticks into a paid run, encoded what they had and quit - while the world
    was answering in about a millisecond.
    """
    assert "--watch-misses" in script, "the default of 3 killed five cameras once"
    misses = int(re.search(r"--watch-misses (\d+)", script).group(1))
    assert misses >= 30, f"{misses} consecutive misses is still a hair trigger"
