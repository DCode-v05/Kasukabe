"""WorldAdminClient tests - real HTTP round trips against
FakeWorldAdmin/httpx.MockTransport (app/CONVENTIONS.md's sanctioned
stand-in for a real out-of-process dependency), covering the request
shaping + response parsing for a representative sample of every tab, plus
the error-translation paths (401 -> WorldAdminError, 404 -> WorldAdminError,
unreachable -> WorldAdminUnavailableError)."""

from __future__ import annotations

import httpx
import pytest

from game.console.errors import WorldAdminError, WorldAdminUnavailableError
from game.console.schemas import (
    ApprovePetitionRequest,
    DenyPetitionRequest,
    DisasterRequest,
    DisasterScope,
    DisasterScopeType,
    DisasterType,
    GrantPrayerRequest,
    PopulationDialsUpdate,
    TickSpeedRequest,
    VetoAmendmentRequest,
)
from game.console.tests.fake_world_admin import build_transport
from game.console.world_client import WorldAdminClient


@pytest.mark.asyncio
async def test_list_land_petitions_returns_typed_views(fake_world, world_client) -> None:
    fake_world.seed_petition(title="expand east", tile_count_requested=20)
    petitions = await world_client.list_land_petitions()
    assert len(petitions) == 1
    assert petitions[0].title == "expand east"
    assert petitions[0].status == "pending"


@pytest.mark.asyncio
async def test_approve_land_petition_returns_expansion_summary(fake_world, world_client) -> None:
    petition = fake_world.seed_petition()
    decision = await world_client.approve_land_petition(
        petition["petition_id"],
        ApprovePetitionRequest(decision_note="approved", edge="south", depth=15),
    )
    assert decision.petition.status == "approved"
    assert decision.expansion is not None
    assert decision.expansion.depth == 15
    assert decision.expansion.edge == "south"


@pytest.mark.asyncio
async def test_deny_land_petition_has_no_expansion(fake_world, world_client) -> None:
    petition = fake_world.seed_petition()
    view = await world_client.deny_land_petition(
        petition["petition_id"], DenyPetitionRequest(decision_note="not now")
    )
    assert view.status == "denied"
    assert view.decision_note == "not now"


@pytest.mark.asyncio
async def test_approve_unknown_petition_raises_world_admin_error(world_client) -> None:
    with pytest.raises(WorldAdminError) as excinfo:
        await world_client.approve_land_petition(
            "does-not-exist", ApprovePetitionRequest(edge="south", depth=1)
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_veto_amendment(fake_world, world_client) -> None:
    amendment = fake_world.seed_amendment()
    result = await world_client.veto_amendment(
        amendment["proposal_id"], VetoAmendmentRequest(veto_note="too risky")
    )
    assert result.status == "vetoed"
    assert result.veto_note == "too risky"


@pytest.mark.asyncio
async def test_sign_amendment(fake_world, world_client) -> None:
    amendment = fake_world.seed_amendment()
    result = await world_client.sign_amendment(amendment["proposal_id"])
    assert result.status == "enacted"
    assert result.resulting_charter_version == 2


@pytest.mark.asyncio
async def test_grant_prayer_is_visible(fake_world, world_client) -> None:
    prayer = fake_world.seed_prayer()
    result = await world_client.grant_prayer(prayer["prayer_id"], GrantPrayerRequest())
    assert result.decision == "granted"
    assert result.visible_to_agent is True


@pytest.mark.asyncio
async def test_silent_deny_prayer_is_not_visible(fake_world, world_client) -> None:
    prayer = fake_world.seed_prayer()
    result = await world_client.silent_deny_prayer(prayer["prayer_id"])
    assert result.decision == "silently_denied"
    assert result.visible_to_agent is False


@pytest.mark.asyncio
async def test_grant_blessing(fake_world, world_client) -> None:
    blessing = fake_world.seed_blessing()
    result = await world_client.grant_blessing(blessing["request_id"])
    assert result.request.status == "granted"
    assert result.request.newborn_agent_id == "agent-newborn-1"


@pytest.mark.asyncio
async def test_deny_blessing_silent_flag_round_trips(fake_world, world_client) -> None:
    from game.console.schemas import DenyBlessingRequest

    blessing = fake_world.seed_blessing()
    result = await world_client.deny_blessing(blessing["request_id"], DenyBlessingRequest(silent=True))
    assert result.request.status == "denied"
    assert result.request.silent_deny is True


@pytest.mark.asyncio
async def test_trigger_disaster_town_wide(world_client) -> None:
    disaster = await world_client.trigger_disaster(
        DisasterRequest(
            disaster_type=DisasterType.PLAGUE,
            scope=DisasterScope(scope_type=DisasterScopeType.TOWN),
            magnitude=2.0,
        )
    )
    assert disaster.disaster_type == DisasterType.PLAGUE
    assert disaster.scope.scope_type == DisasterScopeType.TOWN
    assert disaster.magnitude == 2.0


@pytest.mark.asyncio
async def test_population_dials_round_trip(world_client) -> None:
    before = await world_client.get_population_dials()
    assert before.target_band_min == 250

    after = await world_client.update_population_dials(
        PopulationDialsUpdate(death_hazard_multiplier=0.0)
    )
    assert after.death_hazard_multiplier == 0.0
    assert after.target_band_min == 250


@pytest.mark.asyncio
async def test_get_spend_status(fake_world, world_client) -> None:
    fake_world.seed_spend(burned_usd_cents=500, treasury_usd_cents=49_500, wallets_usd_cents=50_000)
    status = await world_client.get_spend_status()
    assert status.burned_usd_cents == 500
    assert status.budget_total_usd_cents == 100_000


@pytest.mark.asyncio
async def test_alerts_respects_limit(fake_world, world_client) -> None:
    for i in range(5):
        fake_world.seed_alert(summary=f"alert {i}")
    alerts = await world_client.list_alerts(limit=2)
    assert len(alerts) == 2


@pytest.mark.asyncio
async def test_control_pause_resume_and_tick_speed(world_client) -> None:
    paused = await world_client.pause()
    assert paused.paused is True
    resumed = await world_client.resume()
    assert resumed.paused is False
    sped_up = await world_client.set_tick_speed(
        TickSpeedRequest(pacing_mode="burst", continuous_interval_s=0.0)
    )
    assert sped_up.pacing_mode == "burst"


@pytest.mark.asyncio
async def test_checkpoint_now_advances_game_day(world_client) -> None:
    first = await world_client.checkpoint_now()
    second = await world_client.checkpoint_now()
    assert second.game_day == first.game_day + 1


@pytest.mark.asyncio
async def test_wrong_operator_token_raises_world_admin_error(fake_world) -> None:
    client = WorldAdminClient(
        base_url="http://world.test",
        operator_token="wrong-token",
        transport=build_transport(fake_world),
    )
    with pytest.raises(WorldAdminError) as excinfo:
        await client.list_land_petitions()
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_unreachable_world_raises_unavailable_error() -> None:
    def blow_up(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = WorldAdminClient(
        base_url="http://world.test",
        operator_token="t",
        transport=httpx.MockTransport(blow_up),
    )
    with pytest.raises(WorldAdminUnavailableError):
        await client.list_land_petitions()
