"""BrokerService: the full request pipeline, wired against mock-mode
components (MockBedrockGateway, InMemoryLedgerClient, InMemorySink) -
see service.py's module docstring for the pipeline order this exercises
end to end: auth -> allowlist -> spend cap -> six-lane routing ->
translate -> wallet check -> per-lane rate limit -> Bedrock call ->
exact metering -> ledger debit -> circuit breaker feedback -> spend
governor -> trace + event.

`build_service` gives every one of the six lanes the SAME shared
MockBedrockGateway instance (not six separate ones) so tests can keep
asserting on one `bedrock.calls` list regardless of which lane
`LanePool.select` happens to route a given call to - broker/lanes.py's
`build_lane_pool` only requires `gateway_factory` to be called once per
lane, it does not require the returned instances to be distinct, and
broker/app.py's real `build_service` (production/`make run-broker`) is
the place that actually gives each lane its own instance, per
broker/lanes.py's module docstring. Lane-routing-SPECIFIC behavior
(Kimi-preferred, overflow-to-DeepSeek, per-lane circuit breaking) has its
own dedicated coverage in broker/tests/test_lanes.py; the tests here are
about the request pipeline being wired correctly end to end.
"""

from __future__ import annotations

import pytest

from broker.bedrock import MockBedrockGateway
from broker.config import BrokerSettings
from broker.errors import InsufficientFundsError, ModelNotAllowedError, UnknownAgentError
from broker.lanes import MODEL_PREFERENCE_ORDER, CallClass, build_lane_pool
from broker.ledger import InMemoryLedgerClient
from broker.pricing import CostAccountant
from broker.service import BrokerService
from broker.spend import SpendGovernor
from broker.trace import BatchWriter, InMemorySink


def build_service(
    fixed_text: str = "hello",
    rpm_per_lane: int = 100,
    canned_response_fn=None,
    spend_cap_usd: float = 400.0,
) -> tuple[BrokerService, InMemoryLedgerClient, MockBedrockGateway, InMemorySink, InMemorySink]:
    bedrock = MockBedrockGateway(fixed_text=fixed_text, canned_response_fn=canned_response_fn)
    settings = BrokerSettings(mock_mode=True, rpm_per_lane=rpm_per_lane, spend_cap_usd=spend_cap_usd)
    lane_pool = build_lane_pool(settings, gateway_factory=lambda model, region: bedrock)
    ledger = InMemoryLedgerClient()
    trace_sink: InMemorySink = InMemorySink()
    event_sink: InMemorySink = InMemorySink()
    service = BrokerService(
        settings=settings,
        ledger=ledger,
        lane_pool=lane_pool,
        spend_governor=SpendGovernor(
            cap_usd=settings.spend_cap_usd, warn_fraction=settings.spend_warn_fraction
        ),
        cost_accountant=CostAccountant(),
        trace_writer=BatchWriter(sink=trace_sink, batch_size=1000, flush_interval_s=1000.0),
        event_writer=BatchWriter(sink=event_sink, batch_size=1000, flush_interval_s=1000.0),
    )
    return service, ledger, bedrock, trace_sink, event_sink


def headers_for(agent_id: str, purpose: "str | None" = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {agent_id}"}
    if purpose:
        h["X-Kasukabe-Purpose"] = purpose
    return h


LARGE_TEXT = "word " * 50_000


def basic_request(model: str = "moonshotai.kimi-k2.5", max_tokens: int = 100) -> dict:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "hello there"}],
    }


async def drain(service: BrokerService) -> None:
    await service.trace_writer.flush()
    await service.event_writer.flush()


@pytest.mark.asyncio
async def test_success_debits_the_agents_own_wallet() -> None:
    service, ledger, bedrock, _, _ = build_service(fixed_text=LARGE_TEXT)
    ledger.seed("agent-1", balance_cents=100_000)

    response = await service.handle_messages(
        basic_request(max_tokens=100_000), headers_for("agent-1")
    )
    await drain(service)

    assert response["content"] == [{"type": "text", "text": LARGE_TEXT}]
    assert ledger.accounts["agent-1"].balance_cents < 100_000
    assert len(bedrock.calls) == 1


@pytest.mark.asyncio
async def test_success_writes_a_trace_record_with_costs_and_tokens() -> None:
    service, ledger, _, trace_sink, _ = build_service(fixed_text="hi")
    ledger.seed("agent-1", balance_cents=1000)

    await service.handle_messages(basic_request(), headers_for("agent-1"))
    await drain(service)

    assert len(trace_sink.rows) == 1
    record = trace_sink.rows[0]
    assert record.agent_id == "agent-1"
    assert record.payer_agent_id == "agent-1"
    assert record.model == "moonshotai.kimi-k2.5"
    assert record.requested_model == "moonshotai.kimi-k2.5"
    assert record.region in ("ap-south-1", "us-east-1", "us-west-2")
    assert record.call_class == "cognition"
    assert record.purpose == "auto:other"
    assert record.success is True
    assert record.input_tokens > 0
    assert record.output_tokens > 0
    assert record.cost_nano_dollars > 0
    assert record.error_type is None


@pytest.mark.asyncio
async def test_success_emits_broker_call_completed_event() -> None:
    service, ledger, _, _, event_sink = build_service()
    ledger.seed("agent-1", balance_cents=1000)

    await service.handle_messages(basic_request(), headers_for("agent-1"))
    await drain(service)

    types = [e.type for e in event_sink.rows]
    assert "broker.call.completed" in types


@pytest.mark.asyncio
async def test_purpose_header_is_captured_when_present() -> None:
    service, ledger, *_ = build_service()
    ledger.seed("agent-1", balance_cents=1000)

    await service.handle_messages(basic_request(), headers_for("agent-1", purpose="reflection"))
    trace_sink = service.trace_writer.sink
    await drain(service)

    assert trace_sink.rows[0].purpose == "reflection"


@pytest.mark.asyncio
async def test_background_purpose_is_served_by_a_deepseek_lane() -> None:
    service, ledger, _, trace_sink, _ = build_service()
    ledger.seed("agent-1", balance_cents=1000)

    await service.handle_messages(
        basic_request(model="deepseek.v3.2"), headers_for("agent-1", purpose="dream_probe.sleep_3")
    )
    await drain(service)

    record = trace_sink.rows[0]
    assert record.call_class == "background"
    assert record.model == "deepseek.v3.2"


@pytest.mark.asyncio
async def test_model_not_allowed_is_rejected_before_any_bedrock_call() -> None:
    service, _, bedrock, trace_sink, event_sink = build_service()

    with pytest.raises(ModelNotAllowedError):
        await service.handle_messages(
            basic_request(model="anthropic.claude-3-opus"), headers_for("agent-1")
        )
    await drain(service)

    assert bedrock.calls == []
    assert trace_sink.rows[0].success is False
    assert trace_sink.rows[0].error_type == "ModelNotAllowedError"
    assert trace_sink.rows[0].region is None
    assert event_sink.rows[0].type == "broker.call.rejected"


@pytest.mark.asyncio
async def test_unknown_agent_is_rejected_and_traced() -> None:
    service, _, bedrock, trace_sink, _ = build_service()

    with pytest.raises(UnknownAgentError):
        await service.handle_messages(basic_request(), headers_for("agent-ghost"))
    await drain(service)

    assert bedrock.calls == []
    assert trace_sink.rows[0].error_type == "UnknownAgentError"
    assert trace_sink.rows[0].region in ("ap-south-1", "us-east-1", "us-west-2")


@pytest.mark.asyncio
async def test_insufficient_funds_refuses_before_dispatch() -> None:
    service, ledger, bedrock, trace_sink, _ = build_service()
    ledger.seed("agent-poor", balance_cents=0)

    with pytest.raises(InsufficientFundsError):
        await service.handle_messages(basic_request(max_tokens=100_000), headers_for("agent-poor"))
    await drain(service)

    assert bedrock.calls == []
    assert ledger.accounts["agent-poor"].balance_cents == 0
    assert trace_sink.rows[0].error_type == "InsufficientFundsError"


@pytest.mark.asyncio
async def test_sufficient_funds_for_worst_case_is_allowed_through() -> None:
    service, ledger, *_ = build_service()
    ledger.seed("agent-1", balance_cents=10)

    response = await service.handle_messages(basic_request(max_tokens=50), headers_for("agent-1"))

    assert response["content"]


@pytest.mark.asyncio
async def test_spend_cap_reached_refuses_before_any_lane_or_wallet_check() -> None:
    from broker.errors import SpendCapExceededError

    service, ledger, bedrock, trace_sink, _ = build_service()
    ledger.seed("agent-1", balance_cents=1000)
    service.spend_governor.record_spend(service.spend_governor.cap_nano_dollars)
    assert service.spend_governor.should_block_dispatch()

    with pytest.raises(SpendCapExceededError):
        await service.handle_messages(basic_request(), headers_for("agent-1"))
    await drain(service)

    assert bedrock.calls == []
    assert trace_sink.rows[0].error_type == "SpendCapExceededError"


@pytest.mark.asyncio
async def test_minor_calls_debit_the_parents_wallet_not_their_own() -> None:
    service, ledger, _, trace_sink, *_ = build_service(fixed_text=LARGE_TEXT)
    ledger.seed("parent-1", balance_cents=100_000)
    ledger.seed("child-1", balance_cents=0, is_minor=True, parent_agent_id="parent-1")

    await service.handle_messages(basic_request(max_tokens=100_000), headers_for("child-1"))
    await drain(service)

    assert ledger.accounts["child-1"].balance_cents == 0
    assert ledger.accounts["parent-1"].balance_cents < 100_000
    assert trace_sink.rows[0].agent_id == "child-1"
    assert trace_sink.rows[0].payer_agent_id == "parent-1"


@pytest.mark.asyncio
async def test_minor_with_broke_parent_is_refused() -> None:
    service, ledger, bedrock, *_ = build_service()
    ledger.seed("parent-1", balance_cents=0)
    ledger.seed("child-1", balance_cents=1_000_000, is_minor=True, parent_agent_id="parent-1")

    with pytest.raises(InsufficientFundsError):
        await service.handle_messages(basic_request(max_tokens=100_000), headers_for("child-1"))

    assert bedrock.calls == []


@pytest.mark.asyncio
async def test_throttled_call_emits_rpm_throttled_event() -> None:
    fake_now = [0.0]

    def fake_clock() -> float:
        return fake_now[0]

    async def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    bedrock = MockBedrockGateway()
    settings = BrokerSettings(mock_mode=True, rpm_per_lane=1)
    lane_pool = build_lane_pool(
        settings, gateway_factory=lambda model, region: bedrock, clock=fake_clock, sleep=fake_sleep
    )
    ledger = InMemoryLedgerClient()
    ledger.seed("agent-1", balance_cents=100_000)
    event_sink: InMemorySink = InMemorySink()
    service = BrokerService(
        settings=settings,
        ledger=ledger,
        lane_pool=lane_pool,
        spend_governor=SpendGovernor(
            cap_usd=settings.spend_cap_usd, warn_fraction=settings.spend_warn_fraction
        ),
        cost_accountant=CostAccountant(),
        trace_writer=BatchWriter(sink=InMemorySink(), batch_size=1000, flush_interval_s=1000.0),
        event_writer=BatchWriter(sink=event_sink, batch_size=1000, flush_interval_s=1000.0),
    )

    background_lanes = sum(
        len(lane_pool.lanes_by_model[model])
        for model in MODEL_PREFERENCE_ORDER[CallClass.BACKGROUND]
    )
    for _ in range(background_lanes + 1):
        await service.handle_messages(
            basic_request(model="deepseek.v3.2"), headers_for("agent-1", purpose="summarizer.tick")
        )
    await drain(service)

    types = [e.type for e in event_sink.rows]
    assert "broker.rpm.throttled" in types


@pytest.mark.asyncio
async def test_stream_success_yields_events_and_settles_after() -> None:
    service, ledger, _, trace_sink, _ = build_service(fixed_text=LARGE_TEXT)
    ledger.seed("agent-1", balance_cents=100_000)

    request = basic_request(max_tokens=100_000)
    request["stream"] = True

    events = [e async for e in service.handle_messages_stream(request, headers_for("agent-1"))]
    await drain(service)

    types = [e["type"] for e in events]
    assert types[0] == "message_start"
    assert types[-1] == "message_stop"
    assert ledger.accounts["agent-1"].balance_cents < 100_000
    assert trace_sink.rows[0].success is True
    assert trace_sink.rows[0].response_body["content"] == [{"type": "text", "text": LARGE_TEXT}]


@pytest.mark.asyncio
async def test_stream_rejection_before_first_event_raises_without_dispatch() -> None:
    service, _, bedrock, trace_sink, *_ = build_service()

    request = basic_request(model="anthropic.claude-3-opus")
    request["stream"] = True

    gen = service.handle_messages_stream(request, headers_for("agent-1"))
    with pytest.raises(ModelNotAllowedError):
        await gen.__anext__()
    await drain(service)

    assert bedrock.calls == []
    assert trace_sink.rows[0].error_type == "ModelNotAllowedError"


class MidStreamFailingBedrock(MockBedrockGateway):
    """Yields a couple of real events, then raises - simulates Bedrock
    dying mid-generation after already producing partial output."""

    async def converse_stream(self, request: dict):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "partial"}}}
        from broker.errors import UpstreamError

        raise UpstreamError("Bedrock dropped the connection mid-stream")


def build_single_gateway_service(
    bedrock, ledger, trace_sink, event_sink, rpm_per_lane: int = 100
) -> BrokerService:
    """Shared helper for the tests below that need to hand-build a
    BrokerService around a specific gateway instance (a failure double)
    rather than the default MockBedrockGateway `build_service` uses."""
    settings = BrokerSettings(mock_mode=True, rpm_per_lane=rpm_per_lane)
    lane_pool = build_lane_pool(settings, gateway_factory=lambda model, region: bedrock)
    return BrokerService(
        settings=settings,
        ledger=ledger,
        lane_pool=lane_pool,
        spend_governor=SpendGovernor(
            cap_usd=settings.spend_cap_usd, warn_fraction=settings.spend_warn_fraction
        ),
        cost_accountant=CostAccountant(),
        trace_writer=BatchWriter(sink=trace_sink, batch_size=1000, flush_interval_s=1000.0),
        event_writer=BatchWriter(sink=event_sink, batch_size=1000, flush_interval_s=1000.0),
    )


@pytest.mark.asyncio
async def test_stream_mid_stream_failure_settles_with_zero_usage_and_is_traced() -> None:
    bedrock = MidStreamFailingBedrock()
    ledger = InMemoryLedgerClient()
    ledger.seed("agent-1", balance_cents=1000)
    trace_sink: InMemorySink = InMemorySink()
    event_sink: InMemorySink = InMemorySink()
    service = build_single_gateway_service(bedrock, ledger, trace_sink, event_sink)

    request = basic_request()
    request["stream"] = True

    collected = []
    from broker.errors import UpstreamError

    with pytest.raises(UpstreamError):
        async for event in service.handle_messages_stream(request, headers_for("agent-1")):
            collected.append(event)
    await drain(service)

    assert any(e["type"] == "content_block_delta" for e in collected)
    assert ledger.accounts["agent-1"].balance_cents == 1000
    record = trace_sink.rows[0]
    assert record.success is False
    assert record.error_type == "UpstreamError"
    assert record.response_body["content"] == [{"type": "text", "text": "partial"}]
    served_lane = service.lane_pool.lane_by_id(f"{record.model}@{record.region}")
    assert served_lane is not None
    assert served_lane.circuit.consecutive_failures == 0


@pytest.mark.asyncio
async def test_close_flushes_and_does_not_raise() -> None:
    service, ledger, *_ = build_service()
    ledger.seed("agent-1", balance_cents=1000)
    await service.handle_messages(basic_request(), headers_for("agent-1"))

    await service.close()


class DebitFailsLedger(InMemoryLedgerClient):
    """Balance reads succeed normally; every debit() raises, simulating
    a lost race against another concurrent debit for the same payer."""

    async def debit(self, agent_id: str, amount_cents: int, purpose: str, metadata: dict):
        raise InsufficientFundsError("simulated concurrent-debit race")


@pytest.mark.asyncio
async def test_ledger_debit_failure_still_returns_the_inference_result() -> None:
    ledger = DebitFailsLedger()
    ledger.seed("agent-1", balance_cents=100_000)
    trace_sink: InMemorySink = InMemorySink()
    event_sink: InMemorySink = InMemorySink()
    service = build_single_gateway_service(
        MockBedrockGateway(fixed_text=LARGE_TEXT), ledger, trace_sink, event_sink
    )

    response = await service.handle_messages(
        basic_request(max_tokens=100_000), headers_for("agent-1")
    )
    await drain(service)

    assert response["content"] == [{"type": "text", "text": LARGE_TEXT}]


@pytest.mark.asyncio
async def test_ledger_debit_failure_credits_the_cost_back_for_next_time() -> None:
    ledger = DebitFailsLedger()
    ledger.seed("agent-1", balance_cents=100_000)
    trace_sink: InMemorySink = InMemorySink()
    service = build_single_gateway_service(
        MockBedrockGateway(fixed_text=LARGE_TEXT), ledger, trace_sink, InMemorySink()
    )
    cost_accountant = service.cost_accountant

    await service.handle_messages(basic_request(max_tokens=100_000), headers_for("agent-1"))
    await drain(service)

    assert ledger.accounts["agent-1"].balance_cents == 100_000
    record = trace_sink.rows[0]
    assert record.success is True
    assert record.cost_nano_dollars > 0
    assert record.cents_debited == 0
    assert cost_accountant.remainders_nano["agent-1"] == record.cost_nano_dollars
