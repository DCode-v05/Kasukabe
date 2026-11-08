"""The run manifest records how many acts one decision was allowed to buy.

`docs/decisions/PROG-COST-MODEL.md` makes `KASUKABE_PROGRAM_MAX_CALLS` the
lever for run 26 and says, in its own closing line, that "a configuration
nobody wrote down is the same defect one layer up" as run 25's preflight
printing a $10 cap while the broker enforced $20.

The manifest already recorded `full_schema_budget` and `context_window` -
"the knobs that change what a number MEANS, so two reports can be compared
honestly or refused" - and did not record the four program ceilings beside
them. P1, acts per decision, is reported against `max_calls`. Without it in
the manifest, two runs' P1 figures cannot be told apart from two runs'
ceilings.

Recorded as RESOLVED values, read off `world.tools.program`, not as the raw
env strings: the module is what enforces them, and a manifest echoing the
env would faithfully record a value the runtime was not using.
"""

from __future__ import annotations

import json

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools import program
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map

CAP_KEYS = {
    "program_max_calls": "PROGRAM_MAX_CALLS",
    "program_max_steps": "PROGRAM_MAX_STEPS",
    "program_max_repeat": "PROGRAM_MAX_REPEAT",
    "program_wallet_share_percent": "PROGRAM_WALLET_SHARE_PERCENT",
}


@pytest.mark.asyncio
async def test_the_manifest_carries_every_program_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp",
        world_mode="embedded", world_port=0,
    ))
    await runner.boot(resume=False)

    path = runner.write_the_run_manifest()

    assert path is not None, "the manifest was not written at all"
    manifest = json.loads(path.read_text())
    for key, constant in CAP_KEYS.items():
        assert key in manifest, f"{key} is missing - P1's ceiling is unrecorded"
        assert manifest[key] == getattr(program, constant), (
            f"{key} records {manifest[key]!r} but the runtime enforces "
            f"{getattr(program, constant)!r}"
        )


def test_the_recorded_values_are_integers_not_env_strings():
    """The resolved value, not the raw string - so '40' and 40 cannot both
    appear across two manifests and compare unequal."""
    for constant in CAP_KEYS.values():
        assert isinstance(getattr(program, constant), int)
