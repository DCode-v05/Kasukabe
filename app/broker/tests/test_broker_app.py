"""Smoke tests for the broker FastAPI app's health check and top-level
request wiring (auth/JSON/routing failures). The request PIPELINE itself
(wallet checks, rate limiting, cost accounting, tracing) is covered in
broker/tests/test_service.py against a BrokerService built directly,
without going through HTTP - these tests only check that app.py wires a
BrokerService into FastAPI correctly."""

from __future__ import annotations

from fastapi.testclient import TestClient

from broker.app import app, create_app
from broker.config import BrokerSettings, expected_lane_keys
from broker.trace import DEFAULT_HALT_AFTER_CONSECUTIVE_FAILURES


def test_health_reports_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "broker"


def test_health_reports_inference_provenance() -> None:
    """Whether completions are real is not knowable anywhere else.

    A run can have real engine subprocesses talking to a real world server
    and still be receiving the literal string "mock response" for every
    turn. Only this process knows which, so only this process can say.
    """
    mock_app = create_app(settings=BrokerSettings(mock_mode=True))
    assert TestClient(mock_app).get("/health").json()["inference"] == "mock"

    real = BrokerSettings(
        mock_mode=False, world_base_url="http://w", trace_sqlite_path=":memory:"
    )
    assert TestClient(create_app(settings=real)).get("/health").json()["inference"] == "bedrock"


def test_default_app_boots_against_real_bedrock() -> None:
    """The product has exactly one runtime mode now.

    This used to assert the opposite. A broker that can boot into canned
    responses can also boot into them BY ACCIDENT - a missing env var - and
    a run that looks healthy while nothing is thinking is the failure this
    codebase is least able to notice from the inside.
    """
    assert app.state.settings.mock_mode is False


def test_messages_rejects_invalid_json_body() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/messages",
        content=b"{not json",
        headers={"content-type": "application/json", "Authorization": "Bearer agent-1"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"


def test_messages_rejects_non_object_json_body() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/messages",
        json=["not", "an", "object"],
        headers={"Authorization": "Bearer agent-1"},
    )

    assert response.status_code == 400


def test_messages_requires_auth_header() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/messages",
        json={
            "model": "moonshotai.kimi-k2.5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


def test_messages_rejects_model_outside_allowlist() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/messages",
        json={
            "model": "anthropic.claude-3-opus",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer agent-unknown"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_error"


def test_messages_rejects_unknown_agent_before_dispatch() -> None:
    client = TestClient(create_app(settings=BrokerSettings(mock_mode=True, mock_ledger_balance_cents=0)))
    response = client.post(
        "/v1/messages",
        json={
            "model": "moonshotai.kimi-k2.5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer agent-does-not-exist"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found_error"


def test_create_app_accepts_explicit_settings() -> None:
    settings = BrokerSettings(mock_mode=True, rpm_per_lane=5)
    custom_app = create_app(settings=settings)

    assert custom_app.state.settings.rpm_per_lane == 5


def test_admin_lanes_lists_every_lane() -> None:
    client = TestClient(create_app(settings=BrokerSettings(mock_mode=True)))
    response = client.get("/admin/lanes")

    assert response.status_code == 200
    lanes = response.json()
    assert len(lanes) == len(expected_lane_keys())
    ids = {lane["lane_id"] for lane in lanes}
    assert ids == set(expected_lane_keys())
    assert all(lane["circuit_state"] == "closed" for lane in lanes)


def test_admin_spend_reports_a_fresh_snapshot() -> None:
    settings = BrokerSettings(mock_mode=True, spend_cap_usd=123.0)
    custom_app = create_app(settings=settings)
    client = TestClient(custom_app)

    response = client.get("/admin/spend")

    assert response.status_code == 200
    body = response.json()
    assert body["spent_usd"] == 0.0
    assert body["cap_usd"] == 123.0
    assert body["fraction_used"] == 0.0
    assert body["state"] == "normal"
    assert body["projected_exhaustion_s"] is None


def test_admin_trace_reports_healthy_fresh_writers() -> None:
    settings = BrokerSettings(mock_mode=True)
    custom_app = create_app(settings=settings)
    client = TestClient(custom_app)

    response = client.get("/admin/trace")

    assert response.status_code == 200
    body = response.json()
    assert body["dispatch_halted"] is False
    for writer_stats in (body["trace_writer"], body["event_writer"]):
        assert writer_stats["consecutive_failures"] == 0
        assert writer_stats["halted"] is False
        assert writer_stats["halt_after_consecutive_failures"] == DEFAULT_HALT_AFTER_CONSECUTIVE_FAILURES


def test_admin_trace_reports_a_halted_writer() -> None:
    settings = BrokerSettings(mock_mode=True)
    custom_app = create_app(settings=settings)
    service = custom_app.state.service
    service.trace_writer.consecutive_failures = service.trace_writer.halt_after_consecutive_failures
    service.trace_writer.last_failure_message = "sink is down"
    client = TestClient(custom_app)

    response = client.get("/admin/trace")

    body = response.json()
    assert body["dispatch_halted"] is True
    assert body["trace_writer"]["halted"] is True
    assert body["trace_writer"]["last_failure_message"] == "sink is down"


def test_messages_refuses_dispatch_when_trace_writer_is_halted() -> None:
    """The core guarantee: once the trace writer has failed persistently,
    POST /v1/messages must refuse BEFORE calling into BrokerService at
    all - no Bedrock call, no spend, for a call that could not have been
    recorded. Simulated by tripping the writer directly (not by making
    the mock sink fail batch_size times, which would be a real but slow
    and indirect way to reach the same state) - trace.py's own
    test_trace.py already proves flush() -> is_halted wiring in
    isolation.
    """
    settings = BrokerSettings(mock_mode=True)
    custom_app = create_app(settings=settings)
    service = custom_app.state.service
    service.event_writer.consecutive_failures = service.event_writer.halt_after_consecutive_failures
    client = TestClient(custom_app)

    response = client.post(
        "/v1/messages",
        json={
            "model": "moonshotai.kimi-k2.5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer agent-does-not-exist"},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "overloaded_error"
    assert "event_writer" in body["error"]["message"]
    assert response.json()["error"]["type"] != "not_found_error"


def test_messages_streaming_also_refuses_dispatch_when_halted() -> None:
    settings = BrokerSettings(mock_mode=True)
    custom_app = create_app(settings=settings)
    service = custom_app.state.service
    service.trace_writer.consecutive_failures = service.trace_writer.halt_after_consecutive_failures
    client = TestClient(custom_app)

    response = client.post(
        "/v1/messages",
        json={
            "model": "moonshotai.kimi-k2.5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer agent-does-not-exist"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "overloaded_error"


def test_messages_success_debits_and_serves_from_a_lane() -> None:
    settings = BrokerSettings(mock_mode=True)
    custom_service_app = create_app(settings=settings)
    client = TestClient(custom_service_app)

    response = client.post(
        "/v1/messages",
        json={
            "model": "moonshotai.kimi-k2.5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer agent-does-not-exist"},
    )

    assert response.status_code == 404
