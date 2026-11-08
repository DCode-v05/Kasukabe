"""LanePool + CircuitBreaker: the six-lane routing pool the broker-lanes
track builds - see broker/lanes.py's module docstring for the routing
policy (Kimi-preferred cognition, DeepSeek-only background, overflow on
saturation) and the CircuitBreaker's closed/open/half-open state machine
this exercises directly, independent of the full request pipeline
(broker/tests/test_service.py covers that end to end)."""

from __future__ import annotations

import pytest

from broker.config import (
    BEDROCK_LANE_REGIONS,
    DEEPSEEK_MODEL_ID,
    KIMI_MODEL_ID,
    BEDROCK_MODELS,
    QWEN_MODEL_ID,
    BrokerSettings,
)
from broker.errors import UpstreamOverloadedError
from broker.lanes import (
    MODEL_PREFERENCE_ORDER,
    CallClass,
    CircuitBreaker,
    build_lane_pool,
    classify_purpose,
    lane_id,
)


class FakeClock:
    """Monotonic clock under test control: advances only when told to -
    matches broker/tests/test_rate_limit.py's own FakeClock pattern."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def fake_sleep_noop(seconds: float) -> None:
    """LanePool.select never awaits (its own docstring), so none of these
    tests ever call bucket.acquire() - this exists only to satisfy
    build_lane_pool's `sleep` parameter."""


class RecordingGateway:
    """Minimal BedrockGateway stand-in. LanePool.select() never touches
    a lane's gateway (only the caller does, after select() returns), so
    these tests never need a real converse()/converse_stream()."""


def build_pool(
    rpm_per_lane: int = 100,
    circuit_threshold: int = 3,
    circuit_cooldown_s: float = 30.0,
    clock: "FakeClock | None" = None,
):
    clock = clock or FakeClock()
    settings = BrokerSettings(
        mock_mode=True,
        rpm_per_lane=rpm_per_lane,
        circuit_breaker_threshold=circuit_threshold,
        circuit_breaker_cooldown_s=circuit_cooldown_s,
    )
    pool = build_lane_pool(
        settings,
        gateway_factory=lambda model, region: RecordingGateway(),
        clock=clock,
        sleep=fake_sleep_noop,
    )
    return pool, clock


@pytest.mark.parametrize(
    "purpose",
    [
        "learning_centre",
        "learning_centre.review",
        "dream_probe",
        "dream_probe.sleep_tick_3",
        "importance_scoring",
        "importance_scoring.tick",
        "summarizer",
        "summarizer.digest",
    ],
)
def test_classify_purpose_recognizes_every_background_prefix(purpose: str) -> None:
    assert classify_purpose(purpose) is CallClass.BACKGROUND


def test_classify_purpose_is_case_insensitive() -> None:
    assert classify_purpose("DREAM_PROBE.NIGHT") is CallClass.BACKGROUND


@pytest.mark.parametrize("purpose", ["unspecified", "", "reflection", "conversation", "learning"])
def test_classify_purpose_defaults_to_cognition(purpose: str) -> None:
    assert classify_purpose(purpose) is CallClass.COGNITION


def test_classify_purpose_requires_a_true_prefix_not_any_substring() -> None:
    assert classify_purpose("not_a_dream_probe") is CallClass.COGNITION


def test_lane_id_format() -> None:
    assert lane_id("moonshotai.kimi-k2.5", "ap-south-1") == "moonshotai.kimi-k2.5@ap-south-1"


def test_build_lane_pool_builds_one_lane_per_model_per_region() -> None:
    """Derived, not counted: the invariant is the CROSS PRODUCT, and
    hard-coding 6 made adding a third model look like six failures."""
    pool, _ = build_pool()
    lanes = pool.all_lanes()
    expected = {
        lane_id(model, region)
        for model in BEDROCK_MODELS
        for region in BEDROCK_LANE_REGIONS
    }
    assert {lane.id for lane in lanes} == expected
    assert len(lanes) == len(BEDROCK_MODELS) * len(BEDROCK_LANE_REGIONS)


def test_each_lane_gets_its_own_bucket_and_circuit_instance() -> None:
    pool, _ = build_pool()
    lanes = pool.all_lanes()
    assert len({id(lane.bucket) for lane in lanes}) == len(lanes)
    assert len({id(lane.circuit) for lane in lanes}) == len(lanes)


def test_gateway_factory_is_called_exactly_once_per_lane() -> None:
    calls: list[tuple[str, str]] = []

    def gateway_factory(model: str, region: str) -> RecordingGateway:
        calls.append((model, region))
        return RecordingGateway()

    settings = BrokerSettings(mock_mode=True)
    build_lane_pool(settings, gateway_factory=gateway_factory)

    every_lane = len(BEDROCK_MODELS) * len(BEDROCK_LANE_REGIONS)
    assert len(calls) == every_lane
    assert len(set(calls)) == every_lane


def test_cognition_prefers_a_kimi_lane_when_every_lane_is_fresh() -> None:
    pool, _ = build_pool()
    lane = pool.select(CallClass.COGNITION)
    assert lane.model == KIMI_MODEL_ID


def test_background_only_ever_selects_deepseek_even_when_kimi_is_fully_free() -> None:
    pool, _ = build_pool()
    for _ in range(10):
        lane = pool.select(CallClass.BACKGROUND)
        assert lane.model == DEEPSEEK_MODEL_ID


def test_cognition_overflows_to_deepseek_once_every_kimi_lane_is_saturated() -> None:
    pool, _ = build_pool(rpm_per_lane=1)

    for _ in range(3):
        lane = pool.select(CallClass.COGNITION)
        assert lane.model == KIMI_MODEL_ID
        assert lane.bucket.peek_available()
        lane.bucket.tokens -= 1.0

    lane = pool.select(CallClass.COGNITION)
    assert lane.model == DEEPSEEK_MODEL_ID


def test_select_never_consumes_a_token_itself() -> None:
    pool, _ = build_pool()
    lane = pool.select(CallClass.COGNITION)
    assert lane.bucket.tokens == lane.bucket.capacity


def test_rotated_round_robins_across_calls_regardless_of_which_lane_dispatches() -> None:
    pool, _ = build_pool()
    first = pool.select(CallClass.COGNITION)
    second = pool.select(CallClass.COGNITION)
    third = pool.select(CallClass.COGNITION)
    fourth = pool.select(CallClass.COGNITION)

    assert {first.region, second.region, third.region} == set(BEDROCK_LANE_REGIONS)
    assert fourth.region == first.region


def test_select_raises_when_every_lane_for_the_call_class_is_circuit_open() -> None:
    """Exhaust the call class's WHOLE preference order, not one model.

    This opened only DeepSeek's lanes, which was the whole of BACKGROUND
    when DeepSeek was its only model. It now has Qwen behind it, so the
    old version stopped testing exhaustion and started testing fallback -
    and passed for the wrong reason until the fallback existed. Derived
    from MODEL_PREFERENCE_ORDER so a fourth model cannot hollow it out
    the same way.
    """
    pool, _ = build_pool(circuit_threshold=1)
    for model in MODEL_PREFERENCE_ORDER[CallClass.BACKGROUND]:
        for lane in pool.lanes_by_model[model]:
            lane.circuit.record_throttle()

    with pytest.raises(UpstreamOverloadedError):
        pool.select(CallClass.BACKGROUND)


def test_background_falls_back_to_qwen_before_it_gives_up() -> None:
    """The other half, and the reason the test above had to change: with
    DeepSeek dark, background work is served rather than refused."""
    pool, _ = build_pool(circuit_threshold=1)
    for lane in pool.lanes_by_model[DEEPSEEK_MODEL_ID]:
        lane.circuit.record_throttle()

    lane = pool.select(CallClass.BACKGROUND)
    assert lane.model == QWEN_MODEL_ID, (
        "background had a second model and still refused the call"
    )


def test_select_skips_an_open_circuit_and_keeps_using_the_others() -> None:
    pool, _ = build_pool(circuit_threshold=1)
    tripped = pool.lanes_by_model[DEEPSEEK_MODEL_ID][0]
    tripped.circuit.record_throttle()

    for _ in range(6):
        lane = pool.select(CallClass.BACKGROUND)
        assert lane.id != tripped.id


def test_lane_by_id_finds_a_known_lane_and_none_for_unknown() -> None:
    pool, _ = build_pool()
    lane = pool.lane_by_id(lane_id(KIMI_MODEL_ID, "ap-south-1"))
    assert lane is not None
    assert lane.model == KIMI_MODEL_ID
    assert lane.region == "ap-south-1"
    assert pool.lane_by_id("no-such-lane") is None


def test_pool_status_lists_every_lane_with_circuit_state() -> None:
    pool, _ = build_pool()
    rows = pool.status()
    assert len(rows) == len(BEDROCK_MODELS) * len(BEDROCK_LANE_REGIONS)
    assert all(row["circuit_state"] == "closed" for row in rows)
    assert {row["lane_id"] for row in rows} == {lane.id for lane in pool.all_lanes()}


def test_circuit_starts_closed_and_freely_reservable() -> None:
    breaker = CircuitBreaker(threshold=3, cooldown_s=30.0)
    assert breaker.is_open() is False
    assert breaker.try_reserve() is True


def test_circuit_opens_after_threshold_consecutive_throttles() -> None:
    breaker = CircuitBreaker(threshold=3, cooldown_s=30.0)
    breaker.record_throttle()
    breaker.record_throttle()
    assert breaker.is_open() is False
    breaker.record_throttle()
    assert breaker.is_open() is True
    assert breaker.trip_count == 1


def test_a_success_between_throttles_resets_the_consecutive_count() -> None:
    breaker = CircuitBreaker(threshold=3, cooldown_s=30.0)
    breaker.record_throttle()
    breaker.record_throttle()
    breaker.record_success()
    breaker.record_throttle()
    breaker.record_throttle()
    assert breaker.is_open() is False


def test_open_circuit_refuses_reservations_until_cooldown_elapses() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=30.0, clock=clock)
    breaker.record_throttle()
    assert breaker.try_reserve() is False

    clock.advance(29.9)
    assert breaker.try_reserve() is False

    clock.advance(0.2)
    assert breaker.try_reserve() is True


def test_half_open_trial_is_granted_exactly_once() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=30.0, clock=clock)
    breaker.record_throttle()
    clock.advance(30.0)

    assert breaker.try_reserve() is True
    assert breaker.try_reserve() is False


def test_half_open_trial_success_closes_the_circuit() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=30.0, clock=clock)
    breaker.record_throttle()
    clock.advance(30.0)
    breaker.try_reserve()

    breaker.record_success()

    assert breaker.is_open() is False
    assert breaker.consecutive_failures == 0
    assert breaker.try_reserve() is True


def test_half_open_trial_throttle_reopens_the_circuit_without_a_second_trip() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=30.0, clock=clock)
    breaker.record_throttle()
    clock.advance(30.0)
    breaker.try_reserve()

    breaker.record_throttle()

    assert breaker.is_open() is True
    assert breaker.trip_count == 1


def test_release_unused_reservation_is_a_no_op_on_a_closed_circuit() -> None:
    breaker = CircuitBreaker(threshold=3, cooldown_s=30.0)
    assert breaker.try_reserve() is True
    breaker.release_unused_reservation()
    assert breaker.try_reserve() is True


def test_release_unused_reservation_frees_an_unclaimed_half_open_trial() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=30.0, clock=clock)
    breaker.record_throttle()
    clock.advance(30.0)

    assert breaker.try_reserve() is True
    breaker.release_unused_reservation()
    assert breaker.try_reserve() is True


def test_other_failure_releases_the_trial_without_extending_the_cooldown() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=30.0, clock=clock)
    breaker.record_throttle()
    clock.advance(30.0)
    breaker.try_reserve()

    breaker.record_other_failure()

    assert breaker.consecutive_failures == 1
    assert breaker.try_reserve() is True


def test_status_reports_open_then_half_open_as_the_clock_advances() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=30.0, clock=clock)
    breaker.record_throttle()
    assert breaker.status()["circuit_state"] == "open"
    clock.advance(30.0)
    assert breaker.status()["circuit_state"] == "half_open"
