"""GOV-10: the false-conviction rate has a number.

DESIGN section 10's flagship justice metric. `false_conviction_stats` has
existed since GOV-02 gave a case a write-once ground truth, and nothing
called it. The trace could not stand in: its `cases` table listens for
`crime.case.*`, which only the unwired `world/crime/` package emits, while
the live tools emit `crime.theft.attempted`, `crime.complaint.filed` and
`crime.verdict.recorded` - on purpose without the perpetrator, because
residents perceive those events.

So the world, the one place that knows who really did it, now answers
`/world/justice` on both hosts; the run keeps the answer in its reading, so a
stopped run still has it; and the report prints it, keeping "nobody was
convicted" apart from "nobody was wrongly convicted".
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from world.charter.models import GovernmentState
from world.run_genesis import FINAL_READING_ROUTES, GenesisRunner, GenesisRunnerConfig
from world.run_report import justice_section
from world.tools.locations import LOCATION_TOWN_HALL
from world.tools.models import CrimeCase, CrimeCaseKind
from world.tools.primitives.institutions import justice_reading
from world.tools.tests.conftest import (  # noqa: F401 - fixtures
    registry,
    spawn_agent,
    world,
    world_and_registry,
)


def _theft(world, case_id: str, *, thief: str, accused: str) -> CrimeCase:
    case = CrimeCase(
        case_id=case_id, kind=CrimeCaseKind.THEFT, tick=world.tick(),
        filer_or_actor_agent_id=thief, against_agent_id=accused,
        victim_agent_id="victim", ground_truth_perpetrator_id=thief,
        description="took bread from the store", outcome="succeeded",
    )
    world.crime_cases.append(case)
    return case


async def _a_court(world) -> None:
    await spawn_agent(world, "pm", LOCATION_TOWN_HALL)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="pm", minister_agent_ids=[])
    )


@pytest.mark.asyncio
async def test_a_wrong_conviction_through_the_court_is_counted(world, registry) -> None:
    """End to end through `rule`: the court convicts the wrong neighbour."""
    await _a_court(world)
    for who in ("thief", "neighbour"):
        await spawn_agent(world, who, LOCATION_TOWN_HALL, starting_sim=5_000)
    _theft(world, "c-wrong", thief="thief", accused="neighbour")
    _theft(world, "c-right", thief="thief", accused="thief")

    await registry.execute(world, "pm", "rule", {"case_id": "c-wrong", "verdict": "fine", "fine_sim": 100})
    await registry.execute(world, "pm", "rule", {"case_id": "c-right", "verdict": "fine", "fine_sim": 100})

    reading = justice_reading(world)
    assert reading["convictions"] == 2
    assert reading["false"] == 1 and reading["true"] == 1
    assert reading["false_conviction_rate"] == 0.5


@pytest.mark.asyncio
async def test_no_convictions_is_no_rate_not_a_perfect_one(world) -> None:
    _theft(world, "c1", thief="thief", accused="")

    reading = justice_reading(world)

    assert reading["cases"] == 1 and reading["awaiting_ruling"] == 1
    assert reading["false_conviction_rate"] is None


def test_the_report_keeps_the_three_silences_apart() -> None:
    never_asked = "\n".join(justice_section(None))
    nobody = "\n".join(justice_section({"cases": 3, "convictions": 0}))
    unscorable = "\n".join(justice_section(
        {"cases": 2, "convictions": 2, "unknowable": 2, "false_conviction_rate": None}))
    scored = "\n".join(justice_section(
        {"cases": 2, "convictions": 2, "true": 1, "false": 1, "false_conviction_rate": 0.5}))

    assert "not recorded" in never_asked
    assert "nobody was convicted" in nobody
    assert "none can be scored" in unscorable
    assert "50%" in scored
    for text in (never_asked, nobody, unscorable):
        assert "0%" not in text


def test_the_run_keeps_it_in_its_reading() -> None:
    """A stopped world cannot be asked; the reading is how the report still knows."""
    assert FINAL_READING_ROUTES.get("justice") == "/world/justice"


@pytest_asyncio.fixture
async def town(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0, start_at_dawn=True,
    ))
    await runner.boot(resume=False)
    runner.build_world_app()
    return runner


@pytest.mark.asyncio
async def test_the_embedded_host_answers_it(town) -> None:
    _theft(town.world_state, "c1", thief=sorted(town.world_state.agents)[0], accused="")
    client = TestClient(town.world_app)

    body = client.get("/world/justice").json()

    assert body["cases"] == 1
    json.dumps(body)


def test_the_production_host_serves_the_same_route() -> None:
    from world import main

    paths = {getattr(route, "path", None) for route in main.app.routes}
    assert "/world/justice" in paths
