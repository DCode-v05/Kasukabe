"""Full HTTP round trips through app.py's God routes, with particular
attention to the DESIGN.md section 8 guarantee that grant/silent-deny are
genuinely distinct outcomes the operator cannot blur together."""

from __future__ import annotations


def test_prayer_grant_is_visible_to_agent(client, fake_world, auth_headers) -> None:
    prayer = fake_world.seed_prayer(agent_id="agent-7", content="grant me rain")
    response = client.post(
        f"/api/god/prayers/{prayer['prayer_id']}/grant",
        headers=auth_headers,
        json={"response_note": "it shall rain"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "granted"
    assert body["visible_to_agent"] is True


def test_prayer_silent_deny_is_not_visible_to_agent(client, fake_world, auth_headers) -> None:
    prayer = fake_world.seed_prayer(agent_id="agent-7", content="grant me riches")
    response = client.post(
        f"/api/god/prayers/{prayer['prayer_id']}/silent-deny", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "silently_denied"
    assert body["visible_to_agent"] is False


def test_silent_deny_takes_no_request_body(client, fake_world, auth_headers) -> None:
    # There is deliberately nothing to type for a silent deny (app.py's
    # route docstring) - confirm the route works with an empty POST.
    prayer = fake_world.seed_prayer()
    response = client.post(
        f"/api/god/prayers/{prayer['prayer_id']}/silent-deny", headers=auth_headers
    )
    assert response.status_code == 200


def test_prayer_list_reflects_pending_only(client, fake_world, auth_headers) -> None:
    fake_world.seed_prayer(agent_id="a1")
    fake_world.seed_prayer(agent_id="a2")
    response = client.get("/api/god/prayers", headers=auth_headers)
    assert len(response.json()) == 2


def test_grant_blessing(client, fake_world, auth_headers) -> None:
    blessing = fake_world.seed_blessing(household_id="household-9")
    response = client.post(
        f"/api/god/blessings/{blessing['request_id']}/grant", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["request"]["status"] == "granted"


def test_deny_blessing_silently(client, fake_world, auth_headers) -> None:
    blessing = fake_world.seed_blessing()
    response = client.post(
        f"/api/god/blessings/{blessing['request_id']}/deny",
        headers=auth_headers,
        json={"silent": True},
    )
    assert response.status_code == 200
    body = response.json()["request"]
    assert body["status"] == "denied"
    assert body["silent_deny"] is True


def test_disaster_requires_target_outside_town_scope(client, auth_headers) -> None:
    response = client.post(
        "/api/god/disasters",
        headers=auth_headers,
        json={"disaster_type": "crop_blight", "scope": {"scope_type": "district"}},
    )
    assert response.status_code == 422


def test_disaster_town_wide_does_not_need_a_target(client, auth_headers) -> None:
    response = client.post(
        "/api/god/disasters",
        headers=auth_headers,
        json={"disaster_type": "quake", "scope": {"scope_type": "town"}, "magnitude": 3.0},
    )
    assert response.status_code == 200
    assert response.json()["disaster_type"] == "quake"


def test_disaster_district_scope_with_target(client, auth_headers) -> None:
    response = client.post(
        "/api/god/disasters",
        headers=auth_headers,
        json={
            "disaster_type": "crop_blight",
            "scope": {"scope_type": "district", "target": "farmland-north"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["target"] == "farmland-north"


def test_disaster_rejects_unknown_type(client, auth_headers) -> None:
    response = client.post(
        "/api/god/disasters",
        headers=auth_headers,
        json={"disaster_type": "hurricane", "scope": {"scope_type": "town"}},
    )
    assert response.status_code == 422


def test_cast_miracle_and_misfortune(client, auth_headers) -> None:
    miracle = client.post(
        "/api/god/miracles",
        headers=auth_headers,
        json={"agent_id": "agent-9", "description": "a bumper harvest"},
    )
    assert miracle.status_code == 200
    assert miracle.json()["agent_id"] == "agent-9"

    misfortune = client.post(
        "/api/god/misfortunes",
        headers=auth_headers,
        json={"agent_id": "agent-9", "description": "a broken cart wheel"},
    )
    assert misfortune.status_code == 200
    assert misfortune.json()["agent_id"] == "agent-9"


def test_population_dials_get_and_update(client, auth_headers) -> None:
    before = client.get("/api/god/population-dials", headers=auth_headers)
    assert before.status_code == 200
    assert before.json()["target_band_min"] == 250

    updated = client.put(
        "/api/god/population-dials",
        headers=auth_headers,
        json={"death_hazard_multiplier": 0.0},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["death_hazard_multiplier"] == 0.0
    assert body["target_band_min"] == 250  # untouched field survives the sparse update


def test_population_dials_update_rejects_inverted_band(client, auth_headers) -> None:
    response = client.put(
        "/api/god/population-dials",
        headers=auth_headers,
        json={"target_band_min": 400, "target_band_max": 300},
    )
    assert response.status_code == 422
