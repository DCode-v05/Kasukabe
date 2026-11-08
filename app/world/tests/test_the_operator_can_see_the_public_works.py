"""The operator can see what the town is building.

`GROW-25`. `game/console/app.py` served land petitions and charter
amendments and nothing else on the growth path: no tenders, no permits,
no licences, no building sites, no finished buildings. The operator could
approve a petition for land and then had no way at all to learn what the
town did with the ground.

The world already knew most of it and served it in pieces nobody
assembled. `/governance/active-policies` had tenders and licences
read-only, and the console's Town tab never rendered them. Buildings were
served NOWHERE - `agents_api.py:572` reports `len(world.buildings)`, a
count, and that was the whole of the town's construction as far as any
reader was concerned.

The labour figures come from GROW-18, and they are the reason a site view
is worth more than a list: a deadline that has passed with work
outstanding is a crew that stopped turning up, and a countdown alone
cannot show that.
"""
from __future__ import annotations

import pytest

from world.admin_api import BuildingSiteView, PublicWorksView


def test_the_view_carries_the_labour_and_not_only_the_deadline() -> None:
    """A site that is late and a site that is unworked look identical
    without this."""
    fields = BuildingSiteView.model_fields
    for name in ("labour_ticks_required", "labour_ticks_done", "labour_remaining"):
        assert name in fields, (
            f"GROW-25: a site view without {name} cannot distinguish a "
            "crew that is behind from a crew that never started"
        )
    assert "rect" in fields, "a building with no rect cannot be put on a map"


def test_the_panel_covers_everything_the_finding_names() -> None:
    """The review asks for: open tenders, awarded contracts, sites with
    progress, completed buildings - plus the permits and licences the
    world already served and nothing rendered."""
    fields = PublicWorksView.model_fields
    for name in ("sites", "completed", "tenders", "permits_and_licenses"):
        assert name in fields, f"GROW-25: the panel has no {name}"


@pytest.mark.asyncio
async def test_the_route_answers_from_a_real_world(tmp_path) -> None:
    """Through the real embedded app, not by calling the handler."""
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()

    from fastapi.testclient import TestClient

    from world.run_genesis import build_embedded_world_app

    extras = runner.build_admin_extras()
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service, admin_extras=extras,
    )
    with TestClient(app) as client:
        resp = client.get(
            "/admin/president/public-works",
            headers={"X-Operator-Token": extras.operator_token},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {
        "sites", "completed", "tenders", "permits_and_licenses", "expanded_strips"
    }


@pytest.mark.asyncio
async def test_a_site_under_construction_shows_its_progress(tmp_path) -> None:
    """The point of the panel. A town with a half-built smithy should
    show a half-built smithy."""
    from fastapi.testclient import TestClient

    from world.run_genesis import GenesisRunner, GenesisRunnerConfig
    from world.tools.models import Building

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    runner.world_state.buildings["b1"] = Building(
        building_id="b1", plot_id="p1", builder_agent_id="a0001",
        purpose="smithy", footprint=frozenset({(2, 3), (3, 3)}),
        timber_used=4, metal_used=2, started_tick=1, completion_tick=13,
        labour_ticks_required=12, labour_ticks_done=5,
    )

    from world.run_genesis import build_embedded_world_app

    extras = runner.build_admin_extras()
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service, admin_extras=extras,
    )
    with TestClient(app) as client:
        body = client.get(
            "/admin/president/public-works",
            headers={"X-Operator-Token": extras.operator_token},
        ).json()

    sites = {s["building_id"]: s for s in body["sites"]}
    assert "b1" in sites, (
        "GROW-25: a building under construction does not appear in the "
        "operator's public works panel"
    )
    assert sites["b1"]["labour_remaining"] == 7
    assert sites["b1"]["rect"] == {"x": 2, "y": 3, "w": 2, "h": 1}
    assert body["completed"] == [], "an unfinished site is not a completed one"


def test_the_console_exposes_it_too() -> None:
    """The world serving it and the console not rendering it is exactly
    the state the finding describes for tenders and licences."""
    from pathlib import Path

    console = Path(__file__).resolve().parents[2] / "game" / "console"
    app_source = (console / "app.py").read_text(encoding="utf-8")
    client_source = (console / "world_client.py").read_text(encoding="utf-8")

    assert '"/public-works"' in app_source, (
        "GROW-25: the console has no public works route, so the world "
        "serves the panel and the operator still cannot see it"
    )
    assert "get_public_works" in client_source
