"""Full HTTP round trips through app.py's alert feed and top-level dials
(pause/resume, tick speed, checkpoint-now)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_alerts_empty_by_default(client, auth_headers) -> None:
    response = client.get("/api/alerts", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_alerts_returns_seeded_categories(client, fake_world, auth_headers) -> None:
    fake_world.seed_alert(category="crime_spike", summary="theft cluster near the market")
    fake_world.seed_alert(category="regime_change", summary="new Prime Minister sworn in")
    response = client.get("/api/alerts", headers=auth_headers)
    assert response.status_code == 200
    categories = {a["category"] for a in response.json()}
    assert categories == {"crime_spike", "regime_change"}


def test_alerts_respects_limit_query_param(client, fake_world, auth_headers) -> None:
    for i in range(5):
        fake_world.seed_alert(summary=f"alert {i}")
    response = client.get("/api/alerts?limit=3", headers=auth_headers)
    assert len(response.json()) == 3


def test_alerts_rejects_unknown_category_from_world(console_app, fake_world, auth_headers) -> None:
    fake_world.seed_alert(category="earthquake_swarm")
    with TestClient(console_app, raise_server_exceptions=False) as lenient_client:
        response = lenient_client.get("/api/alerts", headers=auth_headers)
    assert response.status_code == 500


def test_control_status_defaults(client, auth_headers) -> None:
    response = client.get("/api/control/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["paused"] is False
    assert body["pacing_mode"] == "continuous"


def test_pause_then_resume(client, auth_headers) -> None:
    paused = client.post("/api/control/pause", headers=auth_headers)
    assert paused.json()["paused"] is True

    resumed = client.post("/api/control/resume", headers=auth_headers)
    assert resumed.json()["paused"] is False


def test_set_tick_speed_to_burst(client, auth_headers) -> None:
    response = client.put(
        "/api/control/tick-speed",
        headers=auth_headers,
        json={"pacing_mode": "burst", "continuous_interval_s": 0.0},
    )
    assert response.status_code == 200
    assert response.json()["pacing_mode"] == "burst"


def test_set_tick_speed_rejects_negative_interval(client, auth_headers) -> None:
    response = client.put(
        "/api/control/tick-speed",
        headers=auth_headers,
        json={"pacing_mode": "continuous", "continuous_interval_s": -1.0},
    )
    assert response.status_code == 422


def test_checkpoint_now_returns_a_summary(client, auth_headers) -> None:
    response = client.post("/api/control/checkpoint-now", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "tick_index" in body
    assert "game_day" in body
    assert "written_at" in body


def test_control_routes_require_auth_too(client) -> None:
    response = client.post("/api/control/pause")
    assert response.status_code == 401
