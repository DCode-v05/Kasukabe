"""Per-request id middleware.

Assigns every inbound HTTP request a server-generated id, makes it available
to the whole request (so all log lines carry it via ``request_id_var``, not
just ``/execute``), and echoes it back as the ``x-request-id`` response header
so a client/SDK can quote a single id to correlate its call with our logs.

Policy: the id is always generated server-side. Any inbound ``x-request-id``
is ignored — a client cannot control the value that lands in our logs, which
removes a log-injection / id-spoofing surface.

Raw ASGI (not Starlette's ``BaseHTTPMiddleware``) for the same reason the
Unicode sanitizer is raw ASGI: ``BaseHTTPMiddleware`` buffers the response and
breaks SSE streaming on endpoints like ``/execute``. Here we only need to set a
contextvar on the way in and append one header on ``http.response.start`` on the
way out, which works identically for buffered and streamed responses.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.logging_config import request_id_var

# Lowercase per ASGI convention; HTTP header names are case-insensitive.
REQUEST_ID_HEADER = "x-request-id"


class RequestIdMiddleware:
    """Generate an id per HTTP request, expose it via ``request_id_var``, and
    add ``x-request-id`` to the response headers."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # 12 hex chars — same id shape /execute already uses for its blocks, so
        # the response header, the log lines, and a turn's block ids all match.
        rid = uuid.uuid4().hex[:12]
        request_id_var.set(rid)
        header = (REQUEST_ID_HEADER.encode("ascii"), rid.encode("ascii"))

        async def send_with_request_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                # message["headers"] is a list of (name, value) byte tuples and
                # may be absent for a bare response; default to [] then append.
                message["headers"] = list(message.get("headers", [])) + [header]
            await send(message)

        await self.app(scope, receive, send_with_request_id)
