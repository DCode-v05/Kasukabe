"""A Bedrock stream that times out is a recorded failure, not a vanished call.

Run 27, 14:49:49 and 14:50:49: Bedrock ap-south-1 stopped answering for
about two minutes. The broker log holds eight of these:

    broker.app: unhandled exception in /v1/messages
      File "broker/bedrock.py", line 231, in converse_stream
        event = await loop.run_in_executor(pool, next, stream, sentinel)
    urllib3.exceptions.ReadTimeoutError: AWSHTTPSConnectionPool(
        host='bedrock-runtime.ap-south-1.amazonaws.com', port=443):
        Read timed out. (read timeout=60)

and six `TimeoutError: _ssl.c:983: The handshake operation timed out`.

`converse_stream` only translated botocore's `ClientError`. A read timeout
is not one - it is a transport error, and mid-stream it arrives as urllib3's
own exception, unwrapped. `handle_messages_stream` only settles a
`BrokerError`, so none of the eight calls was settled: no trace row, no lane
outcome, no event. The run report said "Dispatch success 100.0%" over a run
that lost eight calls, and the engines saw a chunked body cut off mid-reply
("peer closed connection without sending complete message body"), failed
the turn, and answered `/tick` with a bare 500 - four residents lost a tick.

A timeout at Bedrock is Bedrock failing. It is now an `UpstreamError`, with
`failed_at = "bedrock"`, settled and traced like every other one.
"""

from __future__ import annotations

import pytest
import urllib3.exceptions
from botocore.exceptions import ReadTimeoutError as BotocoreReadTimeout

from broker.bedrock import Boto3BedrockGateway
from broker.errors import UpstreamError
from broker.ledger import InMemoryLedgerClient
from broker.tests.test_service import basic_request, build_single_gateway_service, drain, headers_for
from broker.trace import InMemorySink

RUN_27_READ_TIMEOUT = urllib3.exceptions.ReadTimeoutError(
    None, None, "Read timed out. (read timeout=60)"
)

FIRST_EVENTS = [
    {"messageStart": {"role": "assistant"}},
    {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "I will take the"}}},
]


def _stream_that_stops(error: BaseException):
    yield from FIRST_EVENTS
    raise error


def _gateway(*, opening_error: "BaseException | None" = None,
             stream_error: "BaseException | None" = None) -> Boto3BedrockGateway:
    class _Client:
        def converse_stream(self, **kwargs):
            if opening_error is not None:
                raise opening_error
            return {"stream": _stream_that_stops(stream_error)}

        def converse(self, **kwargs):
            raise opening_error

    gateway = Boto3BedrockGateway.__new__(Boto3BedrockGateway)
    gateway.client = _Client()
    gateway.resolved_request = lambda request: request
    return gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    RUN_27_READ_TIMEOUT,
    TimeoutError("_ssl.c:983: The handshake operation timed out"),
], ids=["read-timeout", "tls-handshake-timeout"])
async def test_a_timeout_mid_stream_is_an_upstream_error(error):
    gateway = _gateway(stream_error=error)

    seen = []
    with pytest.raises(UpstreamError) as raised:
        async for event in gateway.converse_stream({}):
            seen.append(event)

    assert seen == FIRST_EVENTS, "the events before the timeout still reached the caller"
    assert "timed out" in raised.value.message


@pytest.mark.asyncio
async def test_a_timeout_before_the_stream_opens_is_an_upstream_error():
    gateway = _gateway(opening_error=BotocoreReadTimeout(endpoint_url="https://bedrock-runtime"))

    with pytest.raises(UpstreamError):
        async for _ in gateway.converse_stream({}):
            pass


@pytest.mark.asyncio
async def test_a_timeout_on_a_non_streaming_call_is_an_upstream_error():
    gateway = _gateway(opening_error=BotocoreReadTimeout(endpoint_url="https://bedrock-runtime"))

    with pytest.raises(UpstreamError):
        await gateway.converse({})


@pytest.mark.asyncio
async def test_the_lost_call_is_in_the_trace():
    """The finding itself, end to end through the service: the call the
    run-27 report could not count."""
    ledger = InMemoryLedgerClient()
    ledger.seed("agent-1", balance_cents=1000)
    trace_sink: InMemorySink = InMemorySink()
    event_sink: InMemorySink = InMemorySink()
    service = build_single_gateway_service(
        _gateway(stream_error=RUN_27_READ_TIMEOUT), ledger, trace_sink, event_sink
    )
    request = basic_request()
    request["stream"] = True

    with pytest.raises(UpstreamError):
        async for _ in service.handle_messages_stream(request, headers_for("agent-1")):
            pass
    await drain(service)

    assert len(trace_sink.rows) == 1, "the timed-out call left no trace row"
    record = trace_sink.rows[0]
    assert record.success is False
    assert record.error_type == "UpstreamError"
    # "upstream", not "bedrock": the hop names the LINK that broke, and the
    # broker now speaks to more than one provider. A run served entirely by
    # gpt-6-luna reported its one failure as "`bedrock` x 1" in the run
    # report, naming a provider it never called. Which provider was behind
    # the hop is what the `model` column is for.
    assert record.failed_at == "upstream"
    assert ledger.accounts["agent-1"].balance_cents == 1000, "an unmetered failure costs nothing"


@pytest.mark.asyncio
async def test_a_programming_error_is_not_dressed_up_as_bedrock():
    """Only transport failures are Bedrock's. A KeyError is a bug here and
    must stay loud, not become a 502 blamed on AWS."""
    gateway = _gateway(stream_error=KeyError("contentBlockIndex"))

    with pytest.raises(KeyError):
        async for _ in gateway.converse_stream({}):
            pass
