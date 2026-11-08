"""Preferring a model is not the same as restricting to it, and the
difference is a bill.

`COGNITION_FIRST_MODEL` moves a model to the front of the order and leaves the
rest of it behind - so a peak still overflows to Bedrock - and it only touches
COGNITION. Every BACKGROUND call (learning centre, dream probes, importance
scoring, summarizer) goes to Bedrock whatever is preferred. On trial-day2-20,
with Kimi as the chosen model, deepseek still took 236 calls and $1.43 of the
$20.16.

A run paid for on one provider's key needs the other thing: one model, every
class, no overflow. That is ONLY_MODEL, and the case that matters most is the
one where it CANNOT be served - because falling back quietly is the entire
behaviour it exists to prevent.
"""
from __future__ import annotations

import dataclasses

import pytest

from broker.config import (
    DEEPSEEK_MODEL_ID,
    KIMI_MODEL_ID,
    OPENAI_LUNA6_MODEL_ID,
    BrokerSettings,
)
from broker.lanes import CallClass, LanePool, build_lane_pool


def _settings(**kw) -> BrokerSettings:
    base = BrokerSettings(openai_api_key="sk-test")
    return dataclasses.replace(base, **kw)


def _pool(**kw) -> LanePool:
    return build_lane_pool(_settings(**kw), lambda model, region: object())


def test_one_model_and_nothing_behind_it() -> None:
    pool = _pool(only_model=OPENAI_LUNA6_MODEL_ID)

    assert pool.preference_for(CallClass.COGNITION) == (OPENAI_LUNA6_MODEL_ID,)


def test_background_calls_are_restricted_too() -> None:
    """The half `cognition_first` never touched. Background work is a small
    share of the calls and a real share of the money, and it went to Bedrock
    every time."""
    pool = _pool(only_model=OPENAI_LUNA6_MODEL_ID)

    order = pool.preference_for(CallClass.BACKGROUND)
    assert order == (OPENAI_LUNA6_MODEL_ID,)
    assert KIMI_MODEL_ID not in order and DEEPSEEK_MODEL_ID not in order


@pytest.mark.parametrize("call_class", list(CallClass))
def test_no_bedrock_model_survives_anywhere_in_the_order(call_class: CallClass) -> None:
    """The property the operator actually asked for, asserted over every
    class rather than the one that happened to be on my mind."""
    pool = _pool(only_model=OPENAI_LUNA6_MODEL_ID)

    assert pool.preference_for(call_class) == (OPENAI_LUNA6_MODEL_ID,)


def test_a_model_that_cannot_be_served_refuses_to_boot() -> None:
    """No key means no OpenAI lane. Booting anyway would serve the whole run
    from Bedrock - the exact bill ONLY_MODEL was set to avoid - and it would
    be discovered as an invoice."""
    with pytest.raises(ValueError) as caught:
        build_lane_pool(
            _settings(openai_api_key="", only_model=OPENAI_LUNA6_MODEL_ID),
            lambda model, region: object(),
        )

    message = str(caught.value)
    assert "ONLY_MODEL" in message and "no lanes" in message
    assert "OPENAI_API_KEY" in message, "the refusal does not name the likely cause"


def test_the_refusal_is_not_silent_about_why_it_refuses() -> None:
    with pytest.raises(ValueError) as caught:
        build_lane_pool(
            _settings(openai_api_key="", only_model=OPENAI_LUNA6_MODEL_ID),
            lambda model, region: object(),
        )

    assert "falling back" in str(caught.value).lower()


def test_an_unset_only_model_changes_nothing() -> None:
    """Every run that does not ask for this keeps its overflow."""
    pool = _pool()

    cognition = pool.preference_for(CallClass.COGNITION)
    assert cognition[0] == KIMI_MODEL_ID
    assert len(cognition) > 1, "overflow disappeared for runs that never asked"


def test_it_still_only_allows_models_this_broker_may_spend_on() -> None:
    """A typo must not become "serve nothing". ONLY_MODEL is read from the
    same allowlist as everything else, and an unknown name is a parse error
    rather than a lane that never matches."""
    _, errors = BrokerSettings.build_from_env({"ONLY_MODEL": "gpt-6-lunaa"})

    assert any("ONLY_MODEL" in e and "MODEL_ALLOWLIST" in e for e in errors), errors


def test_an_allowlisted_model_is_accepted_from_the_environment() -> None:
    settings, errors = BrokerSettings.build_from_env({"ONLY_MODEL": OPENAI_LUNA6_MODEL_ID})

    assert not [e for e in errors if "ONLY_MODEL" in e]
    assert settings.only_model == OPENAI_LUNA6_MODEL_ID
