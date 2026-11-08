"""Shared fake-citadel/broker harness for src/sim/ tests.

Not a test file itself (no ``test_`` prefix - pytest never collects it as a
test module; see CONVENTIONS.md's "Test file names are unique across the
whole repo," which is about test_*.py files, not shared helpers). Patches
``httpx.AsyncHTTPTransport`` - the concrete transport
src/herald/transport.py's ``CitadelClient.__init__`` always constructs - so
every ``Provider`` built through ``src.sim.model_profile.guarded_create_llm``
during a test speaks to an in-process handler function instead of a real
network socket. Mirrors tests/test_transport_sse.py's own
``httpx.MockTransport`` pattern (the engine's established idiom for this,
and one of the two forms CONVENTIONS.md names: "an httpx mock transport, a
fake broker client, a canned response fixture").

Kept in its own module rather than a conftest fixture so
tests/test_sim_pinned_boot.py and tests/test_sim_fake_broker.py can each
compose a FakeBroker with a different script.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


def sse(*events: tuple[str, dict]) -> bytes:
    """Encode (event_name, data_dict) pairs as an Anthropic-style SSE byte
    body - byte-identical shape to tests/test_transport_sse.py's own helper."""
    out = []
    for name, data in events:
        out.append(f"event: {name}\ndata: {json.dumps(data)}\n\n")
    return "".join(out).encode()


def text_turn_sse(text: str, *, input_tokens: int = 10, output_tokens: int = 10) -> bytes:
    """One full streamed turn: plain text, stop_reason=end_turn.

    Event shape matches src/herald/provider.py's stream_with_tools parser
    exactly (message_start -> content_block_start(text) ->
    content_block_delta(text_delta) -> content_block_stop -> message_delta
    -> message_stop).
    """
    return sse(
        (
            "message_start",
            {"type": "message_start", "message": {"id": "msg", "usage": {"input_tokens": input_tokens}}},
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


def tool_use_turn_sse(
    tool_wire_name: str,
    tool_input: dict,
    tool_id: str = "toolu_1",
    *,
    input_tokens: int = 10,
    output_tokens: int = 10,
) -> bytes:
    """One full streamed turn: a single tool_use block, stop_reason=tool_use.

    ``tool_wire_name`` must be the SANITIZED name (colons -> underscores,
    src/herald/types.sanitize_tool_name) - the same transform
    src/herald/provider.py applies to every ToolDef before it reaches the
    wire, and reverses on the way back via tool_name_reverse_map.
    """
    return sse(
        (
            "message_start",
            {"type": "message_start", "message": {"id": "msg", "usage": {"input_tokens": input_tokens}}},
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": tool_id, "name": tool_wire_name},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input)},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


def cut_off_monologue_sse(
    text: str, *, input_tokens: int = 10, output_tokens: int = 768
) -> bytes:
    """One streamed turn of pure prose that ran into the output cap.

    Same shape as ``text_turn_sse`` with ``stop_reason="max_tokens"`` - what
    the broker actually returns when a resident's monologue reaches
    ``sim/config.SIM_MAX_RESPONSE_TOKENS``. NO tool_use block, because that
    is the whole point of ENG-21: nothing was attempted.
    """
    return sse(
        (
            "message_start",
            {"type": "message_start", "message": {"id": "msg", "usage": {"input_tokens": input_tokens}}},
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


def cut_off_after_a_tool_call_sse(
    tool_wire_name: str,
    tool_input: dict,
    tool_id: str = "toolu_1",
    *,
    input_tokens: int = 10,
    output_tokens: int = 768,
) -> bytes:
    """A COMPLETED tool_use block, and then the output cap.

    The case ENG-21's fix must leave alone: the engine's continuation is
    doing real work here (it tombstones the interrupted results and lets the
    model finish the action it started), and a call spent finishing an act
    is what the world's budget is for.
    """
    return sse(
        (
            "message_start",
            {"type": "message_start", "message": {"id": "msg", "usage": {"input_tokens": input_tokens}}},
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": tool_id, "name": tool_wire_name},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input)},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


def json_message_response(text: str) -> httpx.Response:
    """A non-streaming /v1/messages response - what Provider.generate() reads
    (LCPipeline.adapter.generate() never streams)."""
    return httpx.Response(
        200,
        json={
            "id": "msg",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        },
    )


@dataclass
class FakeBroker:
    """Records every /v1/messages request and serves scripted responses.

    ``responses`` is keyed by model id -> a queue of (kind, payload) popped
    in call order. kind="sse" -> payload is bytes served as
    text/event-stream (streamed turns). kind="json" -> payload is a
    pre-built httpx.Response (non-streaming generate() calls).

    An empty queue for a model requested again is treated as a test bug - a
    call nothing scripted - and fails LOUDLY (AssertionError) rather than
    silently reusing the last response. A silent reuse would paper over
    exactly the failure mode this harness exists to catch: one extra model
    call sneaking in (a leaked fork, a stray router hop).
    """

    responses: dict[str, list[tuple[str, Any]]] = field(default_factory=dict)
    requested_models: list[str] = field(default_factory=list)
    request_bodies: list[dict] = field(default_factory=list)

    def queue(self, model_id: str, kind: str, payload: Any) -> None:
        self.responses.setdefault(model_id, []).append((kind, payload))

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages", f"unexpected path {request.url.path}"
        body = json.loads(request.content)
        model_id = str(body.get("model", ""))
        self.requested_models.append(model_id)
        self.request_bodies.append(body)

        queue = self.responses.get(model_id)
        if not queue:
            raise AssertionError(
                f"fake broker: no scripted response left for model {model_id!r} "
                f"(every model requested so far, in order: {self.requested_models})"
            )
        kind, payload = queue.pop(0)
        if kind == "sse":
            return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})
        if kind == "json":
            return payload
        raise ValueError(f"unknown fake-broker response kind {kind!r}")


def install_fake_broker(monkeypatch, broker: FakeBroker) -> None:
    """Patch httpx.AsyncHTTPTransport so every CitadelClient built during the
    test is backed by ``broker.handler`` instead of a real socket.
    Self-reverts at test teardown (monkeypatch fixture scope)."""

    def fake_transport(*args: Any, **kwargs: Any) -> httpx.MockTransport:
        return httpx.MockTransport(broker.handler)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", fake_transport)
