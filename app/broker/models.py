"""Anthropic Messages API wire shapes - the subset broker actually needs.

This is the wire format herald speaks (engine/src/herald/transport.py's
docstring: "The engine speaks the Anthropic Messages wire shape to
citadel... This module is the thin httpx client that POSTs that wire").
broker/app.py's POST /v1/messages must accept and produce exactly this
shape so herald's base_url can point here unmodified (DESIGN.md section
9).

Top-level request fields are validated strictly with pydantic (model,
messages, max_tokens are genuinely required; a request missing them is
malformed, full stop). Content BLOCKS inside messages/system/tools are
kept as `dict` rather than a fully-modeled union of block types: Anthropic's
content-block schema is large (text, image, tool_use, tool_result,
thinking, redacted_thinking, each with their own nested shapes) and
broker only ever transcodes the handful herald actually emits (see
engine/src/herald/provider.py's blocks_to_wire / to_wire_tools /
tool_result_to_wire, which is the exhaustive list). broker/translate.py
walks these dicts directly and raises InvalidRequestError with a specific,
aggregated message for any block shape it doesn't recognize - a stricter
pydantic union would only move that same validation into worse error
messages.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessagesRequest(BaseModel):
    """POST /v1/messages request body. Extra fields (betas, thinking,
    metadata, and anything else herald's build_kwargs adds) are accepted
    and ignored rather than rejected - "compatible" means broker tolerates
    fields it doesn't act on, per the Anthropic API's own forward-
    compatibility stance, not that it implements 100% of the surface."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, Any]]
    max_tokens: int = Field(gt=0)
    system: str | list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    stream: bool = False


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class MessagesResponse(BaseModel):
    """Non-streaming POST /v1/messages response body - the Anthropic
    Messages API response shape (id, type, role, content, model,
    stop_reason, stop_sequence, usage). herald/provider.py's
    generate_with_tools_inner reads exactly these fields (see
    provider.py around response.get("id"), response["usage"], etc.)."""

    id: str
    type: str = "message"
    role: str = "assistant"
    content: list[dict[str, Any]]
    model: str
    stop_reason: str
    stop_sequence: str | None = None
    usage: Usage


class ErrorBody(BaseModel):
    type: str
    message: str


class ErrorResponse(BaseModel):
    type: str = "error"
    error: ErrorBody
