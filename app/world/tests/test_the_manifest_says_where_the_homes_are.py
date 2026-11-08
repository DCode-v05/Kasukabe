"""The run manifest records where this run's engine homes are.

Run 26's report could not find its own residents' session logs. The runner
had been given `KASUKABE_HOME_ROOT=/tmp/kasukabe-agents-run26`; the reporter
had not, fell back to `/tmp/kasukabe-agents`, and reported run 25's cast -
"0 of 23 residents acted" over six residents and 452 tool calls. Nothing in
the manifest said where the homes were, so no reader could have known.

`build_manifest` only copied variables listed in `RECORDED_ENV`, and only
when they were exported, so a home root set in the config and not the
environment was never written down. It is now recorded from the config
every time, and `run_report` reads it back.
"""

from __future__ import annotations

import time
from pathlib import Path

from world.run_genesis import GenesisRunnerConfig
from world.run_manifest import build_manifest


def test_the_home_root_is_recorded_from_the_config():
    config = GenesisRunnerConfig(mock_mode=True, home_root="/tmp/kasukabe-agents-run27")

    manifest = build_manifest(
        run_id="run-27", config=config, app_root=Path("."), engine_root=None,
        started_at=time.time(), environ={},
    )

    assert manifest["env"]["KASUKABE_HOME_ROOT"] == "/tmp/kasukabe-agents-run27"


def test_it_is_recorded_even_when_nothing_was_exported():
    """The case that bit: set in config, absent from the environment."""
    config = GenesisRunnerConfig(mock_mode=True, home_root="/srv/homes")

    manifest = build_manifest(
        run_id="run-27", config=config, app_root=Path("."), engine_root=None,
        started_at=time.time(), environ={"SPEND_CAP_USD": "5.00"},
    )

    assert manifest["env"] == {"SPEND_CAP_USD": "5.00", "KASUKABE_HOME_ROOT": "/srv/homes"}


def test_a_home_root_left_at_its_default_is_not_recorded():
    """`env` records what was SET. The default home root was not chosen, and
    a key for it would say it had been (`test_only_the_knobs_that_were_set_appear`).
    The report's own fallback is that same default, so nothing is lost."""
    import dataclasses

    default_root = {f.name: f.default for f in dataclasses.fields(GenesisRunnerConfig)}["home_root"]
    config = GenesisRunnerConfig(mock_mode=True, home_root=default_root)

    manifest = build_manifest(
        run_id="run-27", config=config, app_root=Path("."), engine_root=None,
        started_at=time.time(), environ={"SPEND_CAP_USD": "5.00"},
    )

    assert manifest["env"] == {"SPEND_CAP_USD": "5.00"}


def test_an_exported_home_root_is_recorded_as_exported():
    config = GenesisRunnerConfig(mock_mode=True)

    manifest = build_manifest(
        run_id="run-27", config=config, app_root=Path("."), engine_root=None,
        started_at=time.time(), environ={"KASUKABE_HOME_ROOT": "/tmp/kasukabe-agents-run27"},
    )

    assert manifest["env"]["KASUKABE_HOME_ROOT"] == "/tmp/kasukabe-agents-run27"

