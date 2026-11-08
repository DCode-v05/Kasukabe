"""The Bedrock gateway - boto3 lives here and ONLY here in this repo.

app/CONVENTIONS.md: "broker/ owns ALL Bedrock access. It is the only
package allowed to `import boto3`." app/tests/test_module_boundaries.py
enforces that mechanically for the whole monorepo; this module is the one
place the import is allowed to exist.

Two implementations of the same BedrockGateway protocol:
  - Boto3BedrockGateway: the real thing. bedrock-runtime's `converse` /
    `converse_stream`, region ap-south-1 per DESIGN.md section 9. boto3
    is synchronous, so every call is pushed through asyncio.to_thread -
    the request pipeline this feeds (broker/service.py) is async
    end-to-end per app/CONVENTIONS.md's "async for anything that does
    I/O", and a blocking boto3 call in the event loop would stall every
    other agent's in-flight call.
  - MockBedrockGateway: BrokerSettings.mock_mode's gateway. Returns
    responses shaped exactly like botocore's real converse/converse_stream
    output (same dict keys, same event-stream shape) so translate.py runs
    through the identical code path in tests as it does in production -
    the mock differs only in where the bytes come from, never in shape.
    TASK spec: "Config for a mock mode (no real Bedrock) used by all
    tests."
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from broker.errors import UpstreamError, UpstreamOverloadedError
from broker.tokens import estimate_tokens

# botocore error codes that mean "the model is allowed, Bedrock just can't
# serve it right now" - distinct from every other ClientError, which is a
# genuine bad-gateway condition (broker/errors.py's UpstreamError).
THROTTLING_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ModelNotReadyException",
        "ServiceUnavailableException",
        "ModelTimeoutException",
    }
)

# The in-stream event keys converse_stream can emit that mean "the model
# stream itself failed partway through" - these arrive as ordinary stream
# events (one dict, one key), not as a raised exception, because the
# HTTP response already started streaming by the time they happen.
STREAM_ERROR_EVENT_KEYS = frozenset(
    {
        "internalServerException",
        "modelStreamErrorException",
        "validationException",
        "throttlingException",
        "serviceUnavailableException",
    }
)


# INFRA-15. How many Bedrock streams may be read at once.
#
# Sized for the town, not for the CPU: every one of these threads is
# blocked on a socket waiting for the next delta, so the usual "do not
# exceed core count" rule does not apply - they are holding a place in a
# queue, not computing. The interpreter's default executor allows
# min(32, cpu + 4), which at the 250-resident design target means 250
# streams contending for at most 32 slots, and the wait appears in no
# metric: not `rate_limit_wait_ms` (nothing throttled), not Bedrock's own
# latency (Bedrock answered) - just turns quietly crossing the
# 120-second dispatch limit, as 10 of run 23's did.
#
# 300 rather than 250: a stream is read for the length of a turn and
# turns overlap at the edges, so sizing exactly to the cast would put the
# last few residents of every tick back in the queue this exists to
# empty.
STREAM_POOL_WORKERS = int(os.environ.get("KASUKABE_STREAM_POOL_WORKERS", "300"))


class BedrockGateway(Protocol):
    async def converse(self, request: dict) -> dict:
        """Non-streaming call. `request` is exactly bedrock-runtime's
        `converse` kwargs (modelId, messages, system, inferenceConfig,
        toolConfig - see broker/translate.py). Returns the raw response
        dict (output.message.content, stopReason, usage, metrics)."""
        ...

    def converse_stream(self, request: dict) -> AsyncIterator[dict]:
        """Streaming call. Yields raw converse_stream event dicts, each
        with exactly one top-level key (messageStart, contentBlockStart,
        contentBlockDelta, contentBlockStop, messageStop, metadata) -
        botocore's own decoded shape, unmodified."""
        ...


def classify_client_error(error_code: str, message: str) -> UpstreamError | UpstreamOverloadedError:
    if error_code in THROTTLING_ERROR_CODES:
        return UpstreamOverloadedError(f"Bedrock {error_code}: {message}")
    return UpstreamError(f"Bedrock {error_code}: {message}")


def transport_errors() -> tuple[type[BaseException], ...]:
    """What Bedrock failing looks like when it never sends an error back.

    Run 27: eight calls died on `urllib3.exceptions.ReadTimeoutError` (read
    timeout=60) and TLS handshake timeouts. None is a `ClientError`, so
    none was translated, none was settled, and none reached the trace - the
    report said 100% dispatch success over a run that lost them, and each
    engine got a chunked body cut off mid-reply and answered `/tick` 500.

    Named rather than `Exception`: a KeyError in translation is a bug here,
    and must stay one rather than become a 502 blamed on AWS.
    """
    import urllib3.exceptions
    from botocore.exceptions import BotoCoreError

    return (BotoCoreError, urllib3.exceptions.HTTPError, OSError)


def transport_failure(exc: BaseException) -> UpstreamError:
    return UpstreamError(f"Bedrock transport failed: {type(exc).__name__}: {exc}")


class Boto3BedrockGateway:
    """One lane's regional bedrock-runtime client.

    `inference_profile_id` is this lane's inference profile, when the
    deploying account grants bedrock:InvokeModel through a profile rather
    than on the bare foundation model (broker/config.py's
    bedrock_inference_profiles). It is applied ONLY here, at the boto3
    boundary, and only to the outgoing request copy: the allowlist gate,
    pricing, lane ids and every trace row stay keyed on the logical model
    id, so a profile swap can never make the research record disagree
    with what was billed. None means "send the bare model id".
    """

    def __init__(
        self,
        region: str,
        max_attempts: int = 1,
        inference_profile_id: "str | None" = None,
    ) -> None:
        # Imported here, not at module top level: keeps the module
        # importable (and app/tests/test_module_boundaries.py's AST scan
        # happy about WHERE boto3 lives) even in environments that build
        # this package without the boto3 wheel installed, e.g. a docs
        # build. Real usage always has boto3 installed (pyproject.toml
        # lists it unconditionally); this is defensive, not a workaround.
        import boto3
        from botocore.config import Config

        # max_attempts=1 disables botocore's OWN internal retry loop.
        # TASK spec (broker-lanes track): "Circuit-break a lane on
        # repeated throttles, never spin-retry." Without this, a
        # ThrottlingException would get silently retried inside boto3
        # itself before this gateway ever sees a ClientError to classify -
        # both hiding the throttle from broker/lanes.py's CircuitBreaker
        # (which needs to SEE every throttle to count it) and quietly
        # burning extra wall-clock time against the caller's one dispatch
        # attempt. One lane, one attempt, one outcome; retry policy
        # (whether to try a different lane, or give up) belongs entirely
        # to broker/lanes.py + broker/service.py, not to botocore.
        self.region = region
        self.inference_profile_id = inference_profile_id
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(retries={"max_attempts": max_attempts}),
        )

    def resolved_request(self, request: dict) -> dict:
        """`request` with modelId pointed at this lane's inference profile.

        Returns a COPY - never mutates the caller's dict. broker/service.py
        holds on to the request it built for the trace record, and that
        record must keep saying which model was asked for, not which ARN
        happened to carry it.
        """
        if not self.inference_profile_id:
            return request
        return {**request, "modelId": self.inference_profile_id}

    async def converse(self, request: dict) -> dict:
        from botocore.exceptions import ClientError

        try:
            return await asyncio.to_thread(self.call_converse_sync, request)
        except ClientError as e:
            error = e.response.get("Error", {})
            raise classify_client_error(
                error.get("Code", "UnknownError"), error.get("Message", str(e))
            ) from e
        except transport_errors() as e:
            raise transport_failure(e) from e

    def call_converse_sync(self, request: dict) -> dict:
        return self.client.converse(**self.resolved_request(request))

    @property
    def stream_pool(self) -> ThreadPoolExecutor:
        """The executor every streaming read runs on.

        INFRA-15. `converse_stream` pulls each Bedrock event with
        `asyncio.to_thread`, which uses the interpreter's DEFAULT executor
        - `min(32, cpu_count + 4)` workers. Each of those workers is
        blocked on a socket read for as long as the model takes to produce
        the next delta, so the pool is not doing work, it is holding
        places in a queue.

        At 20 residents that is invisible. At the 250-resident design
        target, 250 concurrent streams each pulling hundreds of deltas
        contend for at most 32 slots, and the wait shows up nowhere: it is
        not `rate_limit_wait_ms` (the broker did not throttle), not
        latency Bedrock reports (Bedrock answered), just turns quietly
        exceeding the 120-second dispatch limit. Run 23 already discarded
        10 turns that way.

        A pool of its own, sized for the town rather than for the CPU,
        because these threads are waiting rather than computing - the
        usual "don't exceed core count" reasoning does not apply to a
        thread that is blocked on a socket. Shared across every lane and
        every stream in the process, created once.
        """
        gateway = type(self)
        if gateway._stream_pool is None:
            gateway._stream_pool = ThreadPoolExecutor(
                max_workers=STREAM_POOL_WORKERS,
                thread_name_prefix="bedrock-stream",
            )
        return gateway._stream_pool

    async def converse_stream(self, request: dict) -> AsyncIterator[dict]:
        from botocore.exceptions import ClientError

        try:
            response = await asyncio.to_thread(self.call_converse_stream_sync, request)
        except ClientError as e:
            error = e.response.get("Error", {})
            raise classify_client_error(
                error.get("Code", "UnknownError"), error.get("Message", str(e))
            ) from e
        except transport_errors() as e:
            raise transport_failure(e) from e

        stream = iter(response["stream"])
        sentinel = object()
        loop = asyncio.get_running_loop()
        pool = self.stream_pool
        while True:
            # INFRA-15: this pool, not the interpreter's default one. See
            # `stream_pool` for why the default is a ceiling rather than a
            # resource here.
            try:
                event = await loop.run_in_executor(pool, next, stream, sentinel)
            except transport_errors() as e:
                # Mid-stream, urllib3's own exception arrives unwrapped by
                # botocore - the exact shape of run 27's eight lost calls.
                raise transport_failure(e) from e
            if event is sentinel:
                return
            for key in event:
                if key in STREAM_ERROR_EVENT_KEYS:
                    body = event[key]
                    raise classify_client_error(key, str(body))
            yield event

    # INFRA-15: one pool for the whole process, built on first use.
    _stream_pool: "ThreadPoolExecutor | None" = None

    def call_converse_stream_sync(self, request: dict) -> dict:
        return self.client.converse_stream(**self.resolved_request(request))


class MockBedrockGateway:
    """Deterministic canned responses. Default behavior: echo a short
    fixed text reply. Override per-test via `canned_response_fn`, which
    receives the translated `request` dict and returns a converse-shaped
    response dict - use it to make the mock return a tool_use block, a
    specific stop_reason, or a specific token count.

    `raise_error`, if set, is raised (as-is) on the next call instead of
    returning a response, then cleared - one-shot, so a test can inject
    exactly one failure without affecting subsequent calls.
    """

    def __init__(
        self,
        fixed_text: str = "mock response",
        latency_s: float = 0.0,
        canned_response_fn: Callable[[dict], dict] | None = None,
        text_content_block_start: bool = False,
    ) -> None:
        # INFRA-23. Default OFF, because real Bedrock frequently omits it.
        #
        # Real Bedrock sends `contentBlockStart` for a `toolUse` block and
        # often does NOT send it for plain text - a text block arrives as a
        # bare `contentBlockDelta`. This mock always sent it, so every
        # end-to-end test through `build_service` exercised a friendlier
        # wire than production ever produces.
        #
        # That is how defect D1 survived fifteen runs: 938 calls recorded
        # with zero content and 360,250 output tokens billed against them,
        # while the suite was green throughout. `test_stream_fidelity.py`
        # now covers the bare shape directly, but only because it builds
        # its fixtures by hand; everything that goes through the mock was
        # still testing the shape that cannot fail.
        #
        # Kept as a flag rather than removed so a test that specifically
        # wants the friendly shape can still ask for it, in one visible
        # word rather than by accident.
        self.text_content_block_start = text_content_block_start
        self.fixed_text = fixed_text
        self.latency_s = latency_s
        self.canned_response_fn = canned_response_fn
        self.raise_error: Exception | None = None
        self.calls: list[dict] = []

    def build_response(self, request: dict) -> dict:
        self.calls.append(request)
        if self.canned_response_fn is not None:
            return self.canned_response_fn(request)

        input_chars = sum(
            len(block.get("text", ""))
            for message in request.get("messages", [])
            for block in message.get("content", [])
            if "text" in block
        )
        input_chars += sum(
            len(block.get("text", "")) for block in request.get("system", []) if "text" in block
        )
        input_tokens = estimate_tokens("x" * input_chars) if input_chars else 1
        output_tokens = estimate_tokens(self.fixed_text)

        return {
            "output": {"message": {"role": "assistant", "content": [{"text": self.fixed_text}]}},
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": input_tokens + output_tokens,
            },
            "metrics": {"latencyMs": int(self.latency_s * 1000)},
        }

    async def converse(self, request: dict) -> dict:
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.raise_error is not None:
            error, self.raise_error = self.raise_error, None
            raise error
        return self.build_response(request)

    async def converse_stream(self, request: dict) -> AsyncIterator[dict]:
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.raise_error is not None:
            error, self.raise_error = self.raise_error, None
            raise error

        response = self.build_response(request)
        message = response["output"]["message"]

        yield {"messageStart": {"role": "assistant"}}

        for index, block in enumerate(message["content"]):
            if "text" in block:
                # INFRA-23: only when explicitly asked for. See __init__.
                if self.text_content_block_start:
                    yield {
                        "contentBlockStart": {"contentBlockIndex": index, "start": {}}
                    }
                yield {
                    "contentBlockDelta": {
                        "contentBlockIndex": index,
                        "delta": {"text": block["text"]},
                    }
                }
                yield {"contentBlockStop": {"contentBlockIndex": index}}
            elif "toolUse" in block:
                tool_use = block["toolUse"]
                yield {
                    "contentBlockStart": {
                        "contentBlockIndex": index,
                        "start": {
                            "toolUse": {"toolUseId": tool_use["toolUseId"], "name": tool_use["name"]}
                        },
                    }
                }
                yield {
                    "contentBlockDelta": {
                        "contentBlockIndex": index,
                        "delta": {"toolUse": {"input": json.dumps(tool_use["input"])}},
                    }
                }
                yield {"contentBlockStop": {"contentBlockIndex": index}}

        yield {"messageStop": {"stopReason": response["stopReason"]}}
        yield {"metadata": {"usage": response["usage"], "metrics": response["metrics"]}}


def new_bedrock_message_id() -> str:
    """Synthetic Anthropic-shaped message id (Bedrock's converse response
    doesn't carry one) - translate.py uses this for the `id` field of the
    translated Messages response/message_start event."""
    return f"msg_bkr_{uuid.uuid4().hex[:24]}"
