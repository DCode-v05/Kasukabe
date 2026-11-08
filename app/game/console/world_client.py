"""WorldAdminClient: the ONLY way this package talks to world state
(app/CONVENTIONS.md: "game/ ... never computes game logic locally -
petitions, disasters, grants all go through world's HTTP API").

Every method here is a thin, typed wrapper over one call to world's
admin HTTP API. See README.md's "Open integration point" for the full
path list this client targets; that section and this file must be kept
in sync by hand until world/ actually implements the surface (there is
no shared source of truth to generate either side from yet - see that
README section's own note on this).

call_json() is the one place that does the actual httpx call, status
handling, and JSON parsing, so every public method below is just "shape
the request, call_json, parse the response" - matching ledger/core.py's
"one mutation primitive" idiom (app/CONVENTIONS.md-adjacent: funnel every
variant through one funnel so a behavior added there - retries, timeout
handling, error translation - lands everywhere at once).
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter

from game.console.errors import WorldAdminError, WorldAdminUnavailableError
from game.console.schemas import (
    AlertView,
    AmendmentProposalView,
    ApprovePetitionRequest,
    BirthDecisionView,
    BirthRequestView,
    BroadcastRequest,
    BroadcastView,
    CheckpointSummary,
    DenyBlessingRequest,
    DenyPetitionRequest,
    DisasterRequest,
    DisasterView,
    GrantPrayerRequest,
    HonorRequest,
    HonorView,
    LandPetitionView,
    PublicWorksView,
    MiracleRequest,
    MiracleView,
    MisfortuneRequest,
    MisfortuneView,
    PetitionDecisionView,
    PopulationDialsUpdate,
    PopulationDialsView,
    PrayerDecisionView,
    PrayerView,
    SimControlStatus,
    SpendStatusView,
    TickSpeedRequest,
    VetoAmendmentRequest,
)

ADMIN_PREFIX = "/admin"


class WorldAdminClient:
    """One instance lives on `app.state.world_client` for the life of the
    console process (see app.py's lifespan), the same pattern
    broker/app.py uses for BrokerService.

    `transport` exists purely for tests (httpx.MockTransport - see
    tests/fake_world_admin.py); production and `make run-console` both
    leave it None and get a real httpx.AsyncClient making real requests.
    """

    def __init__(
        self,
        base_url: str,
        operator_token: str,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {operator_token}"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def call_json(
        self,
        method: str,
        path: str,
        json_body: BaseModel | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Sends one request to world's admin API and returns the parsed
        JSON body. Raises WorldAdminUnavailableError if world could not
        be reached at all, WorldAdminError if world responded with a
        non-2xx status."""
        body = json_body.model_dump(mode="json") if json_body is not None else None
        try:
            response = await self.client.request(method, path, json=body, params=params)
        except httpx.HTTPError as exc:
            raise WorldAdminUnavailableError(
                f"could not reach world admin API at {self.client.base_url}{path}: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                parsed = response.json()
                if isinstance(parsed, dict) and "detail" in parsed:
                    detail = str(parsed["detail"])
            except ValueError:
                pass
            raise WorldAdminError(response.status_code, detail)

        if not response.content:
            return None
        return response.json()


    async def town_households(self) -> list[dict[str, Any]]:
        return await self.call_json("GET", "/family/households")

    async def town_at_risk(self, limit: int = 12) -> list[dict[str, Any]]:
        return await self.call_json("GET", "/family/at-risk", params={"limit": limit})

    async def town_wealth(self, top: int = 5) -> dict[str, Any]:
        return await self.call_json("GET", "/family/wealth", params={"top": top})

    async def town_money_flow(self) -> dict[str, Any]:
        return await self.call_json("GET", "/family/money-flow")

    async def town_businesses(self) -> list[dict[str, Any]]:
        return await self.call_json("GET", "/businesses/pnl")

    async def town_policy_and_treasury(self) -> dict[str, Any]:
        return await self.call_json("GET", "/governance/policy-and-treasury")

    async def town_terms(self) -> list[dict[str, Any]]:
        return await self.call_json("GET", "/governance/terms")

    async def town_skills(self) -> list[dict[str, Any]]:
        return await self.call_json("GET", "/skills/town")

    async def get_public_works(self) -> PublicWorksView:
        """GROW-25: what the town is building, and what it may build.

        The console could approve a land petition and then had no way to
        see what became of the ground - no tenders, no permits, no sites,
        no finished buildings. The world knew all of it and served none of
        it together.
        """
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/president/public-works")
        return PublicWorksView.model_validate(data)

    async def list_land_petitions(self) -> list[LandPetitionView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/president/petitions")
        return TypeAdapter(list[LandPetitionView]).validate_python(data)

    async def approve_land_petition(
        self, petition_id: str, req: ApprovePetitionRequest
    ) -> PetitionDecisionView:
        data = await self.call_json(
            "POST", f"{ADMIN_PREFIX}/president/petitions/{petition_id}/approve", req
        )
        return PetitionDecisionView.model_validate(data)

    async def deny_land_petition(
        self, petition_id: str, req: DenyPetitionRequest
    ) -> LandPetitionView:
        data = await self.call_json(
            "POST", f"{ADMIN_PREFIX}/president/petitions/{petition_id}/deny", req
        )
        return LandPetitionView.model_validate(data)


    async def list_amendments(self) -> list[AmendmentProposalView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/president/amendments")
        return TypeAdapter(list[AmendmentProposalView]).validate_python(data)

    async def sign_amendment(self, proposal_id: str) -> AmendmentProposalView:
        data = await self.call_json(
            "POST", f"{ADMIN_PREFIX}/president/amendments/{proposal_id}/sign"
        )
        return AmendmentProposalView.model_validate(data)

    async def veto_amendment(
        self, proposal_id: str, req: VetoAmendmentRequest
    ) -> AmendmentProposalView:
        data = await self.call_json(
            "POST", f"{ADMIN_PREFIX}/president/amendments/{proposal_id}/veto", req
        )
        return AmendmentProposalView.model_validate(data)


    async def broadcast_address(self, req: BroadcastRequest) -> BroadcastView:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/president/broadcast", req)
        return BroadcastView.model_validate(data)

    async def list_broadcasts(self) -> list[BroadcastView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/president/broadcasts")
        return TypeAdapter(list[BroadcastView]).validate_python(data)


    async def grant_honor(self, req: HonorRequest) -> HonorView:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/president/honors", req)
        return HonorView.model_validate(data)

    async def list_honors(self) -> list[HonorView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/president/honors")
        return TypeAdapter(list[HonorView]).validate_python(data)


    async def list_prayers(self) -> list[PrayerView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/god/prayers")
        return TypeAdapter(list[PrayerView]).validate_python(data)

    async def grant_prayer(self, prayer_id: str, req: GrantPrayerRequest) -> PrayerDecisionView:
        data = await self.call_json(
            "POST", f"{ADMIN_PREFIX}/god/prayers/{prayer_id}/grant", req
        )
        return PrayerDecisionView.model_validate(data)

    async def silent_deny_prayer(self, prayer_id: str) -> PrayerDecisionView:
        data = await self.call_json(
            "POST", f"{ADMIN_PREFIX}/god/prayers/{prayer_id}/silent-deny"
        )
        return PrayerDecisionView.model_validate(data)


    async def list_blessing_requests(self) -> list[BirthRequestView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/god/blessings")
        return TypeAdapter(list[BirthRequestView]).validate_python(data)

    async def grant_blessing(self, request_id: str) -> BirthDecisionView:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/god/blessings/{request_id}/grant")
        return BirthDecisionView.model_validate(data)

    async def deny_blessing(
        self, request_id: str, req: DenyBlessingRequest
    ) -> BirthDecisionView:
        data = await self.call_json(
            "POST", f"{ADMIN_PREFIX}/god/blessings/{request_id}/deny", req
        )
        return BirthDecisionView.model_validate(data)


    async def trigger_disaster(self, req: DisasterRequest) -> DisasterView:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/god/disasters", req)
        return DisasterView.model_validate(data)

    async def list_disasters(self) -> list[DisasterView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/god/disasters")
        return TypeAdapter(list[DisasterView]).validate_python(data)


    async def cast_miracle(self, req: MiracleRequest) -> MiracleView:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/god/miracles", req)
        return MiracleView.model_validate(data)

    async def list_miracles(self) -> list[MiracleView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/god/miracles")
        return TypeAdapter(list[MiracleView]).validate_python(data)

    async def cast_misfortune(self, req: MisfortuneRequest) -> MisfortuneView:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/god/misfortunes", req)
        return MisfortuneView.model_validate(data)

    async def list_misfortunes(self) -> list[MisfortuneView]:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/god/misfortunes")
        return TypeAdapter(list[MisfortuneView]).validate_python(data)


    async def get_population_dials(self) -> PopulationDialsView:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/god/population-dials")
        return PopulationDialsView.model_validate(data)

    async def update_population_dials(
        self, req: PopulationDialsUpdate
    ) -> PopulationDialsView:
        data = await self.call_json("PUT", f"{ADMIN_PREFIX}/god/population-dials", req)
        return PopulationDialsView.model_validate(data)


    async def get_spend_status(self) -> SpendStatusView:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/spend/status")
        return SpendStatusView.model_validate(data)


    async def list_alerts(self, limit: int = 50) -> list[AlertView]:
        data = await self.call_json(
            "GET", f"{ADMIN_PREFIX}/alerts", params={"limit": limit}
        )
        return TypeAdapter(list[AlertView]).validate_python(data)


    async def get_control_status(self) -> SimControlStatus:
        data = await self.call_json("GET", f"{ADMIN_PREFIX}/control/status")
        return SimControlStatus.model_validate(data)

    async def pause(self) -> SimControlStatus:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/control/pause")
        return SimControlStatus.model_validate(data)

    async def resume(self) -> SimControlStatus:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/control/resume")
        return SimControlStatus.model_validate(data)

    async def set_tick_speed(self, req: TickSpeedRequest) -> SimControlStatus:
        data = await self.call_json("PUT", f"{ADMIN_PREFIX}/control/tick-speed", req)
        return SimControlStatus.model_validate(data)

    async def checkpoint_now(self) -> CheckpointSummary:
        data = await self.call_json("POST", f"{ADMIN_PREFIX}/control/checkpoint-now")
        return CheckpointSummary.model_validate(data)
