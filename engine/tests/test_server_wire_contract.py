"""Wire-contract guarantees for the engine HTTP edge.

Three things, proven against the real app through ASGITransport (no mocks on
the wire path):

  1. Every response carries a server-generated ``x-request-id`` header — on
     success and on error — and a fresh id per request.
  2. Every error is the one ``{"error": {code, message, retryable}}`` envelope,
     including unmatched-route 404s that Starlette raises before the engine's
     own handlers (previously a bare ``{"detail": "Not Found"}``).
  3. The 422 field-error list stays at ``error.details.errors[]`` — locking the
     decision to keep engine-internal "details is a dict" consistency rather
     than match citadel's bare ``error.details[]``.

Plus a unit slice of ``engine_error_from_status`` — the status→code mapper that
backs guarantee #2.
"""

import pytest
from httpx import ASGITransport, AsyncClient

import src.server as server
from src.errors import ErrorCode, engine_error_from_status


# ── Unit: status → structured error mapping ───────────────────────────────


def test_engine_error_from_status_maps_known_statuses():
    assert engine_error_from_status(404).code is ErrorCode.NOT_FOUND
    assert engine_error_from_status(503).code is ErrorCode.SERVICE_UNAVAILABLE
    assert engine_error_from_status(400).code is ErrorCode.BAD_REQUEST
    assert engine_error_from_status(401).code is ErrorCode.UNAUTHORIZED
    assert engine_error_from_status(403).code is ErrorCode.FORBIDDEN


def test_engine_error_from_status_preserves_and_defaults_message():
    # An ad-hoc detail survives verbatim...
    assert engine_error_from_status(503, "asset directory not ready").message == (
        "asset directory not ready"
    )
    # ...and absent a message, the code's default is used.
    assert engine_error_from_status(404).message == "Not Found"


def test_engine_error_from_status_honors_unmapped_status():
    # A status with no generic code keeps the original transport status and
    # falls back to internal_error — it is never silently rewritten to 500.
    err = engine_error_from_status(418, "teapot")
    assert err.http_status == 418
    assert err.code is ErrorCode.INTERNAL_ERROR
    assert err.message == "teapot"


# ── Integration: x-request-id on every response ───────────────────────────


@pytest.mark.asyncio
async def test_request_id_present_on_success_and_error():
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.get("/openapi.json")  # guaranteed 200, no auth
        missing = await client.get("/no-such-route")  # unmatched → 404

    assert ok.status_code == 200
    assert ok.headers.get("x-request-id")  # present and non-empty
    assert missing.status_code == 404
    assert missing.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_request_id_is_fresh_per_request():
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        a = await client.get("/openapi.json")
        b = await client.get("/openapi.json")
    assert a.headers["x-request-id"] != b.headers["x-request-id"]


# ── Integration: unmatched-route 404 is the envelope ──────────────────────


@pytest.mark.asyncio
async def test_unmatched_route_returns_error_envelope():
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/coordinator/threads/x/no-such-route")

    assert resp.status_code == 404
    body = resp.json()
    assert "detail" not in body  # no bare FastAPI shape
    assert body["error"]["code"] == ErrorCode.NOT_FOUND.value
    assert body["error"]["retryable"] is False
    assert body["error"]["message"]


# ── Integration: 422 field errors stay at error.details.errors[] ──────────


@pytest.mark.asyncio
async def test_validation_error_nests_at_details_errors():
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/execute", json={}, headers={"x-engine-key": "x"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == ErrorCode.INVALID_PARAMS.value
    errors = body["error"]["details"]["errors"]  # the locked nesting
    assert isinstance(errors, list) and errors
    assert {"loc", "msg", "type"} <= set(errors[0])
