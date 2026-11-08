"""An alert the console raised is still there after the ring forgets.

`UI-32`. `/admin/alerts` recomputed the entire feed from
`event_bus.recent()` on every 8-second poll. That ring holds 2,000 events
- `agents * 200` since CORE-09, so 50,000 at the 250-resident target - and
nothing else remembered an alert had ever been raised. A regime change at
tick 5 simply vanished from the operator's console once enough later
events pushed it out. An alert that disappears is worse than one never
raised: the operator cannot tell "nothing happened" from "it scrolled
off".

Same finding, second half: CRIME_SPIKE counted every `crime.case.opened`
in the whole buffer and fired at five. Over 50,000 events that is not a
spike, it is a total - a normal town crosses five cases once and the
alarm then stays lit for the rest of the run, which is the same as having
no alarm.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from world.admin_api import (
    CRIME_SPIKE_THRESHOLD,
    CRIME_SPIKE_WINDOW_TICKS,
    AdminExtras,
)
from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)

HEADERS = {"X-Operator-Token": "t"}


async def _runner_and_app(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service,
        admin_extras=AdminExtras(operator_token="t"),
    )
    return runner, app


def _alerts(client) -> list[dict]:
    response = client.get("/admin/alerts", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_a_regime_change_survives_the_ring_overflowing(tmp_path):
    """The finding itself, at the scale it actually bites."""
    runner, app = await _runner_and_app(tmp_path)
    bus = runner.world_state.event_bus

    await bus.emit("politics.regime.changed", tick=5, payload={"to": "council"})

    with TestClient(app) as client:
        assert any(a["category"] == "regime_change" for a in _alerts(client)), (
            "the alert was never raised at all - this test proves nothing"
        )

        # Now bury it. `size_for_population` sized the ring for this cast;
        # filling past that evicts the regime event entirely.
        for i in range(bus.buffer.maxlen + 10):
            await bus.emit("agent.moved", tick=6, payload={"agent_id": f"a{i}"})
        assert not any(
            e.type == "politics.regime.changed" for e in bus.recent()
        ), "the ring did not actually forget it, so the test is not testing UI-32"

        after = _alerts(client)

    assert any(a["category"] == "regime_change" for a in after), (
        "UI-32: the regime change scrolled out of the event ring and the "
        "console's alert feed lost it with no trace that it had ever fired"
    )


@pytest.mark.asyncio
async def test_the_same_alert_is_not_shown_twice(tmp_path):
    """Keeping alerts must not mean accumulating duplicates on every poll -
    the console polls every 8 seconds."""
    runner, app = await _runner_and_app(tmp_path)
    await runner.world_state.event_bus.emit(
        "politics.charter.amendment_proposed", tick=3, payload={"title": "curfew"}
    )
    with TestClient(app) as client:
        for _ in range(5):
            alerts = _alerts(client)
    ids = [a["alert_id"] for a in alerts]
    assert len(ids) == len(set(ids)), f"UI-32: duplicated after five polls: {ids}"
    assert sum(1 for a in alerts if a["category"] == "amendment_tabled") == 1


@pytest.mark.asyncio
async def test_five_crimes_across_a_long_run_is_not_a_spike(tmp_path):
    """The unwindowed count latched on and never cleared."""
    runner, app = await _runner_and_app(tmp_path)
    bus = runner.world_state.event_bus
    for i in range(CRIME_SPIKE_THRESHOLD):
        await bus.emit("crime.case.opened", tick=i * 50, payload={"case_id": f"c{i}"})
    # The town has moved on well past all of them.
    runner.world_state.current_tick = 5_000

    with TestClient(app) as client:
        alerts = _alerts(client)

    assert not any(a["category"] == "crime_spike" for a in alerts), (
        f"UI-32: {CRIME_SPIKE_THRESHOLD} cases spread over 200+ ticks and "
        "thousands of ticks in the past still read as a spike, so the alarm "
        "latches on for the rest of the run"
    )


@pytest.mark.asyncio
async def test_five_crimes_in_one_window_is_a_spike(tmp_path):
    """The alarm must still work, or the fix is just a mute button."""
    runner, app = await _runner_and_app(tmp_path)
    bus = runner.world_state.event_bus
    runner.world_state.current_tick = 1_000
    for i in range(CRIME_SPIKE_THRESHOLD):
        await bus.emit("crime.case.opened", tick=990 + i, payload={"case_id": f"c{i}"})

    with TestClient(app) as client:
        alerts = _alerts(client)

    spikes = [a for a in alerts if a["category"] == "crime_spike"]
    assert spikes, "UI-32: a real spike no longer raises anything"
    assert spikes[0]["payload"]["window_ticks"] == CRIME_SPIKE_WINDOW_TICKS
    assert str(CRIME_SPIKE_WINDOW_TICKS) in spikes[0]["summary"], (
        'the summary said "recently", which meant nothing measurable'
    )


@pytest.mark.asyncio
async def test_the_history_is_bounded(tmp_path):
    """Remembering forever is its own bug - this is a live process."""
    from world.admin_api import MAX_RAISED_ALERTS

    runner, app = await _runner_and_app(tmp_path)
    bus = runner.world_state.event_bus
    with TestClient(app) as client:
        for i in range(MAX_RAISED_ALERTS + 50):
            await bus.emit("politics.regime.changed", tick=i, payload={"n": i})
        _alerts(client)

    assert len(runner.world_state.raised_alerts) <= MAX_RAISED_ALERTS


@pytest.mark.asyncio
async def test_the_newest_alerts_are_the_ones_kept(tmp_path):
    runner, app = await _runner_and_app(tmp_path)
    bus = runner.world_state.event_bus
    with TestClient(app) as client:
        for tick in (1, 900, 40):
            await bus.emit("politics.regime.changed", tick=tick, payload={"t": tick})
        alerts = _alerts(client)

    assert [a["raised_tick"] for a in alerts][:3] == [900, 40, 1]
