"""The host that runs must own the map the town is standing on.

`GROW-06`, `GROW-05`, `GROW-07`, `UI-01`, `CORE-26`, `UI-02` are one
defect wearing six ids: `run_genesis.py` hard-coded `map_service=None` into
every dependency bundle it built, so on the only host any run has ever used

  * `approve_land_petition` logged "decision recorded, no physical map
    growth applied" and the town could not grow;
  * a building the town completed was flipped to COMPLETE and never
    stamped onto a sector, so it was placeable by gate and not walkable;
  * `/map/tiled` answered 503 and both viewers drew the genesis bytes
    forever;
  * the expanded map was in no checkpoint, so a resume lost it.

And `GROW-05`: even where a MapService existed, `build_world()` was called
without `world_map=`, so `tools/bootstrap.py` loaded a SECOND WorldMap off
disk for `MovementSystem`. Two independent maps meant expansion and stamped
buildings were invisible to movement, `list_places` and every gate - the
map grew and nobody could walk on the new part.

These tests are about WIRING, not geometry. They assert that the objects
are the same objects, because "there is a map service" was true on the
other host for months while every run had none.
"""
from __future__ import annotations

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.bootstrap import DEFAULT_VILLE_DIR

pytestmark = pytest.mark.skipif(
    not DEFAULT_VILLE_DIR.exists(),
    reason="Ville assets absent; the map chain cannot be exercised (GROW-27)",
)


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))


@pytest.mark.asyncio
async def test_the_runner_boots_with_a_map_service(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    assert runner.map_service is not None, (
        "GROW-06: the production host booted with map_service=None, so land "
        "approval was a logged no-op in every run ever made"
    )


@pytest.mark.asyncio
async def test_movement_walks_the_same_map_the_service_grows(tmp_path):
    """GROW-05. Not 'both have a map' - the SAME map object.

    Two WorldMaps is the failure that lets `expand_land` succeed and
    `move` still refuse the new sector."""
    runner = _runner(tmp_path)
    await runner.boot()
    movement = runner.world_state.movement
    assert movement is not None, "no MovementSystem to share a map with"
    assert movement.world_map is runner.map_service.world, (
        "GROW-05: MovementSystem is walking a different WorldMap from the one "
        "the MapService grows and stamps buildings onto"
    )


@pytest.mark.asyncio
async def test_the_map_is_checkpointed(tmp_path):
    """GROW-07. Growth that does not survive a resume is growth that a
    spot-instance restart silently undoes."""
    runner = _runner(tmp_path)
    await runner.boot()
    names = runner.tick_engine.checkpoints.components
    assert "map" in names, (
        f"GROW-07: no map among the checkpointed components {sorted(names)}; "
        "an expanded map is lost on resume"
    )


@pytest.mark.asyncio
async def test_the_map_round_trips_a_checkpoint(tmp_path):
    """Registered is not the same as working. The adapter has to actually
    carry the geometry across, which is the half a name check misses."""
    runner = _runner(tmp_path)
    await runner.boot()
    adapter = runner.tick_engine.checkpoints.components["map"]
    blob = adapter.snapshot()
    assert blob, "the map adapter snapshotted nothing"
    adapter.restore(blob)  # must not raise on its own output
