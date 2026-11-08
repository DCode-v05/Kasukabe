"""Tests for world.core.scheduler - cadence derivation and due-agent selection."""

from __future__ import annotations

import pytest

from world.core.scheduler import AgentScheduler, SchedulerConfig


@pytest.mark.parametrize(
    "target_fraction,expected_cadence",
    [
        (1.0, 1),
        (0.5, 2),
        (1 / 3, 3),
        (0.3, 3),
        (0.25, 4),
        (0.2, 5),
    ],
)
def test_cadence_derived_from_target_fraction(
    target_fraction: float, expected_cadence: int
) -> None:
    config = SchedulerConfig(target_fraction=target_fraction)
    assert config.cadence_ticks == expected_cadence


def test_min_cadence_ticks_floors_cadence() -> None:
    config = SchedulerConfig(target_fraction=0.9, min_cadence_ticks=2)
    assert config.cadence_ticks == 2


@pytest.mark.parametrize("bad_fraction", [0.0, -0.1, 1.1])
def test_config_rejects_out_of_range_target_fraction(bad_fraction: float) -> None:
    with pytest.raises(ValueError):
        SchedulerConfig(target_fraction=bad_fraction)


def test_config_rejects_non_positive_min_cadence() -> None:
    with pytest.raises(ValueError):
        SchedulerConfig(min_cadence_ticks=0)


def test_register_duplicate_agent_raises() -> None:
    scheduler = AgentScheduler()
    scheduler.register_agent("a1", current_tick=0)
    with pytest.raises(ValueError):
        scheduler.register_agent("a1", current_tick=0)


def test_unregister_unknown_agent_is_noop() -> None:
    scheduler = AgentScheduler()
    scheduler.unregister_agent("ghost")


def test_mark_decided_unknown_agent_raises_keyerror() -> None:
    scheduler = AgentScheduler()
    with pytest.raises(KeyError):
        scheduler.mark_decided("ghost", current_tick=1)


def test_interrupt_unknown_agent_is_silently_ignored() -> None:
    scheduler = AgentScheduler()
    scheduler.interrupt("ghost")
    assert scheduler.agent_count == 0


def test_no_agent_is_due_before_its_first_offset() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 3))
    scheduler.register_agent("a0", current_tick=0)
    scheduler.register_agent("a1", current_tick=0)
    scheduler.register_agent("a2", current_tick=0)

    assert scheduler.due_agents(0) == []


def test_round_robin_spreads_agents_across_the_full_cadence_cycle() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 3))
    for i in range(9):
        scheduler.register_agent(f"a{i}", current_tick=0)

    seen: set[str] = set()
    for tick in range(1, 4):
        due = scheduler.due_agents(tick)
        assert len(due) == 3
        assert seen.isdisjoint(due)
        seen.update(due)
        for agent_id in due:
            scheduler.mark_decided(agent_id, tick)

    assert seen == {f"a{i}" for i in range(9)}


def test_schedule_is_stable_across_many_cadence_cycles() -> None:
    """Once phased in, the same tick-of-cycle always has the same due set
    (this is the bug this design specifically guards against: buckets
    merging together after the first cycle)."""
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 3))
    for i in range(9):
        scheduler.register_agent(f"a{i}", current_tick=0)

    due_by_phase: dict[int, set[str]] = {}
    for tick in range(1, 19):
        due = set(scheduler.due_agents(tick))
        for agent_id in due:
            scheduler.mark_decided(agent_id, tick)
        phase = (tick - 1) % 3
        if phase in due_by_phase:
            assert due_by_phase[phase] == due, f"tick {tick} (phase {phase}) drifted"
        else:
            due_by_phase[phase] = due
        assert len(due) == 3


def test_average_due_fraction_at_250_agents_matches_target() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=0.3))
    for i in range(250):
        scheduler.register_agent(f"a{i}", current_tick=0)

    counts = []
    for tick in range(1, 13):
        due = scheduler.due_agents(tick)
        counts.append(len(due))
        for agent_id in due:
            scheduler.mark_decided(agent_id, tick)

    average_fraction = sum(counts) / len(counts) / 250
    assert average_fraction == pytest.approx(1 / 3, abs=0.01)
    for count in counts:
        assert abs(count - 250 / 3) <= 1


def test_interrupted_agent_is_due_even_when_not_scheduled() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 3))
    scheduler.register_agent("a0", current_tick=0)

    assert scheduler.due_agents(0) == []
    scheduler.interrupt("a0")
    assert scheduler.due_agents(0) == ["a0"]


def test_mark_decided_clears_interrupt_and_reschedules_from_current_tick() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 3))
    scheduler.register_agent("a0", current_tick=0)

    scheduler.interrupt("a0")
    scheduler.mark_decided("a0", current_tick=5)

    assert scheduler.due_agents(5) == []
    assert scheduler.due_agents(8) == ["a0"]


def test_late_registration_still_gets_a_future_offset() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 3))
    scheduler.register_agent("late", current_tick=50)

    assert scheduler.due_agents(50) == []
    assert scheduler.due_agents(51) == ["late"]


def test_explicit_cadence_override_bypasses_config() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=0.3))
    scheduler.register_agent("slow", current_tick=0, cadence_ticks=10)

    entry = scheduler.entries["slow"]
    assert entry.cadence_ticks == 10


def test_register_agent_rejects_cadence_below_one() -> None:
    scheduler = AgentScheduler()
    with pytest.raises(ValueError):
        scheduler.register_agent("a0", current_tick=0, cadence_ticks=0)


def test_snapshot_restore_round_trip_preserves_due_agents() -> None:
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 3))
    for i in range(6):
        scheduler.register_agent(f"a{i}", current_tick=0)
    scheduler.interrupt("a4")
    for agent_id in scheduler.due_agents(1):
        scheduler.mark_decided(agent_id, 1)

    data = scheduler.snapshot()

    restored = AgentScheduler()
    restored.restore(data)

    assert restored.agent_count == scheduler.agent_count
    for tick in range(1, 8):
        assert restored.due_agents(tick) == scheduler.due_agents(tick)
