"""Minimal HTTP transport for the citadel gateway — no vendor SDK.

The engine speaks the Anthropic Messages **wire shape** to citadel (it does not
talk to api.anthropic.com; citadel fans out to the real upstream). This module is
the thin httpx client that POSTs that wire and decodes the SSE stream — replacing
the anthropic SDK entirely. The wire bytes are byte-for-byte what the SDK sent;
only the client library is gone.

Two surfaces:
  - ``post_messages``   — non-streaming POST /v1/messages → parsed JSON dict.
  - ``stream_messages`` — streaming POST /v1/messages → decoded SSE event dicts.

Errors become ``CitadelAPIError`` (carries status_code/body/headers). It exposes a
``.response`` shim so ``herald/retry.py``'s duck-typed ``get_status_code`` /
``get_retry_after`` keep working unchanged.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import AsyncIterator

import httpx

from src.config import Settings

# The SDK sent this version header automatically; citadel may key on it, so we
# send it explicitly. KEEP this literal — it is a wire string, not a code dep.
ANTHROPIC_VERSION = "2023-06-01"

# Connection-level retry that the SDK used to provide. Status-code retries (429/
# 529) stay in the engine's OverloadTracker loop — this only covers transport
# hiccups (connection reset, etc.).
TRANSPORT_RETRIES = 2

DEFAULT_TIMEOUT = httpx.Timeout(600.0, connect=10.0)

# Transient-failure retry the engine lost when it dropped the vendor SDK and that
# httpx's transport-level ``retries`` does NOT cover: read-timeouts, dropped
# connections, and 5xx responses. citadel is single-shot (it trips a deployment's
# cooldown and raises — it does not re-pick within a request), so a retry here is
# what lets its route-to-next failover actually help the live turn. Pure httpx +
# asyncio backoff — no SDK. 429/529 are deliberately EXCLUDED: the engine's
# OverloadTracker owns those (overload/cache semantics live in the agent loop).
TRANSIENT_MAX_RETRIES = 2  # total attempts = 3
TRANSIENT_BACKOFF_BASE_S = 0.25
TRANSIENT_BACKOFF_MAX_S = 8.0

# httpx transient failures worth retrying: all timeouts, all network errors, and
# a server-side protocol drop. LocalProtocolError (a malformed request WE built)
# is intentionally excluded — a retry would just rebuild the same bad request.
RETRYABLE_HTTPX: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def is_retryable_status(status_code: int) -> bool:
    """A 5xx is a transient gateway/provider failure worth retrying — except 529
    (overloaded), which the OverloadTracker owns. 429 is a 4xx and also stays
    with the tracker."""
    return 500 <= status_code < 600 and status_code != 529


def transient_backoff(attempt: int) -> float:
    """Exponential backoff (0-based attempt) with small jitter, hard-capped."""
    return min(TRANSIENT_BACKOFF_BASE_S * (2**attempt), TRANSIENT_BACKOFF_MAX_S) + random.uniform(
        0, 0.1
    )


class CitadelAPIError(Exception):
    """A non-2xx response from citadel.

    Replaces the anthropic SDK's ``APIStatusError`` family. Carries the parsed
    error body (when JSON) so callers can inspect ``body["error"]["code"]`` (e.g.
    the citadel-stable ``QUOTA_EXCEEDED``). The ``.response`` property is a small
    shim exposing ``.status_code`` + ``.headers`` so the existing duck-typed
    extractors in ``retry.py`` need no change.
    """

    def __init__(
        self,
        status_code: int,
        body: dict | None = None,
        headers: dict | None = None,
        message: str = "",
    ) -> None:
        self.status_code = status_code
        self.body = body if isinstance(body, dict) else None
        self.headers = headers or {}
        super().__init__(message or f"citadel returned HTTP {status_code}")

    @property
    def response(self) -> "ResponseShim":
        return ResponseShim(self.status_code, self.headers)


class ResponseShim:
    """Tiny stand-in for ``exc.response`` so retry.py's getattr chain resolves."""

    def __init__(self, status_code: int, headers: dict) -> None:
        self.status_code = status_code
        self.headers = headers


class CitadelClient:
    """One persistent httpx client pointed at the citadel gateway.

    Construction mirrors the headers the anthropic SDK used to send so citadel
    sees an identical request: bearer auth, ``x-api-key`` (the SDK's default
    auth header), the ``anthropic-version`` pin, JSON content-type, and the
    optional ``X-Engine-Id`` trace header. Per-request ``anthropic-beta`` is
    merged in at call time.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.cdt_url or not settings.cdt_key:
            raise ValueError(
                "CitadelClient requires settings.cdt_url and settings.cdt_key — "
                "set CDT_URL and CDT_KEY env vars (citadel gateway URL + sk-sept-cit-* token)"
            )
        headers: dict[str, str] = {
            "Authorization": f"Bearer {settings.cdt_key}",
            # Citadel authenticates via the bearer token; the SDK also sent
            # x-api-key by default, so keep it for parity / belt-and-suspenders.
            "x-api-key": settings.cdt_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if settings.engine_id:
            headers["X-Engine-Id"] = settings.engine_id
        self.client = httpx.AsyncClient(
            base_url=settings.cdt_url,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            transport=httpx.AsyncHTTPTransport(retries=TRANSPORT_RETRIES),
        )

    async def close(self) -> None:
        """Close the underlying httpx client (used by stress-test teardown)."""
        await self.client.aclose()

    async def post_messages(self, body: dict, extra_headers: dict | None = None) -> dict:
        """POST /v1/messages (non-streaming) → parsed JSON. Raises CitadelAPIError.

        Retries transient transport failures and 5xx responses with backoff
        (429/529 excluded — the OverloadTracker owns those). Non-streaming, so a
        retry can never duplicate emitted output."""
        for attempt in range(TRANSIENT_MAX_RETRIES + 1):
            try:
                resp = await self.client.post(
                    "/v1/messages",
                    json=body,
                    headers=extra_headers or None,
                )
            except RETRYABLE_HTTPX:
                if attempt >= TRANSIENT_MAX_RETRIES:
                    raise
                await asyncio.sleep(transient_backoff(attempt))
                continue
            if resp.status_code >= 400:
                if is_retryable_status(resp.status_code) and attempt < TRANSIENT_MAX_RETRIES:
                    await asyncio.sleep(transient_backoff(attempt))
                    continue
                raise error_from_response(resp)
            return resp.json()
        raise RuntimeError("post_messages retry loop exhausted")  # defensive; unreachable

    async def stream_messages(
        self,
        body: dict,
        extra_headers: dict | None = None,
    ) -> AsyncIterator[dict]:
        """POST /v1/messages with stream=True → decoded SSE event dicts.

        Each yielded dict is the parsed ``data:`` JSON for one SSE event, which
        already carries its own ``type`` field (message_start, content_block_delta,
        …). Raises CitadelAPIError on a non-2xx status (error body read first).
        """
        stream_body = {**body, "stream": True}
        # Retry transient failures and pre-stream 5xx ONLY before the first event
        # is yielded. Once the model has emitted output, replaying the request
        # would duplicate it — so a mid-stream drop bubbles instead.
        for attempt in range(TRANSIENT_MAX_RETRIES + 1):
            yielded = False
            retry = False
            try:
                async with self.client.stream(
                    "POST",
                    "/v1/messages",
                    json=stream_body,
                    headers=extra_headers or None,
                ) as resp:
                    if resp.status_code >= 400:
                        if (
                            is_retryable_status(resp.status_code)
                            and attempt < TRANSIENT_MAX_RETRIES
                        ):
                            retry = True  # status known before any event → safe
                        else:
                            raw = await resp.aread()
                            raise error_from_bytes(resp.status_code, raw, dict(resp.headers))
                    else:
                        async for data in iter_sse(resp):
                            yielded = True
                            yield data
                        return
            except RETRYABLE_HTTPX:
                if yielded or attempt >= TRANSIENT_MAX_RETRIES:
                    raise  # mid-stream (would double-emit) or out of attempts
                retry = True
            if retry:
                await asyncio.sleep(transient_backoff(attempt))
                continue
        raise RuntimeError("stream_messages retry loop exhausted")  # defensive


async def iter_sse(resp: httpx.Response) -> AsyncIterator[dict]:
    """Decode an SSE stream into the JSON payloads of each ``data:`` line.

    Anthropic's Messages SSE sends paired ``event: <name>`` / ``data: <json>``
    lines separated by blank lines. The JSON payload already includes a ``type``
    field, so we yield the parsed data dict directly and ignore the ``event:``
    line. A ``data: [DONE]`` sentinel (if citadel emits one) ends the stream.
    """
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            # Skip malformed frames rather than crash the turn.
            continue


def error_from_response(resp: httpx.Response) -> CitadelAPIError:
    try:
        body = resp.json()
    except Exception:
        body = None
    return make_error(resp.status_code, body, dict(resp.headers), resp.text)


def error_from_bytes(status_code: int, raw: bytes, headers: dict) -> CitadelAPIError:
    try:
        body = json.loads(raw.decode() or "null")
    except Exception:
        body = None
    text = raw.decode(errors="replace")[:500]
    return make_error(status_code, body, headers, text)


def make_error(status_code: int, body, headers: dict, text: str) -> CitadelAPIError:
    message = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message", ""))
    return CitadelAPIError(
        status_code=status_code,
        body=body if isinstance(body, dict) else None,
        headers=headers,
        message=message or f"citadel returned HTTP {status_code}: {text[:200]}",
    )
