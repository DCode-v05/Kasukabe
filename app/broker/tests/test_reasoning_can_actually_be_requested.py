"""Reasoning can be asked for, and asking is a decision somebody takes.

`INFRA-06`. Build Spec threshold 9.5 wants reasoning captured on 100% of
turns. Every piece needed to capture it was already built and already
tested: `translate.py` turns Anthropic's `thinking` into Bedrock's
`reasoning_config`, and carries `reasoningContent` back on both the
streaming and non-streaming paths (`test_stream_fidelity.py`,
`test_reasoning_request.py`).

What was missing is that nobody ever asked. `MessagesRequest.thinking`
only arrives if herald sets it, and herald sets it only for
`super_thinking` or a latched session - tiers this sim never resolves. So
across 23 runs, "the model did not reason" and "we never requested
reasoning" were the same observation. Run 23's session logs show Kimi
emitting `</think>` inside `text`: the model reasons in band, and the
record cannot separate reasoning from speech.

THIS DOES NOT DECIDE F1, and it must not look like it did. The review's
fix is "decide F1", and that decision costs a live call against a model
nobody has established honours `reasoning_config` at all. What this adds
is the knob the review's second option names -
`KASUKABE_REQUEST_REASONING` - defaulting OFF, so the decision can be
taken deliberately and reversed with an environment variable. Leaving it
off keeps every run so far comparable with the next one.

The review is also explicit that this option "couples routing policy to
research settings". It does. That is the price of the broker-only path,
and it is the cheaper price than a seam wrapper that cannot be turned off
without a redeploy.
"""
from __future__ import annotations

from typing import Any

import pytest

from broker.config import BrokerSettings
from broker.translate import reasoning_config_from_thinking


class _Req:
    """The two fields `thinking_for` reads off a MessagesRequest."""

    def __init__(self, extra: dict[str, Any] | None = None) -> None:
        self.model_extra = extra


def _service(**overrides):
    """A BrokerService with nothing but settings - `thinking_for` reads no
    other state, and building a real one needs Bedrock credentials."""
    from broker.service import BrokerService

    service = BrokerService.__new__(BrokerService)
    service.settings = BrokerSettings(**overrides)
    return service


def test_off_by_default() -> None:
    """Every run to date was made with no reasoning requested. A default
    that changed that would make run 24 incomparable with run 23 for a
    reason nobody chose."""
    assert BrokerSettings().request_reasoning is False


def test_with_the_knob_off_nothing_is_injected() -> None:
    service = _service()
    assert service.thinking_for(_Req(), "auto:agent_turn") is None


def test_with_the_knob_on_a_turn_asks_for_reasoning() -> None:
    service = _service(request_reasoning=True)
    asked = service.thinking_for(_Req(), "auto:agent_turn")
    assert asked == {"type": "enabled", "budget_tokens": 2048}


def test_housekeeping_still_does_not_reason() -> None:
    """A summariser that reasons pays thinking tokens to compress text,
    and 9.5 asks about the agent's turn."""
    service = _service(request_reasoning=True)
    assert service.thinking_for(_Req(), "auto:summarise") is None
    assert service.thinking_for(_Req(), "auto:learning") is None


def test_the_budget_is_configurable() -> None:
    service = _service(request_reasoning=True, request_reasoning_budget_tokens=512)
    assert service.thinking_for(_Req(), "auto:agent_turn")["budget_tokens"] == 512


def test_an_explicit_request_is_not_overruled() -> None:
    """A client that asked for a specific budget is not given the
    operator's."""
    service = _service(request_reasoning=True, request_reasoning_budget_tokens=2048)
    explicit = {"type": "enabled", "budget_tokens": 99}
    assert service.thinking_for(_Req({"thinking": explicit}), "auto:agent_turn") is explicit


def test_an_explicit_disable_is_not_quietly_re_enabled() -> None:
    """The one that matters. A caller that has decided this turn must not
    reason - because it is retrying after a failure, say - has that
    decision honoured over the operator default."""
    service = _service(request_reasoning=True)
    disabled = {"type": "disabled"}
    assert service.thinking_for(_Req({"thinking": disabled}), "auto:agent_turn") == disabled


def test_what_is_injected_survives_translation() -> None:
    """The knob is worth nothing if `reasoning_config_from_thinking` drops
    what it produces - which it would for a malformed shape, silently, by
    design."""
    service = _service(request_reasoning=True)
    asked = service.thinking_for(_Req(), "auto:agent_turn")
    config = reasoning_config_from_thinking(asked, max_tokens=8192)
    assert config is not None, (
        "INFRA-06: the broker injects a `thinking` value that translate.py "
        "then drops, so the knob turns on and nothing reaches Bedrock"
    )
    assert config["type"] == "enabled"
    assert config["budget_tokens"] > 0


def test_the_budget_is_still_capped_against_max_tokens() -> None:
    """One place decides the final budget. If `thinking_for` also capped,
    the two would drift and the smaller would win at random."""
    service = _service(request_reasoning=True, request_reasoning_budget_tokens=1_000_000)
    asked = service.thinking_for(_Req(), "auto:agent_turn")
    assert asked["budget_tokens"] == 1_000_000
    config = reasoning_config_from_thinking(asked, max_tokens=1000)
    assert config["budget_tokens"] < 1000


def test_the_dispatch_path_calls_it() -> None:
    """A method with no caller is this review's most common defect. The
    injection point used to read `model_extra` inline."""
    import inspect

    from broker import service as module

    source = inspect.getsource(module)
    assert "thinking=self.thinking_for(req, purpose)" in source, (
        "INFRA-06: thinking_for is not on the dispatch path, so the knob "
        "does nothing whatever it is set to"
    )


def test_the_env_var_is_the_one_the_review_names() -> None:
    settings, errors = BrokerSettings.build_from_env(
        {"KASUKABE_REQUEST_REASONING": "true"}
    )
    assert not errors
    assert settings.request_reasoning is True


def test_a_junk_value_is_an_error_not_a_shrug() -> None:
    """An operator who typed something wrong is told. Falling back to off
    would leave them believing a run captured reasoning when it did not -
    which is the exact confusion this finding is about."""
    _, errors = BrokerSettings.build_from_env(
        {"KASUKABE_REQUEST_REASONING": "yes-please"}
    )
    assert errors and any("KASUKABE_REQUEST_REASONING" in e for e in errors)


def test_f1_is_recorded_as_open_where_the_knob_is() -> None:
    """The decision is not taken here and the next reader must know that.
    A knob with no note reads as a feature somebody finished."""
    import inspect

    from broker import config

    source = inspect.getsource(config)
    start = source.index("request_reasoning: bool")
    note = source[max(0, start - 2200):start]
    assert "INFRA-06" in note
    assert "decide F1" in note or "F1" in note
    assert "OFF by default" in note
