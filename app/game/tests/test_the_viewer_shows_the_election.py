"""The township viewer shows the town's election.

world/ served `/voting/elections` on the embedded host all along and the
viewer never asked: a run could hold a whole election and the only sign on
screen was sashes appearing on the winners afterwards. `get_election` feeds
the map's election strip and the Election book.

Two rules the tests hold:

  * the result stays visible after the count. The next election is
    scheduled the moment a vote is counted, so "the open election" stops
    being the one with the result exactly when the result is the news -
    `latest_result` is carried separately;
  * who voted for whom stays off the page, as it stays off the town's own
    event feed. Candidates and totals only.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game.config import GameSettings
from game.mock_world import MockWorldClient
from game.world_client import HttpWorldClient, WorldClient

COUNTED = {"election_id": "e1", "phase": "tallied", "scheduled_tick": 0, "candidacy_opens_tick": 0,
           "candidacy_closes_tick": 144, "voting_opens_tick": 288, "voting_closes_tick": 432,
           "candidate_count": 2, "ballots_cast": 3,
           "result": {"prime_minister": {"agent_id": "n03", "name": "Hester Vance"}, "ministers": [],
                      "vote_counts": {"n03": 2, "n07": 1}, "tallied_tick": 432}}
NEXT = {**COUNTED, "election_id": "e2", "phase": "candidacy_open", "scheduled_tick": 432,
        "candidate_count": 0, "ballots_cast": 0, "result": None}


def _world(elections: list[dict]) -> FastAPI:
    app = FastAPI()

    @app.get("/voting/elections")
    async def listing() -> list[dict]:
        return elections

    @app.get("/voting/elections/{election_id}")
    async def detail(election_id: str) -> dict:
        body = next(e for e in elections if e["election_id"] == election_id)
        return {**body, "candidates": [], "running_tally": {},
                "ballots": [{"voter": {"agent_id": "n01"}, "candidate": {"agent_id": "n03"}}]}

    @app.get("/world/state")
    async def state() -> dict:
        return {"tick": 440}

    return app


def _client(app: FastAPI) -> HttpWorldClient:
    client = HttpWorldClient(GameSettings(mock_mode=False, world_base_url="http://world.test"))
    client.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://world.test")
    return client


@pytest.mark.asyncio
async def test_after_the_count_the_result_is_still_there() -> None:
    client = _client(_world([NEXT, COUNTED]))

    body = await client.get_election()

    assert body["current"]["election_id"] == "e2", "the open one is the next election"
    assert body["latest_result"]["election_id"] == "e1"
    assert body["latest_result"]["result"]["prime_minister"]["name"] == "Hester Vance"
    assert body["tick"] == 440
    await client.http.aclose()


@pytest.mark.asyncio
async def test_who_voted_for_whom_is_not_served() -> None:
    client = _client(_world([NEXT, COUNTED]))

    body = await client.get_election()

    assert "ballots" not in body["current"] and "ballots" not in body["latest_result"]
    await client.http.aclose()


@pytest.mark.asyncio
async def test_before_any_count_there_is_no_result() -> None:
    client = _client(_world([{**NEXT, "election_id": "e1", "scheduled_tick": 0}]))

    body = await client.get_election()

    assert body["current"]["phase"] == "candidacy_open" and body["latest_result"] is None
    await client.http.aclose()


def test_the_mock_still_satisfies_the_protocol() -> None:
    assert isinstance(MockWorldClient(GameSettings(mock_mode=True)), WorldClient)


def test_the_route_serves_it() -> None:
    from game.server import create_app

    app = create_app(GameSettings(mock_mode=True))
    with TestClient(app) as client:
        body = client.get("/api/town/election").json()

    assert body["available"] is True
    assert body["current"] is None and body["latest_result"] is None


def test_the_page_carries_the_panel_the_strip_and_the_script() -> None:
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "static"
    page = (static / "township-v2.html").read_text(encoding="utf-8")
    shell = (static / "js" / "township" / "shell.js").read_text(encoding="utf-8")

    assert 'id="electionStrip"' in page and 'id="electionBody"' in page
    assert "js/township/election.js" in page and "css/parts/election.css" in page
    assert "id: 'election'" in shell, "the Election book has no tab"
    assert "get('book')" in shell, "?book=election cannot open it"
