"""game/realsim.py: the Real sim button's run controller.

Starting a run spends real money and replaces the town, so the parts worth
testing are the refusals and the freshness guarantees - not the subprocess
itself, which is exercised by actually running it.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from game.realsim import (
    DEFAULT_AGENTS,
    MAX_AGENTS,
    MIN_AGENTS,
    TOTAL_USD_CENTS,
    RealSimController,
    max_wallet_cents,
    validate,
)
from game.server import create_app


# ── the purse ceiling ────────────────────────────────────────────────


def test_the_default_cast_is_fifty() -> None:
    assert DEFAULT_AGENTS == 50


def test_an_equal_purse_is_capped_by_the_towns_fixed_thousand() -> None:
    """world/ledger/money.py fixes TOTAL_USD_CENTS at $1000 and world/'s
    snapshot table has a CHECK constraint pinning wallets+treasury+burned to
    it, so the whole cast can hold at most $1000 between them."""
    assert max_wallet_cents(50) == TOTAL_USD_CENTS // 50 == 2000
    assert validate(50, 2000) is None
    problem = validate(50, 2001)
    assert problem and "2000 cents" in problem


def test_the_ceiling_moves_with_the_cast() -> None:
    """Twice the agents, half the purse. A fixed ceiling would be wrong for
    every cast size but one."""
    assert max_wallet_cents(100) == max_wallet_cents(50) // 2
    assert validate(100, 2000) is not None, "fine for 50, too much for 100"


@pytest.mark.parametrize("n", [MIN_AGENTS - 1, 0, MAX_AGENTS + 1])
def test_an_impossible_cast_size_is_refused(n: int) -> None:
    assert validate(n, 200) is not None


def test_negative_money_is_refused() -> None:
    assert validate(50, -1) is not None


# ── the controller ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_bad_request_never_spawns_anything() -> None:
    controller = RealSimController()
    result = await controller.start(agents=50, wallet_cents=999_999)
    assert result["ok"] is False
    assert controller.run is None or not controller.run.alive()


@pytest.mark.anyio
async def test_stopping_when_nothing_runs_is_not_an_error() -> None:
    controller = RealSimController()
    assert (await controller.stop())["ok"] is True


def test_status_reports_not_running_before_anything_starts() -> None:
    assert RealSimController().status()["running"] is False


# ── the endpoints ────────────────────────────────────────────────────


def test_defaults_endpoint_gives_the_dialog_its_ceiling() -> None:
    """The browser must not compute the ceiling itself - a dialog that
    guesses it lets the operator submit a run that is refused only after a
    genesis file has been written."""
    with TestClient(create_app()) as client:
        body = client.get("/api/realsim/defaults").json()
    assert body["agents"] == 50
    assert body["town_total_cents"] == TOTAL_USD_CENTS
    assert body["min_agents"] == MIN_AGENTS and body["max_agents"] == MAX_AGENTS


def test_start_refuses_an_over_funded_cast_with_a_readable_message() -> None:
    with TestClient(create_app()) as client:
        res = client.post("/api/realsim/start",
                          json={"agents": 50, "wallet_cents": 5000})
    assert res.status_code == 400
    assert "$20.00" in res.json()["error"], "the message states the real ceiling"


def test_start_refuses_nonsense_numbers() -> None:
    with TestClient(create_app()) as client:
        res = client.post("/api/realsim/start", json={"agents": "lots"})
    assert res.status_code == 400


def test_the_map_reads_the_run_world_when_one_is_bound() -> None:
    """The button starts a world on 8100; the map has to follow it. It used
    not to: a healthy 50-agent run showed as `cognition: mock, reported: 12`
    because the binding was fixed at process start."""
    # Baseline pinned to the mock town so the SWITCH is observable. The
    # default is a real world server now, which would report "world"
    # both before and after a run binds - true, but it would make this
    # test pass without testing anything.
    from game.config import GameSettings

    app = create_app(settings=GameSettings(mock_mode=True))
    with TestClient(app) as client:
        assert client.get("/api/engine").json()["world"]["source"] == "mock"

        class FakeRunWorld:
            async def get_snapshot(self):
                from game.contracts import Snapshot

                return Snapshot(tick=7, map_tiled_json={}, agents=[])

            async def get_provenance(self):
                return {"cognition": "engine", "inference": "bedrock"}

            async def close(self):
                return None

        app.state.realsim.world_client = FakeRunWorld()
        body = client.get("/api/engine").json()["world"]
        assert body["source"] == "world" and body["cognition"] == "engine"
        assert body["tick"] == 7


# ── the survival stop condition ──────────────────────────────────────
#
# A survival experiment has to END. Nothing else here ends it: the runner has
# no opinion about population, and a budget cap stops the spending without
# answering the question.


class FakeSnapshotWorld:
    """A world whose population the test drives directly."""

    def __init__(self, alive_sequence: list[int]) -> None:
        self.alive_sequence = list(alive_sequence)
        self.calls = 0

    async def get_snapshot(self):
        from game.contracts import AgentPosition, AgentSummary, Snapshot

        alive = self.alive_sequence[min(self.calls, len(self.alive_sequence) - 1)]
        self.calls += 1
        agents = [
            AgentSummary(
                agent_id=f"a{i}", display_name=f"A{i}", sprite_key="Isabella Rodriguez",
                position=AgentPosition(location_id="hospital"), is_alive=i < alive,
            )
            for i in range(10)
        ]
        return Snapshot(tick=self.calls, map_tiled_json={}, agents=agents)

    async def close(self):
        return None


@pytest.mark.anyio
async def test_the_watcher_stops_the_run_at_the_last_survivor(monkeypatch) -> None:
    import game.realsim as rs

    monkeypatch.setattr(rs, "SURVIVOR_POLL_S", 0.01)
    controller = rs.RealSimController()
    handle = rs.RunHandle(agents=10, wallet_cents=5, budget_cap_cents=400,
                          until_survivors=1)

    class LiveProcess:
        def poll(self): return None

    handle.process = LiveProcess()
    controller.run = handle
    controller.world_client = FakeSnapshotWorld([10, 6, 3, 1])

    terminated: list[bool] = []
    monkeypatch.setattr(controller, "_terminate", lambda h: terminated.append(True))

    await controller._watch_survivors(handle)

    assert terminated == [True], "the run must be stopped, not merely noticed"
    assert handle.alive == 1
    assert handle.dead == 9
    assert "1 of 10" in (handle.finished_reason or "")


@pytest.mark.anyio
async def test_the_watcher_leaves_a_healthy_town_alone(monkeypatch) -> None:
    """It must not stop a run that is simply going well."""
    import asyncio

    import game.realsim as rs

    monkeypatch.setattr(rs, "SURVIVOR_POLL_S", 0.01)
    controller = rs.RealSimController()
    handle = rs.RunHandle(agents=10, wallet_cents=5, budget_cap_cents=400,
                          until_survivors=1)

    class LiveProcess:
        def poll(self): return None

    handle.process = LiveProcess()
    controller.run = handle
    controller.world_client = FakeSnapshotWorld([10, 9, 8])
    terminated: list[bool] = []
    monkeypatch.setattr(controller, "_terminate", lambda h: terminated.append(True))

    task = asyncio.create_task(controller._watch_survivors(handle))
    await asyncio.sleep(0.12)
    task.cancel()
    assert terminated == []
    assert handle.finished_reason is None


@pytest.mark.anyio
async def test_zero_survivors_is_still_an_ending(monkeypatch) -> None:
    """A town that dies out entirely has answered the question too.

    UI-23. This test used to set `handle.finished_reason = "nobody
    survived"` and then assert `handle.finished_reason` - it asserted its
    own setup and could not fail, whatever the watcher did. The condition
    it was supposed to cover is the one where the population goes STRAIGHT
    PAST the threshold: `alive <= until_survivors` has to be the check,
    because `alive == until_survivors` never fires when the last two
    residents die in the same poll.
    """
    import game.realsim as rs

    monkeypatch.setattr(rs, "SURVIVOR_POLL_S", 0.01)
    controller = rs.RealSimController()
    handle = rs.RunHandle(agents=10, wallet_cents=5, budget_cap_cents=400,
                          until_survivors=1)

    class LiveProcess:
        def poll(self): return None

    handle.process = LiveProcess()
    controller.run = handle
    # 10 -> 4 -> 0: never equal to 1, straight past it.
    controller.world_client = FakeSnapshotWorld([10, 4, 0])

    terminated: list[bool] = []
    monkeypatch.setattr(controller, "_terminate", lambda h: terminated.append(True))

    # Bounded. A watcher that never notices would otherwise LOOP FOREVER
    # rather than fail, and a hanging test tells a reader nothing - the
    # first version of this fix hung for exactly that reason when the
    # threshold was flipped back to `==`.
    import asyncio

    try:
        await asyncio.wait_for(controller._watch_survivors(handle), timeout=5)
    except asyncio.TimeoutError:
        pytest.fail(
            "UI-23: the whole town died and the watcher is still polling - "
            "the threshold is tested for equality, so a population that "
            "steps over it in one poll is never noticed"
        )

    assert terminated == [True], (
        "UI-23: the whole town died and the watcher never stopped the run"
    )
    assert handle.alive == 0
    assert handle.dead == 10
    assert handle.finished_reason, "the run ended and recorded no reason"


def test_until_survivors_reaches_the_controller() -> None:
    with TestClient(create_app()) as client:
        body = client.get("/api/realsim/defaults").json()
    assert body["until_survivors"] == 1, "run to the last one standing by default"


# ── a cast the build-time roster has never heard of ─────────────────────
#
# agents.js holds the 314 residents of world/genesis.json and is the source
# the map and every panel read. A run against any OTHER cast - genesis_10's
# ten neighbours, ids n01..n10 - intersects it in nothing, so the filter
# produced an empty array and the viewer drew an empty town while ten
# engines were demonstrably alive.

def test_roster_ids_are_read_from_the_file():
    from game.server import _roster_ids

    raw = '...{"id":"a0039","n":"Felix Ward"},{"id":"a0090","n":"X"}...'
    assert _roster_ids(raw) == {"a0039", "a0090"}


def test_a_live_cast_outside_the_roster_is_detected():
    """The condition that decides whether to build rows from the world."""
    from game.server import _roster_ids

    raw = '{"id":"a0039"},{"id":"a0090"}'
    assert not ({"n01", "n02"} & _roster_ids(raw)), "n-ids must not match a-ids"
    assert {"a0039"} & _roster_ids(raw), "a known id must still match"
