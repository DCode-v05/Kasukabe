"""The overflow lane may be absent; the two the routing policy is built on may not.

d1af582 added Qwen3 Coder 480B as a third lane, to lift ap-south-1 from 200
to 300 RPM at 50 residents. Its inference profile has to be provisioned by
an account admin, and until one is, `build_lane_pool` raised "no inference
profile for any region of: qwen.qwen3-coder-480b" - so the broker would not
boot at all, even for a 20-resident run that two lanes serve comfortably
(run 29: 20 residents, Kimi only).

Now a model in `OVERFLOW_ONLY_MODELS` may have no lanes. It is left out of
routing and the boot says so, with the capacity that leaves. Kimi and
DeepSeek are still required: `test_a_model_with_no_lanes_at_all_is_a_hard_
error` in test_inference_profiles.py still holds.
"""

from __future__ import annotations

import logging

import pytest

from broker.config import (
    BEDROCK_LANE_REGIONS,
    DEEPSEEK_MODEL_ID,
    KIMI_MODEL_ID,
    QWEN_MODEL_ID,
    BrokerSettings,
    lane_key,
)
from broker.lanes import OVERFLOW_ONLY_MODELS, CallClass, build_lane_pool

ARN = "arn:aws:bedrock:{region}:123456789012:application-inference-profile/{slug}"
REGION = BEDROCK_LANE_REGIONS[0]


def _settings(*models: str) -> BrokerSettings:
    return BrokerSettings(
        mock_mode=False,
        world_base_url="http://w",
        trace_sqlite_path="/tmp/k.db",
        bedrock_inference_profiles={
            lane_key(model, REGION): ARN.format(region=REGION, slug=str(i))
            for i, model in enumerate(models)
        },
    )


def test_qwen_is_the_only_overflow_only_model() -> None:
    assert OVERFLOW_ONLY_MODELS == (QWEN_MODEL_ID,)


def test_a_deployment_without_qwen_boots_and_says_so(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="broker.lanes"):
        pool = build_lane_pool(_settings(KIMI_MODEL_ID, DEEPSEEK_MODEL_ID), lambda m, r: object())

    assert {lane.model for lane in pool.all_lanes()} == {KIMI_MODEL_ID, DEEPSEEK_MODEL_ID}
    said = " ".join(r.getMessage() for r in caplog.records)
    assert QWEN_MODEL_ID in said and "will NOT be routed to" in said and "RPM" in said


def test_routing_moves_past_the_absent_lane() -> None:
    pool = build_lane_pool(_settings(KIMI_MODEL_ID, DEEPSEEK_MODEL_ID), lambda m, r: object())

    assert pool.select(CallClass.COGNITION).model == KIMI_MODEL_ID
    assert pool.select(CallClass.BACKGROUND).model == DEEPSEEK_MODEL_ID
    assert pool.rotated(QWEN_MODEL_ID) == []


def test_cognition_still_overflows_to_deepseek_when_kimi_is_open_circuit() -> None:
    pool = build_lane_pool(_settings(KIMI_MODEL_ID, DEEPSEEK_MODEL_ID), lambda m, r: object())
    for lane in pool.lanes_by_model[KIMI_MODEL_ID]:
        lane.circuit.try_reserve = lambda: False

    assert pool.select(CallClass.COGNITION).model == DEEPSEEK_MODEL_ID


def test_deepseek_is_still_required() -> None:
    with pytest.raises(ValueError, match="cannot be served at all"):
        build_lane_pool(_settings(KIMI_MODEL_ID, QWEN_MODEL_ID), lambda m, r: object())


def test_with_a_qwen_profile_the_third_tier_is_back() -> None:
    pool = build_lane_pool(
        _settings(KIMI_MODEL_ID, DEEPSEEK_MODEL_ID, QWEN_MODEL_ID), lambda m, r: object()
    )

    assert {lane.model for lane in pool.all_lanes()} == {KIMI_MODEL_ID, DEEPSEEK_MODEL_ID, QWEN_MODEL_ID}
