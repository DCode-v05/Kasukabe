"""A resident's standing commitment comes back from disk as a commitment.

Run 28b, resumed from run 28's tick-69 checkpoint. Wren Thorne had set out
at tick 68 to harvest wheat until she held twelve. Her first turn after the
resume, and every one after it:

    n06 lost its turn to AttributeError: 'dict' object has no attribute
    'satisfied' (24 turn(s) lost so far)

`WorldState.restore` rebuilds the typed values a JSON checkpoint flattened
into dicts, through `_typed_state_fields`. That table lists price posts,
wage promises, intentions, goals, needs, beliefs - and not `continuing`,
even though `continuing` is in OWNED_STATE_FIELDS and is written to every
checkpoint. So a commitment survived a resume as a dict, and the first
line to ask it whether it was done killed the turn. One resident silenced
for a whole run, by a table entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.state import ContinuingAction, _rehydrated

WREN = {
    "location_id": "farmland:wheat", "owner": "n06", "params": {"crop": "wheat"},
    "repetitions": 0, "started_tick": 68, "tool": "harvest",
    "until": {"item_count": {"at_least": 12, "item": "wheat"}},
}


def test_the_run_28_commitment_is_rebuilt_as_one():
    """Wren's commitment exactly as run 28's checkpoint stored it."""
    restored = _rehydrated("continuing", {"n06": dict(WREN)})["n06"]

    assert isinstance(restored, ContinuingAction), type(restored)
    assert restored.owner == "n06"
    assert restored.until == WREN["until"]


@pytest.mark.asyncio
async def test_a_commitment_survives_a_real_resume(tmp_path: Path):
    """Through the checkpoint the runner writes and the boot that reads it."""
    def runner() -> GenesisRunner:
        return GenesisRunner(GenesisRunnerConfig(
            mock_mode=True, checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded", world_port=0, budget_cap_cents=40_000,
        ))

    first = runner()
    await first.boot(resume=False)
    who = next(iter(first.world_state.agents))
    first.world_state.continuing[who] = ContinuingAction(
        tool="harvest", params={"crop": "wheat"},
        until={"item_count": {"at_least": 12, "item": "wheat"}},
        started_tick=first.world_state.current_tick, location_id="farmland:wheat", owner=who,
    )
    await first.shutdown()

    second = runner()
    await second.boot(resume=True)

    restored = second.world_state.continuing.get(who)
    assert isinstance(restored, ContinuingAction), (
        f"the commitment came back as {type(restored).__name__}, and the first "
        "turn that asks whether it is done will die on it"
    )
    assert restored.satisfied(second.world_state) is None, "holding no wheat, it is not done"
    await second.shutdown()
