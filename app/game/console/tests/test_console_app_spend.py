"""Full HTTP round trips through app.py's spend/burn-rate route (DESIGN.md
section 8, task spec: "show live spend + burn rate prominently").

Burn RATE itself is a client-side concern (static/console.js diffs two
samples - see schemas.py's SpendStatusView docstring) so there is nothing
to round-trip-test for it here; these tests cover the one thing this
route is actually responsible for: reporting the ledger's three USD pools
faithfully, auth-gated like every other admin route.
"""

from __future__ import annotations


def test_spend_status_defaults_to_genesis_pools(client, auth_headers) -> None:
    response = client.get("/api/spend/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["burned_usd_cents"] == 0
    assert body["treasury_usd_cents"] == 50_000
    assert body["wallets_usd_cents"] == 50_000
    assert body["budget_total_usd_cents"] == 100_000


def test_spend_status_reflects_burn_after_inference(client, fake_world, auth_headers) -> None:
    fake_world.seed_spend(
        burned_usd_cents=1_234,
        treasury_usd_cents=48_766,
        wallets_usd_cents=50_000,
        tick_index=17,
    )
    response = client.get("/api/spend/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["burned_usd_cents"] == 1_234
    assert body["budget_total_usd_cents"] == 100_000
    assert body["tick_index"] == 17


def test_spend_status_requires_auth(client) -> None:
    response = client.get("/api/spend/status")
    assert response.status_code == 401
