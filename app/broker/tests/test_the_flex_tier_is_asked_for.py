"""The broker priced every call at the Flex rate and never asked for it.

`DESIGN.md` section 3 and `broker/config.py` both say flex-tier prices -
Kimi at $0.36 per million input tokens - and `MODEL_PRICES_USD_PER_MILLION`
is what every cost figure this project has ever published was computed
from. But no request ever set Bedrock's `serviceTier`, and AWS routes a
request without one to **Standard**: $0.72 per million in ap-south-1,
where every run so far has gone.

So the recorded price was half the real one. Runs 27, 28 and 28b recorded
$5.00, $10.04 and $10.00 and cost about $8.98, $18.31 and $18.13 at list
prices - and every cost decision in `docs/decisions/NO-PROMPT-CACHE-COST.md`
was made against the smaller number.

This asks for the tier the prices assume, refuses a tier the API does not
have, and notices when Bedrock serves something other than what was asked
for - because a silent downgrade would put the project back where it
started, paying Standard while reporting Flex.
"""

from __future__ import annotations

import logging

import pytest

from broker.bedrock import MockBedrockGateway
from broker.config import BrokerSettings
from broker.lanes import build_lane_pool
from broker.ledger import InMemoryLedgerClient
from broker.pricing import CostAccountant
from broker.service import BrokerService
from broker.spend import SpendGovernor
from broker.trace import BatchWriter, InMemorySink
from broker.translate import build_converse_request


def _service(tier: str, seen: list[dict], served: str | None = None):
    def canned(request: dict) -> dict:
        seen.append(request)
        response = {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
        }
        if served is not None:
            response["serviceTier"] = served
        return response

    settings = BrokerSettings(mock_mode=True, bedrock_service_tier=tier, spend_cap_usd=400.0)
    bedrock = MockBedrockGateway(canned_response_fn=canned)
    ledger = InMemoryLedgerClient()
    ledger.seed("a0001", balance_cents=100_000)
    service = BrokerService(
        settings=settings,
        ledger=ledger,
        lane_pool=build_lane_pool(settings, gateway_factory=lambda model, region: bedrock),
        spend_governor=SpendGovernor(cap_usd=400.0, warn_fraction=0.8),
        cost_accountant=CostAccountant(),
        trace_writer=BatchWriter(sink=InMemorySink(), batch_size=1000, flush_interval_s=1000.0),
        event_writer=BatchWriter(sink=InMemorySink(), batch_size=1000, flush_interval_s=1000.0),
    )
    return service


def _a_request() -> dict:
    return {
        "model": "moonshotai.kimi-k2.5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_the_tier_the_prices_assume_is_the_default() -> None:
    """The price table is flex-tier. Defaulting to anything else would keep
    every recorded cost figure wrong by a factor of two."""
    assert BrokerSettings().bedrock_service_tier == "flex"


def test_a_built_request_carries_the_tier() -> None:
    request = build_converse_request(
        model="moonshotai.kimi-k2.5",
        messages=[{"role": "user", "content": "hi"}],
        system=None,
        max_tokens=16,
        service_tier="flex",
    )
    assert request["serviceTier"] == {"type": "flex"}


def test_standard_asks_for_nothing_at_all() -> None:
    """`default` IS what Bedrock does with no field, and a model that has
    never seen the parameter must keep working exactly as it did."""
    request = build_converse_request(
        model="moonshotai.kimi-k2.5",
        messages=[{"role": "user", "content": "hi"}],
        system=None,
        max_tokens=16,
        service_tier="default",
    )
    assert "serviceTier" not in request


def test_a_tier_bedrock_does_not_have_is_refused_at_boot() -> None:
    """Named at the call site rather than discovered as a ValidationException
    forty minutes into a run."""
    errors = BrokerSettings(bedrock_service_tier="cheap").validate()
    assert any("cheap" in e and "flex" in e for e in errors), errors


@pytest.mark.asyncio
async def test_the_live_request_asks_for_the_configured_tier() -> None:
    """The wiring test: the tier reaches the wire through the service, not
    just through the translator."""
    seen: list[dict] = []
    service = _service("flex", seen)

    await service.handle_messages(_a_request(), {"authorization": "Bearer a0001"})

    assert seen and seen[0].get("serviceTier") == {"type": "flex"}


@pytest.mark.asyncio
async def test_a_downgrade_is_noticed(caplog) -> None:
    """Bedrock reports the tier that actually served the request. If it is
    not the one we asked for, the run is paying Standard while the trace
    prices it as Flex - which is the whole defect, returning quietly."""
    seen: list[dict] = []
    service = _service("flex", seen, served="default")

    with caplog.at_level(logging.WARNING):
        await service.handle_messages(_a_request(), {"authorization": "Bearer a0001"})

    assert any("flex" in r.message and "default" in r.message for r in caplog.records), (
        [r.message for r in caplog.records]
    )


@pytest.mark.asyncio
async def test_the_tier_we_asked_for_being_served_says_nothing(caplog) -> None:
    seen: list[dict] = []
    service = _service("flex", seen, served="flex")

    with caplog.at_level(logging.WARNING):
        await service.handle_messages(_a_request(), {"authorization": "Bearer a0001"})

    assert not [r for r in caplog.records if "serviceTier" in r.message]
