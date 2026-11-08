"""The first move of a run has to be a walk like every other one.

`CORE-31`. `move` walks only when BOTH the destination is a map sector AND
the mover's own position resolves to a tile
(`movement.resolve_current_tile`). `world.agent_tiles` is empty until a
walk COMPLETES, and every genesis resident starts at `home:<household>`,
which is not a sector - so the first move of every run took the opaque-jump
fallback and the resident was teleported without being told which it got.

Run 23 measured it: over 196 `move` calls, 170 `look_around` results came
back with `"tile": null` and 3 with a tile. The walking the map exists for
was reachable only by residents who had already somehow walked.
"""
from __future__ import annotations

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.bootstrap import DEFAULT_VILLE_DIR

pytestmark = pytest.mark.skipif(
    not DEFAULT_VILLE_DIR.exists(),
    reason="no Ville assets: there is no map to stand anybody on (GROW-27)",
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
async def test_somebody_is_standing_on_a_tile_at_genesis(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()

    tiles = runner.world_state.agent_tiles
    assert tiles, (
        "CORE-31: not one resident has a tile at genesis, so the first move "
        "of the run is an opaque jump for every one of them"
    )


@pytest.mark.asyncio
async def test_the_tiles_are_inside_the_map(tmp_path):
    """A tile the map does not contain is worse than none: it would make
    `resolve_current_tile` answer confidently and wrongly."""
    runner = _runner(tmp_path)
    await runner.boot()

    world_map = runner.map_service.world
    for agent_id, (x, y) in runner.world_state.agent_tiles.items():
        assert 0 <= x < world_map.width, f"{agent_id} at x={x}"
        assert 0 <= y < world_map.height, f"{agent_id} at y={y}"


@pytest.mark.asyncio
async def test_a_town_with_no_map_still_boots(tmp_path, monkeypatch):
    """Best effort by design. A checkout without the gitignored assets has
    no map to ask, and must still boot a town that talks and trades."""
    runner = _runner(tmp_path)
    await runner.boot()
    runner.map_service = None
    runner.stand_them_on_a_tile("n01", "home:h_quarry")   # must not raise
