"""Smoke tests for the world server scaffold."""

from __future__ import annotations

from fastapi.testclient import TestClient

from world.app import app


def test_health_reports_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "world"}
