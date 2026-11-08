"""Cross-module proof for STATUS.md risk #2 ("console-contract" task):
the REAL `game/console/world_client.py` `WorldAdminClient` driving the
REAL `world/admin_api.py` router - no `httpx.MockTransport` hand-rolled
fake anywhere in this file (that is `game/console/tests/fake_world_admin.py`'s
job, proving console's own request/response plumbing in isolation; this
file proves the two packages' wire contract actually agrees end to end,
against real `WorldState`/`Ledger`/`CharterEngine`/`TickEngine` objects).

Lives at the top level, not in `world/tests/` or `game/console/tests/`,
per app/CONVENTIONS.md's "Test conventions": "Cross-module invariant
tests - things no single package owns ... live in the top-level
app/tests/." Exactly that here: this test imports both `world.run_genesis`
and `game.console.world_client`, a boundary neither package's own test
suite is allowed to cross (app/CONVENTIONS.md's module-boundary rule,
`game/console/__init__.py`'s "game/ and world/ do not share Python
imports" - true for *production* code; a top-level cross-module test is
exactly the sanctioned exception, the same one this file's sibling
`test_ledger_dollar_invariant.py` and `test_module_boundaries.py` already
use).

Two hosts are exercised, matching STATUS.md risk #2's own text:

- `TestConsoleAgainstEmbeddedWorldApp`: boots a real `GenesisRunner` in
  mock mode and calls `start_embedded_world_server()` for real - a real
  `uvicorn.Server` bound to a real localhost TCP port, the exact code
  path `world/run_genesis.py`'s `main_async()` runs for a live genesis
  run. The `WorldAdminClient` talks to it over real HTTP with no
  transport override. This is "CRITICAL: run_genesis.py's embedded app
  must serve these too, since that is what a live run actually boots"
  from the task brief, proven as literally as a test can prove it short
  of an actual AWS host.
- `TestConsoleAgainstStandaloneWorldServer`: boots `world.main:app`'s
  real `lifespan()` (the same one `docker compose up world` runs) with a
  real `MapService` from the real Ville tileset, reached via
  `httpx.ASGITransport` instead of a socket (no fake handler - every
  request still runs the real FastAPI routing, the real pydantic
  validation, the real `WorldServer` methods; ASGITransport only skips
  the TCP layer, the same substitution `fastapi.testclient.TestClient`
  makes under the hood and that this repo's own
  `world/tests/test_run_genesis.py::TestEmbeddedWorldApp` already relies
  on). This is the one host with a real `MapService` wired in, so it is
  the one that can prove a petition approval actually grows the map, not
  just flips a status field.
"""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

import world.main as world_main
from game.console.errors import WorldAdminError
from game.console.schemas import ApprovePetitionRequest, DenyPetitionRequest
from game.console.world_client import WorldAdminClient
from world.charter.models import LandPetition
from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.models import PrayerRequest


def free_tcp_port() -> int:
    """Picks a real, currently-free localhost port for the embedded
    world app's real uvicorn server to bind. `world/tests/test_run_genesis.py`'s
    `make_runner` helper passes `world_port=0` (let the OS pick) precisely
    because none of ITS tests ever dial in over HTTP; this file's whole
    point is dialing in over HTTP, so this test needs to know the actual
    port ahead of time rather than introspect uvicorn's bound socket
    after the fact."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def boot_embedded_runner(tmp_path: Path) -> GenesisRunner:
    """Mirrors `main_async()`'s own real boot sequence (`world/run_genesis.py`):
    boot -> start_embedded_world_server -> start_agents. Mock mode
    (app/CONVENTIONS.md: "Never call a real LLM API in a test") but every
    other piece - world state, ledger, charter, the tick engine, the real
    `/admin/*` router - is the genuine article."""
    config = GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_host="127.0.0.1",
        world_port=free_tcp_port(),
        budget_cap_cents=40_000,
    )
    runner = GenesisRunner(config)
    await runner.boot(resume=False)
    await runner.start_embedded_world_server()
    await runner.start_agents()
    return runner


def admin_client_for(runner: GenesisRunner) -> WorldAdminClient:
    assert runner.admin_extras is not None, "start_embedded_world_server() resolves this"
    return WorldAdminClient(
        base_url=runner.config.world_base_url(),
        operator_token=runner.admin_extras.operator_token,
    )


class TestConsoleAgainstEmbeddedWorldApp:
    """The host a live 250-agent genesis run actually boots."""

    @pytest.mark.asyncio
    async def test_approve_land_petition_transitions_real_charter_state(
        self, tmp_path: Path
    ) -> None:
        runner = await boot_embedded_runner(tmp_path)
        client = admin_client_for(runner)
        try:
            filer_id = next(iter(runner.world_state.agents))
            petition = LandPetition(
                filed_by_agent_id=filer_id,
                title="grow south for farmland",
                description="the town needs more crop tiles",
                tile_count_requested=40,
                filed_tick=runner.world_state.tick(),
            )
            await runner.world_state.charter.land_petitions.store.put_land_petition(petition)

            pending = await client.list_land_petitions()
            assert [p.petition_id for p in pending] == [petition.petition_id]
            assert pending[0].status == "pending"

            decision = await client.approve_land_petition(
                petition.petition_id,
                ApprovePetitionRequest(decision_note="approved - farmland is scarce", edge="south", depth=5),
            )

            assert decision.petition.status == "approved"
            assert decision.petition.decision_note == "approved - farmland is scarce"
            # GROW-06. This used to assert `decision.expansion is None`,
            # and the comment explained why: the embedded world had no
            # MapService, so approval "recorded the decision" and grew
            # nothing. That was the finding, and this test was pinning it.
            #
            # Both hosts build a MapService at boot now, so the town the
            # operator approves land for actually gets the land. The
            # assertion is inverted rather than deleted: "the embedded host
            # grows the map too" is the fact worth holding.
            assert decision.expansion is not None, (
                "GROW-06: approving a petition on the embedded host grew no "
                "map - the host every run uses could not expand the town"
            )
            assert decision.expansion.new_height > decision.expansion.previous_height
            assert decision.expansion.depth == 5

            # And the charter's own state genuinely transitioned - not
            # just the HTTP response shape, the real CharterStore record.
            stored = await runner.world_state.charter.land_petitions.load_petition(petition.petition_id)
            assert stored.status.value == "approved"

            still_pending = await client.list_land_petitions()
            assert still_pending == []
        finally:
            await client.close()
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_deny_petition_records_the_decision_note(self, tmp_path: Path) -> None:
        runner = await boot_embedded_runner(tmp_path)
        client = admin_client_for(runner)
        try:
            filer_id = next(iter(runner.world_state.agents))
            petition = LandPetition(
                filed_by_agent_id=filer_id,
                title="grow east for a second college",
                description="one college cannot serve 250 agents forever",
                tile_count_requested=20,
                filed_tick=runner.world_state.tick(),
            )
            await runner.world_state.charter.land_petitions.store.put_land_petition(petition)

            denied = await client.deny_land_petition(
                petition.petition_id, DenyPetitionRequest(decision_note="not yet - treasury is thin")
            )
            assert denied.status == "denied"
            assert denied.decision_note == "not yet - treasury is thin"
        finally:
            await client.close()
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_silent_deny_prayer_leaves_no_trace_visible_to_the_agent(
        self, tmp_path: Path
    ) -> None:
        runner = await boot_embedded_runner(tmp_path)
        client = admin_client_for(runner)
        try:
            agent_id = next(iter(runner.world_state.agents))
            prayer = PrayerRequest(
                request_id="prayer-integration-test-1",
                agent_id=agent_id,
                tick=runner.world_state.tick(),
                message="please bless my harvest",
                donation_sim=0,
            )
            runner.world_state.prayer_requests.append(prayer)

            feed = await client.list_prayers()
            assert [p.prayer_id for p in feed] == [prayer.request_id]

            decision = await client.silent_deny_prayer(prayer.request_id)
            assert decision.decision == "silently_denied"
            assert decision.visible_to_agent is False

            # DESIGN.md section 8, verbatim: "silent-deny (agent never
            # learns)". world/admin_api.py's silent_deny_prayer route
            # deliberately omits target_agent_id/involved_agent_ids/
            # location_id from the emitted event's payload so the
            # requester's own perception interrupt policy
            # (world/core/perception.py) never surfaces it - checked here
            # against the REAL event bus, not just trusted from a comment.
            denial_events = [
                e for e in runner.world_state.event_bus.recent() if e.type == "religion.prayer.denied"
            ]
            assert len(denial_events) == 1
            payload = denial_events[0].payload
            assert "target_agent_id" not in payload
            assert "involved_agent_ids" not in payload
            assert "location_id" not in payload
        finally:
            await client.close()
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_pause_actually_freezes_ticks_and_resume_unfreezes_them(
        self, tmp_path: Path
    ) -> None:
        """The task's own bar: "a real total freeze: ticks halt at a
        boundary, broker holds dispatch, timestamp frozen" - not merely a
        `paused` flag that nothing reads. Found and fixed as a REAL bug in
        the course of writing this test: `GenesisRunner.run_tick()` never
        awaited `self.sim_control.wait_if_paused()` anywhere before this
        change, so POST /admin/control/pause flipped the flag but every
        tick kept advancing regardless - see world/run_genesis.py's
        run_tick() for the fix."""
        import asyncio

        runner = await boot_embedded_runner(tmp_path)
        client = admin_client_for(runner)
        try:
            status = await client.get_control_status()
            assert status.paused is False
            assert status.tick_index == 0

            paused_status = await client.pause()
            assert paused_status.paused is True

            burst_task = asyncio.create_task(runner.run_burst(3))
            await asyncio.sleep(0.3)

            assert not burst_task.done(), (
                "run_burst kept advancing while paused - the pause gate is not real"
            )
            frozen_status = await client.get_control_status()
            assert frozen_status.paused is True
            assert frozen_status.tick_index == 0, "timestamp must stay frozen while paused"

            resumed_status = await client.resume()
            assert resumed_status.paused is False

            results = await asyncio.wait_for(burst_task, timeout=10.0)
            assert len(results) == 3

            final_status = await client.get_control_status()
            assert final_status.paused is False
            assert final_status.tick_index == 3
        finally:
            await client.close()
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_spend_status_reads_the_real_ledger_pools(self, tmp_path: Path) -> None:
        runner = await boot_embedded_runner(tmp_path)
        client = admin_client_for(runner)
        try:
            spend = await client.get_spend_status()
            # SpendStatusView.check_consistent already enforces
            # burned + treasury + wallets == budget_total on parse (the
            # $1000 invariant, app/CONVENTIONS.md) - reaching this line at
            # all is half the proof. The other half: these are genesis's
            # real numbers, not placeholders.
            assert spend.budget_total_usd_cents == 100_000
            # Read from the genesis the runner booted, not pinned: the town
            # now staffs its institutions, so more of the fixed $1000 sits
            # in purses and correspondingly less in the treasury.
            genesis = runner.genesis
            assert spend.wallets_usd_cents == genesis.usd_ledger.agent_wallets_cents
            assert spend.treasury_usd_cents == genesis.usd_ledger.treasury_cents
            # Whatever the split, the town is still worth exactly $1000.
            assert spend.wallets_usd_cents + spend.treasury_usd_cents == 100_000
            assert spend.burned_usd_cents == 0

            await runner.run_burst(1)
            spend_after = await client.get_spend_status()
            assert spend_after.burned_usd_cents >= spend.burned_usd_cents
            assert (
                spend_after.burned_usd_cents
                + spend_after.treasury_usd_cents
                + spend_after.wallets_usd_cents
                == 100_000
            )
        finally:
            await client.close()
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_world_independently_rejects_a_wrong_operator_token(self, tmp_path: Path) -> None:
        """game/console/README.md: "world independently re-validates
        instead of trusting this proxy blindly." Proven here against the
        real embedded app, not just world/admin_api.py's own unit tests."""
        runner = await boot_embedded_runner(tmp_path)
        bad_client = WorldAdminClient(
            base_url=runner.config.world_base_url(), operator_token="not-the-real-token"
        )
        try:
            with pytest.raises(WorldAdminError) as exc_info:
                await bad_client.list_land_petitions()
            assert exc_info.value.status_code == 401
        finally:
            await bad_client.close()
            await runner.shutdown()


class TestConsoleAgainstStandaloneWorldServer:
    """world.main:app - the same composition `docker compose up world`
    runs, the only host with a real MapService wired in."""

    @pytest.mark.asyncio
    async def test_approve_land_petition_actually_grows_the_map(self) -> None:
        async with world_main.lifespan(world_main.app):
            server = world_main.app.state.world_server
            token = world_main.app.state.admin_extras.operator_token
            client = WorldAdminClient(
                base_url="http://world-standalone.test",
                operator_token=token,
                transport=httpx.ASGITransport(app=world_main.app),
            )
            try:
                filer_id = next(iter(server.world.agents))
                petition = LandPetition(
                    filed_by_agent_id=filer_id,
                    title="grow south for farmland",
                    description="the town needs more crop tiles",
                    tile_count_requested=40,
                    filed_tick=server.world.tick(),
                )
                await server.world.charter.land_petitions.store.put_land_petition(petition)

                decision = await client.approve_land_petition(
                    petition.petition_id,
                    ApprovePetitionRequest(
                        decision_note="approved",
                        edge="south",
                        depth=4,
                        new_sector_name="test_expansion_sector",
                    ),
                )

                assert decision.petition.status == "approved"
                assert decision.expansion is not None, (
                    "world.main:app has a real MapService wired - approval must "
                    "actually grow the map, not just flip a status field"
                )
                expansion = decision.expansion
                assert expansion.edge == "south"
                assert expansion.depth == 4
                assert expansion.new_height > expansion.previous_height
                assert expansion.new_sector_names == ["test_expansion_sector"]
            finally:
                await client.close()
