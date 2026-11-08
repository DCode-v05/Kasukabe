"""The event ring is how speech is delivered, so it has to fit the town.

`CORE-09`. `EventBus.buffer` is a 2,000-entry deque, and it is not a
debugging convenience: `PerceptionSystem.build_perception` reads
`recent()` to decide what was said to a resident and never delivered - with
no age limit - and what is still unanswered. Anything that falls off the
back was, as far as the town is concerned, never said.

Walking floods it. `MovementSystem.advance` emits one `agent.moved` PER
TILE CROSSED, up to forty per resident per tick, so twenty residents can
produce 800 events a tick from walking alone. The whole ring turns over in
under three ticks - and a resident is due every third tick, so a remark
could expire before its addressee was ever offered it.

Sized off the population, never below the old default so small fixtures
behave exactly as before.
"""
from __future__ import annotations

import pytest

from world.core.events import EventBus
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


class TestSizing:
    def test_the_default_is_unchanged_for_a_small_town(self):
        bus = EventBus()
        bus.size_for_population(3)
        assert bus.buffer.maxlen == EventBus.DEFAULT_BUFFER

    def test_it_grows_for_a_big_one(self):
        bus = EventBus()
        bus.size_for_population(250)
        assert bus.buffer.maxlen == 250 * 200

    def test_it_never_shrinks(self):
        bus = EventBus(buffer_size=99_000)
        bus.size_for_population(10)
        assert bus.buffer.maxlen == 99_000

    def test_resizing_keeps_what_was_already_said(self):
        """A resize that dropped the buffer would lose exactly the remarks
        this finding is about."""
        from world.core.events import WorldEvent

        bus = EventBus()
        bus.buffer.append(WorldEvent(type="agent.utterance.spoken", tick=1, payload={}))
        bus.size_for_population(250)
        assert len(bus.buffer) == 1


@pytest.mark.asyncio
async def test_the_runner_sizes_it_at_boot(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))
    await runner.boot()

    population = len(runner.world_state.agents)
    assert runner.world_state.event_bus.buffer.maxlen >= max(
        EventBus.DEFAULT_BUFFER, population * 200
    ), "CORE-09: the ring was never sized to the population that boots into it"
