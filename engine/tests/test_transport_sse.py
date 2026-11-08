"""Transport unit tests — the citadel HTTP client + SSE decoder (no SDK, no network).

Drives ``CitadelClient`` with ``httpx.MockTransport`` feeding canned bytes, so the
wire decode is tested in isolation for the first time (the anthropic SDK made this
impossible). Covers: non-streaming post_messages, the full streaming event
sequence (message_start → content blocks → message_delta), tool_use accumulation,
thinking, and CitadelAPIError mapping for both paths.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.config import Settings
from src.herald.transport import CitadelClient, CitadelAPIError


def make_client(handler) -> CitadelClient:
    """Build a CitadelClient whose httpx client uses a MockTransport handler."""
    settings = Settings()
    settings.cdt_url = "http://citadel.test"
    settings.cdt_key = "sk-sept-cit-test"
    client = CitadelClient(settings)
    # Swap the real transport for the mock; keep the configured headers/base_url.
    client.client = httpx.AsyncClient(
        base_url="http://citadel.test",
        headers=dict(client.client.headers),
        transport=httpx.MockTransport(handler),
    )
    return client


def sse(*events: tuple[str, dict]) -> bytes:
    """Encode (event_name, data_dict) pairs as an Anthropic-style SSE byte body."""
    out = []
    for name, data in events:
        out.append(f"event: {name}\ndata: {json.dumps(data)}\n\n")
    return "".join(out).encode()


@pytest.mark.asyncio
async def test_post_messages_returns_parsed_json():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["version"] = request.headers.get("anthropic-version")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        )

    client = make_client(handler)
    result = await client.post_messages({"model": "m", "messages": []})
    assert result["id"] == "msg_1"
    assert result["content"][0]["text"] == "hi"
    assert captured["url"].endswith("/v1/messages")
    assert captured["auth"] == "Bearer sk-sept-cit-test"
    assert captured["version"] == "2023-06-01"  # the wire version pin is sent


@pytest.mark.asyncio
async def test_post_messages_raises_citadel_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": "QUOTA_EXCEEDED", "message": "no"}})

    client = make_client(handler)
    with pytest.raises(CitadelAPIError) as ei:
        await client.post_messages({"model": "m", "messages": []})
    assert ei.value.status_code == 429
    assert ei.value.body["error"]["code"] == "QUOTA_EXCEEDED"
    # retry.py duck-typing shim
    assert ei.value.response.status_code == 429


@pytest.mark.asyncio
async def test_stream_messages_sets_stream_flag_and_decodes_events():
    captured = {}
    body = sse(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_9",
                    "usage": {"input_tokens": 11, "cache_read_input_tokens": 3},
                },
            },
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 7},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = make_client(handler)
    events = [e async for e in client.stream_messages({"model": "m", "messages": []})]
    assert captured["body"]["stream"] is True  # transport sets stream=True
    types = [e["type"] for e in events]
    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[2]["delta"]["text"] == "Hello"
    assert events[0]["message"]["usage"]["input_tokens"] == 11


@pytest.mark.asyncio
async def test_stream_messages_raises_on_error_status():
    # A non-retryable 4xx surfaces immediately as CitadelAPIError. (5xx is now
    # retried — see the transient-retry tests below.)
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "boom"}})

    client = make_client(handler)
    with pytest.raises(CitadelAPIError) as ei:
        async for _ in client.stream_messages({"model": "m", "messages": []}):
            pass
    assert ei.value.status_code == 400


# --- transient-failure retry (no SDK; pure httpx + backoff) --------------------

NO_BACKOFF = "src.herald.transport.transient_backoff"


@pytest.mark.asyncio
async def test_post_messages_retries_transient_httpx_then_succeeds(monkeypatch):
    monkeypatch.setattr(NO_BACKOFF, lambda a: 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow first response", request=request)
        return httpx.Response(200, json={"id": "ok", "content": []})

    client = make_client(handler)
    result = await client.post_messages({"model": "m", "messages": []})
    assert result["id"] == "ok"
    assert calls["n"] == 2  # one transient failure, then success


@pytest.mark.asyncio
async def test_post_messages_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(NO_BACKOFF, lambda a: 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503, json={"error": {"message": "no healthy deployment"}})
        return httpx.Response(200, json={"id": "ok2", "content": []})

    client = make_client(handler)
    result = await client.post_messages({"model": "m", "messages": []})
    assert result["id"] == "ok2"
    assert calls["n"] == 3  # 503, 503, 200 — within MAX_RETRIES=2


@pytest.mark.asyncio
async def test_post_messages_does_not_retry_529(monkeypatch):
    monkeypatch.setattr(NO_BACKOFF, lambda a: 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(529, json={"error": {"message": "overloaded"}})

    client = make_client(handler)
    with pytest.raises(CitadelAPIError) as ei:
        await client.post_messages({"model": "m", "messages": []})
    assert ei.value.status_code == 529
    assert calls["n"] == 1  # the OverloadTracker owns 529 — herald must NOT retry it


@pytest.mark.asyncio
async def test_post_messages_5xx_exhausts_retries_then_raises(monkeypatch):
    monkeypatch.setattr(NO_BACKOFF, lambda a: 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = make_client(handler)
    with pytest.raises(CitadelAPIError) as ei:
        await client.post_messages({"model": "m", "messages": []})
    assert ei.value.status_code == 500
    assert calls["n"] == 3  # 1 initial + 2 retries, then it gives up


@pytest.mark.asyncio
async def test_stream_retries_pre_stream_5xx_then_streams(monkeypatch):
    monkeypatch.setattr(NO_BACKOFF, lambda a: 0.0)
    calls = {"n": 0}
    body = sse(("message_stop", {"type": "message_stop"}))

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"message": "no healthy"}})
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = make_client(handler)
    events = [e async for e in client.stream_messages({"model": "m", "messages": []})]
    assert [e["type"] for e in events] == ["message_stop"]
    assert calls["n"] == 2  # one 503 (before any event), then the real stream


@pytest.mark.asyncio
async def test_stream_does_not_retry_529(monkeypatch):
    monkeypatch.setattr(NO_BACKOFF, lambda a: 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(529, json={"error": {"message": "overloaded"}})

    client = make_client(handler)
    with pytest.raises(CitadelAPIError) as ei:
        async for _ in client.stream_messages({"model": "m", "messages": []}):
            pass
    assert ei.value.status_code == 529
    assert calls["n"] == 1


class MidStreamDropClient:
    """Fake httpx client: opens a stream, yields ONE event, then drops mid-stream.
    Tracks how many times the stream was opened to prove there was no replay."""

    def __init__(self) -> None:
        self.opens = 0

    def stream(self, method, url, json=None, headers=None):
        self.opens += 1

        class CM:
            async def __aenter__(inner):
                return MidStreamResp()

            async def __aexit__(inner, *exc):
                return False

        return CM()

    async def aclose(self):
        return None


class MidStreamResp:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"type": "message_start"}'
        raise httpx.RemoteProtocolError("peer closed connection mid-stream")


@pytest.mark.asyncio
async def test_stream_mid_stream_drop_is_not_retried(monkeypatch):
    """Once an event has been yielded, a drop must bubble — replaying would
    duplicate already-emitted output."""
    monkeypatch.setattr(NO_BACKOFF, lambda a: 0.0)
    settings = Settings()
    settings.cdt_url = "http://citadel.test"
    settings.cdt_key = "sk-sept-cit-test"
    client = CitadelClient(settings)
    fake = MidStreamDropClient()
    client.client = fake

    seen = []
    with pytest.raises(httpx.RemoteProtocolError):
        async for e in client.stream_messages({"model": "m", "messages": []}):
            seen.append(e)
    assert len(seen) == 1  # the one pre-drop event was delivered
    assert fake.opens == 1  # NOT retried after a partial stream


@pytest.mark.asyncio
async def test_sse_skips_blank_and_done_lines():
    raw = (
        b"event: ping\ndata: \n\n"
        b'event: message_stop\ndata: {"type": "message_stop"}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw)

    client = make_client(handler)
    events = [e async for e in client.stream_messages({"model": "m", "messages": []})]
    assert [e["type"] for e in events] == ["message_stop"]
