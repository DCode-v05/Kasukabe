"""Full HTTP round trips through app.py's President routes: TestClient ->
route -> WorldAdminClient -> FakeWorldAdmin -> back. Also covers the
auth gate (shared by every /api/* route, so it only needs proving once
here and isn't repeated per tab test file) and the unauthenticated bits
(health check, static page)."""

from __future__ import annotations


def test_health_is_unauthenticated(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "console"}


def test_console_page_is_served_at_root(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Kasukabe" in response.text


def test_missing_token_is_rejected(client) -> None:
    response = client.get("/api/president/petitions")
    assert response.status_code == 401


def test_wrong_token_is_rejected(client) -> None:
    response = client.get(
        "/api/president/petitions", headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_x_operator_token_header_also_works(client, fake_world) -> None:
    fake_world.seed_petition()
    response = client.get(
        "/api/president/petitions", headers={"X-Operator-Token": "test-operator-token"}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_list_petitions_empty(client, auth_headers) -> None:
    response = client.get("/api/president/petitions", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_approve_petition_triggers_expansion(client, fake_world, auth_headers) -> None:
    petition = fake_world.seed_petition(title="grow south", tile_count_requested=12)
    response = client.post(
        f"/api/president/petitions/{petition['petition_id']}/approve",
        headers=auth_headers,
        json={"decision_note": "approved for farmland", "edge": "south", "depth": 12},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["petition"]["status"] == "approved"
    assert body["expansion"]["depth"] == 12
    assert body["expansion"]["edge"] == "south"


def test_approve_unknown_petition_is_404(client, auth_headers) -> None:
    response = client.post(
        "/api/president/petitions/does-not-exist/approve",
        headers=auth_headers,
        json={"edge": "south", "depth": 5},
    )
    assert response.status_code == 404


def test_approve_petition_rejects_invalid_edge(client, fake_world, auth_headers) -> None:
    petition = fake_world.seed_petition()
    response = client.post(
        f"/api/president/petitions/{petition['petition_id']}/approve",
        headers=auth_headers,
        json={"edge": "north", "depth": 5},
    )
    assert response.status_code == 422


def test_deny_petition(client, fake_world, auth_headers) -> None:
    petition = fake_world.seed_petition()
    response = client.post(
        f"/api/president/petitions/{petition['petition_id']}/deny",
        headers=auth_headers,
        json={"decision_note": "not enough surplus timber"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "denied"


def test_sign_and_veto_amendments(client, fake_world, auth_headers) -> None:
    to_sign = fake_world.seed_amendment(field_name="tax_rate_bps")
    to_veto = fake_world.seed_amendment(field_name="pm_seats")

    signed = client.post(
        f"/api/president/amendments/{to_sign['proposal_id']}/sign", headers=auth_headers
    )
    assert signed.status_code == 200
    assert signed.json()["status"] == "enacted"

    vetoed = client.post(
        f"/api/president/amendments/{to_veto['proposal_id']}/veto",
        headers=auth_headers,
        json={"veto_note": "too destabilizing"},
    )
    assert vetoed.status_code == 200
    assert vetoed.json()["status"] == "vetoed"
    assert vetoed.json()["veto_note"] == "too destabilizing"


def test_broadcast_address_to_the_nation(client, auth_headers) -> None:
    response = client.post(
        "/api/president/broadcast", headers=auth_headers, json={"message": "harvest festival tomorrow"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "harvest festival tomorrow"

    listing = client.get("/api/president/broadcasts", headers=auth_headers)
    assert len(listing.json()) == 1


def test_broadcast_rejects_empty_message(client, auth_headers) -> None:
    response = client.post("/api/president/broadcast", headers=auth_headers, json={"message": ""})
    assert response.status_code == 422


def test_grant_honor(client, auth_headers) -> None:
    response = client.post(
        "/api/president/honors",
        headers=auth_headers,
        json={"agent_id": "agent-42", "title": "Order of the Ville", "citation": "for civic duty"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == "agent-42"

    listing = client.get("/api/president/honors", headers=auth_headers)
    assert len(listing.json()) == 1
