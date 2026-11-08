"""Provider.stream must account usage + relay the quota snapshot.

The no-tools streamed turn (``Provider.stream``, the live call site in
thread.execute when ``tool_count == 0``) is the one inference path that did
NOT read ``message_start`` input usage / ``message_delta`` output usage, never
called ``record_usage``, and dropped citadel's ``quota`` relay. Every other
provider entry point (``generate``, ``generate_with_tools``,
``stream_with_tools``) records both — so a plain streamed turn silently
reported ZERO tokens to the per-request accumulator and to the product-layer
ledger, and the live-badge quota never updated.

These tests drive the REAL ``Provider.stream`` over a real ``CitadelClient``
whose httpx transport is an ``httpx.MockTransport`` feeding a fabricated
Anthropic SSE body (no network, no SDK) — the same seam test_transport_sse.py
uses. A real ``UsageAccumulator`` is bound on the contextvar (no mocks) and
inspected after the stream drains. ``stream_with_tools`` already does this
(provider.py:556-640); ``stream`` must mirror it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.config import Settings
from src.herald.provider import Provider
from src.herald.types import Message
from src.herald.usage_tracker import UsageAccumulator, usage_tracker_var


def sse(*events: tuple[str, dict]) -> bytes:
    """Encode (event_name, data_dict) pairs as an Anthropic-style SSE byte body."""
    out = []
    for name, data in events:
        out.append(f"event: {name}\ndata: {json.dumps(data)}\n\n")
    return "".join(out).encode()


def make_provider(body: bytes) -> Provider:
    """Build a real Provider whose transport replays a canned SSE body.

    Mirrors test_transport_sse.make_client: keep the configured headers/base_url
    but swap the real httpx transport for a MockTransport so no socket is opened.
    """
    settings = Settings()
    settings.cdt_url = "http://citadel.test"
    settings.cdt_key = "sk-sept-cit-test"
    provider = Provider(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider.client.client = httpx.AsyncClient(
        base_url="http://citadel.test",
        headers=dict(provider.client.client.headers),
        transport=httpx.MockTransport(handler),
    )
    return provider


# A complete no-tools streamed turn: message_start carries the input usage,
# a text delta carries the visible body, and message_delta carries the final
# output token count plus citadel's pre-computed quota dict.
STREAM_BODY = sse(
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_bare",
                "usage": {
                    "input_tokens": 137,
                    "cache_read_input_tokens": 40,
                    "cache_creation_input_tokens": 5,
                },
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
            "delta": {"type": "text_delta", "text": "Hello world"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 58},
            "quota": {
                "subscription": {
                    "session": {"used": 2000, "limit": 5000, "remaining": 3000, "reset": 18000},
                },
            },
        },
    ),
    ("message_stop", {"type": "message_stop"}),
)


@pytest.mark.asyncio
async def test_bare_stream_yields_text_unchanged():
    """The visible text contract is untouched — the fix only adds accounting."""
    provider = make_provider(STREAM_BODY)
    chunks = [c async for c in provider.stream([Message(role="user", content="hi")], system=None)]
    assert "".join(chunks) == "Hello world"


@pytest.mark.asyncio
async def test_bare_stream_records_usage_into_accumulator():
    """input_tokens come from message_start, output_tokens from message_delta —
    both must land in the active UsageAccumulator (matching stream_with_tools)."""
    provider = make_provider(STREAM_BODY)
    acc = UsageAccumulator()
    token = usage_tracker_var.set(acc)
    try:
        async for _ in provider.stream([Message(role="user", content="hi")], system=None):
            pass
    finally:
        usage_tracker_var.reset(token)

    total = acc.total()
    assert total.input_tokens == 137
    assert total.output_tokens == 58
    assert total.cache_read_input_tokens == 40
    assert total.cache_creation_input_tokens == 5
    assert acc.call_count == 1  # exactly one record_usage for the turn


@pytest.mark.asyncio
async def test_bare_stream_relays_quota_snapshot():
    """citadel's message_delta quota dict must reach the accumulator's
    latest_quota — the live-badge relay the no-tools path previously dropped."""
    provider = make_provider(STREAM_BODY)
    acc = UsageAccumulator()
    token = usage_tracker_var.set(acc)
    try:
        async for _ in provider.stream([Message(role="user", content="hi")], system=None):
            pass
    finally:
        usage_tracker_var.reset(token)

    assert acc.latest_quota() == {
        "subscription": {
            "session": {"used": 2000, "limit": 5000, "remaining": 3000, "reset": 18000},
        },
    }


@pytest.mark.asyncio
async def test_bare_stream_without_quota_records_usage_but_no_snapshot():
    """A turn where citadel omits quota still records usage; latest_quota stays
    None (record_quota_snapshot(None) is a no-op, never an invented dict)."""
    body = sse(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {"id": "msg_nq", "usage": {"input_tokens": 9}},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "ok"},
            },
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 3},
            },
        ),
    )
    provider = make_provider(body)
    acc = UsageAccumulator()
    token = usage_tracker_var.set(acc)
    try:
        async for _ in provider.stream([Message(role="user", content="hi")], system=None):
            pass
    finally:
        usage_tracker_var.reset(token)

    total = acc.total()
    assert total.input_tokens == 9
    assert total.output_tokens == 3
    assert acc.latest_quota() is None
