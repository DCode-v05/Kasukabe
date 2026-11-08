"""Asking the model to reason, not just carrying the answer back.

Build Spec v2 objective 3.2 and threshold 9.5, and the second half of
defect D2.

The first half is fixed: broker's streaming translator now carries a
`reasoningContent` block back as a `thinking` block, and the accumulator
records it. But measured across 6,000 successful calls, **zero requests
ever asked for reasoning**, and `build_converse_request` has no branch that
could: it forwards temperature, top_p, top_k, stop sequences, tools and
tool choice, and drops `thinking` on the floor.

So the carry path had nothing to carry. "The model did not reason" and "we
never asked" have looked identical in every run this project has made, and
threshold 9.5 requires them to be distinguishable.

WHAT THIS DOES NOT CLAIM. Wiring the request through is not the same as
the model honouring it. Kimi K2.5 on Bedrock may or may not support a
reasoning config, and this file cannot answer that - only a live call can,
and the answer belongs in a run report rather than in an assertion here.
What these tests fix is the thing that made the question unanswerable:
until now the request could not have been made even if the model wanted it.
"""

from __future__ import annotations

from broker.translate import build_converse_request

MESSAGES = [{"role": "user", "content": "It is game-day 0, 06:20."}]


def build(**kwargs):
    return build_converse_request(
        model="moonshotai.kimi-k2.5",
        messages=MESSAGES,
        system=None,
        max_tokens=4096,
        **kwargs,
    )


def test_no_thinking_field_means_no_reasoning_config() -> None:
    """The default must not change. Every existing caller sends no
    thinking field and must produce a byte-identical request."""
    request = build()
    assert "reasoning_config" not in (request.get("additionalModelRequestFields") or {})


def test_enabled_thinking_becomes_a_bedrock_reasoning_config() -> None:
    request = build(thinking={"type": "enabled", "budget_tokens": 2000})
    fields = request["additionalModelRequestFields"]
    assert fields["reasoning_config"] == {"type": "enabled", "budget_tokens": 2000}


def test_thinking_and_top_k_do_not_overwrite_each_other() -> None:
    """`top_k` already owns additionalModelRequestFields and assigns a
    fresh dict to it. Adding a second writer that also assigns would make
    whichever ran last silently delete the other, and the loser would be
    invisible: a request missing top_k looks exactly like one that never
    asked for it.
    """
    request = build(top_k=40, thinking={"type": "enabled", "budget_tokens": 1024})
    fields = request["additionalModelRequestFields"]
    assert fields["top_k"] == 40
    assert fields["reasoning_config"]["budget_tokens"] == 1024


def test_disabled_thinking_sends_no_reasoning_config() -> None:
    """An explicit "off" is a request not to reason, not a request to
    reason with a zero budget."""
    request = build(thinking={"type": "disabled"})
    assert "reasoning_config" not in (request.get("additionalModelRequestFields") or {})


def test_a_malformed_thinking_field_is_ignored_rather_than_forwarded() -> None:
    """Bedrock rejects the whole call on an unrecognised model field, so a
    junk value here would turn a bad parameter into a dead turn. Dropping
    it costs one un-reasoned turn instead."""
    for bad in ("enabled", 7, [], {"type": "wat"}, {}, None):
        request = build(thinking=bad)
        assert "reasoning_config" not in (request.get("additionalModelRequestFields") or {})


def test_a_missing_budget_gets_a_default_rather_than_being_dropped() -> None:
    """`{"type": "enabled"}` is a well-formed Anthropic thinking config.
    Refusing it because it omits an optional field would silently disable
    reasoning for a caller that correctly asked for it."""
    request = build(thinking={"type": "enabled"})
    config = request["additionalModelRequestFields"]["reasoning_config"]
    assert config["type"] == "enabled"
    assert isinstance(config.get("budget_tokens"), int)
    assert config["budget_tokens"] > 0


def test_the_reasoning_budget_stays_under_max_tokens() -> None:
    """Bedrock rejects a reasoning budget that meets or exceeds maxTokens:
    the model would have no room left to answer. A caller asking for more
    thinking than the whole response allows is clamped rather than
    refused, because the turn is worth more than the setting.
    """
    request = build_converse_request(
        model="moonshotai.kimi-k2.5",
        messages=MESSAGES,
        system=None,
        max_tokens=1000,
        thinking={"type": "enabled", "budget_tokens": 8000},
    )
    budget = request["additionalModelRequestFields"]["reasoning_config"]["budget_tokens"]
    assert budget < 1000
