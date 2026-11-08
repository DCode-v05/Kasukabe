"""A resident must be told where it is standing.

Every perception this project has ever rendered opened with the clock and
went straight to who was present:

    It is game-day 0, 06:10 (day).
    With you, and nobody else: Sebastian Ellery, Dashiell Ellery.

The place was never named. Not once, in any of the 23 runs.

Every location gate in the town is a rule about a fact the resident could
not see: `craft` needs a workshop, `pray` needs the temple, `buy` needs a
shop's premises. A refusal for standing in the wrong place was therefore
unanswerable - the resident could not know where it stood, so moving was a
guess and staying put was the safer play. `Perception.location_id` has
carried the answer since the model was written, and nothing rendered it.
"""
from __future__ import annotations

import pytest

from world.retrieval_depth import format_ambient


class _Ambient:
    game_day = 0
    hour = 6
    minute = 10
    is_daytime = True


class _P:
    ambient = _Ambient()

    def __init__(self, location_id):
        self.location_id = location_id


def test_the_place_is_named():
    line = format_ambient(_P("Kasukabe Temple"))
    assert "Kasukabe Temple" in line, (
        "the resident is not told where it is, so every location gate in the "
        "town is a rule about a fact it cannot see"
    )


def test_the_clock_is_still_there():
    line = format_ambient(_P("Kasukabe Temple"))
    assert "game-day 0" in line and "06:10" in line


def test_a_home_reads_as_a_home_and_keeps_its_id():
    """The id is what `move` accepts, so it stays in the sentence - but it
    is read by a person, not a parser."""
    line = format_ambient(_P("home:h0043"))
    assert "home (home:h0043)" in line


def test_a_resident_nowhere_is_not_told_a_lie():
    line = format_ambient(_P(None))
    assert "You are at" not in line
    assert "game-day 0" in line


@pytest.mark.asyncio
async def test_a_real_perception_names_the_place(tmp_path):
    """End to end, through the renderer a real turn uses."""
    from world.retrieval_depth import render_perception_text
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000, start_at_dawn=True,
    ))
    await runner.boot()
    result = await runner.tick_engine.tick()
    assert result.due_agents, "nobody was due; this test proves nothing"

    agent_id = result.due_agents[0]
    text = render_perception_text(
        result.perceptions.get(agent_id),
        depth=runner.depth,
        visible_agent_bios=runner.agent_bios,
        visible_agent_names=runner.agent_names,
    )
    where = runner.world_state.location_of(agent_id)
    assert "You are at" in text, f"no place in the perception:\n{text[:300]}"
    assert where and where in text, (
        f"the perception does not name {where!r}, so the resident cannot "
        f"reason about where it is:\n{text[:300]}"
    )
