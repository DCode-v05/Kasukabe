"""Console FastAPI entrypoint (DESIGN.md section 8, TASK spec: "the
operator's President+God console as a separate page served by the same
FastAPI server, talking to world-server admin APIs").

"The same FastAPI server" is this module's `app`: one process serves the
console's static page (GET /) and every one of its JSON routes (/api/*),
so the browser only ever talks to one origin - no CORS, no second hop
the operator has to reason about. Every /api/* route is presentation
plumbing only (app/CONVENTIONS.md: "game/ ... never computes game logic
locally"): verify the operator token (auth.py), call the matching
WorldAdminClient method, return whatever world said. All the actual
decisions - does this petition get approved, does the map grow, does the
prayer get silently denied - happen inside world/, reached over HTTP
through world_client.py.

`create_app()` mirrors broker/app.py's factory shape exactly (settings/
client overrides for tests, a module-level `app = create_app()` for
`make run-console` and `from game.console.app import app`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request

from game.console.framing import (
    frame_at_risk,
    frame_business,
    frame_household,
    frame_skill_spread,
    frame_treasury,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from game.console.auth import require_operator_token
from game.console.config import ConsoleSettings
from game.console.errors import ConsoleError
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
from game.console.world_client import WorldAdminClient

logger = logging.getLogger("game.console.app")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_world_client(request: Request) -> WorldAdminClient:
    return request.app.state.world_client


president_router = APIRouter(prefix="/president", tags=["president"])


@president_router.get("/public-works", response_model=PublicWorksView)
async def public_works(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> PublicWorksView:
    """GROW-25: the growth path in one panel - open tenders, permits and
    licences, sites with their labour progress, and finished buildings."""
    return await client.get_public_works()


@president_router.get("/petitions", response_model=list[LandPetitionView])
async def list_petitions(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[LandPetitionView]:
    return await client.list_land_petitions()


@president_router.post("/petitions/{petition_id}/approve", response_model=PetitionDecisionView)
async def approve_petition(
    petition_id: str,
    req: ApprovePetitionRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> PetitionDecisionView:
    return await client.approve_land_petition(petition_id, req)


@president_router.post("/petitions/{petition_id}/deny", response_model=LandPetitionView)
async def deny_petition(
    petition_id: str,
    req: DenyPetitionRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> LandPetitionView:
    return await client.deny_land_petition(petition_id, req)


@president_router.get("/amendments", response_model=list[AmendmentProposalView])
async def list_amendments(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[AmendmentProposalView]:
    return await client.list_amendments()


@president_router.post("/amendments/{proposal_id}/sign", response_model=AmendmentProposalView)
async def sign_amendment(
    proposal_id: str,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> AmendmentProposalView:
    return await client.sign_amendment(proposal_id)


@president_router.post("/amendments/{proposal_id}/veto", response_model=AmendmentProposalView)
async def veto_amendment(
    proposal_id: str,
    req: VetoAmendmentRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> AmendmentProposalView:
    return await client.veto_amendment(proposal_id, req)


@president_router.post("/broadcast", response_model=BroadcastView)
async def broadcast_address(
    req: BroadcastRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> BroadcastView:
    return await client.broadcast_address(req)


@president_router.get("/broadcasts", response_model=list[BroadcastView])
async def list_broadcasts(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[BroadcastView]:
    return await client.list_broadcasts()


@president_router.post("/honors", response_model=HonorView)
async def grant_honor(
    req: HonorRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> HonorView:
    return await client.grant_honor(req)


@president_router.get("/honors", response_model=list[HonorView])
async def list_honors(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[HonorView]:
    return await client.list_honors()


god_router = APIRouter(prefix="/god", tags=["god"])


@god_router.get("/prayers", response_model=list[PrayerView])
async def list_prayers(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[PrayerView]:
    return await client.list_prayers()


@god_router.post("/prayers/{prayer_id}/grant", response_model=PrayerDecisionView)
async def grant_prayer(
    prayer_id: str,
    req: GrantPrayerRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> PrayerDecisionView:
    return await client.grant_prayer(prayer_id, req)


@god_router.post("/prayers/{prayer_id}/silent-deny", response_model=PrayerDecisionView)
async def silent_deny_prayer(
    prayer_id: str,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> PrayerDecisionView:
    """No request body: DESIGN.md section 8's silent-deny leaves no trace
    visible to agents, so there is no "response note" field to offer the
    operator here the way grant_prayer has one - anything typed would
    have nowhere honest to go."""
    return await client.silent_deny_prayer(prayer_id)


@god_router.get("/blessings", response_model=list[BirthRequestView])
async def list_blessings(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[BirthRequestView]:
    return await client.list_blessing_requests()


@god_router.post("/blessings/{request_id}/grant", response_model=BirthDecisionView)
async def grant_blessing(
    request_id: str,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> BirthDecisionView:
    return await client.grant_blessing(request_id)


@god_router.post("/blessings/{request_id}/deny", response_model=BirthDecisionView)
async def deny_blessing(
    request_id: str,
    req: DenyBlessingRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> BirthDecisionView:
    return await client.deny_blessing(request_id, req)


@god_router.post("/disasters", response_model=DisasterView)
async def trigger_disaster(
    req: DisasterRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> DisasterView:
    return await client.trigger_disaster(req)


@god_router.get("/disasters", response_model=list[DisasterView])
async def list_disasters(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[DisasterView]:
    return await client.list_disasters()


@god_router.post("/miracles", response_model=MiracleView)
async def cast_miracle(
    req: MiracleRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> MiracleView:
    return await client.cast_miracle(req)


@god_router.get("/miracles", response_model=list[MiracleView])
async def list_miracles(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[MiracleView]:
    return await client.list_miracles()


@god_router.post("/misfortunes", response_model=MisfortuneView)
async def cast_misfortune(
    req: MisfortuneRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> MisfortuneView:
    return await client.cast_misfortune(req)


@god_router.get("/misfortunes", response_model=list[MisfortuneView])
async def list_misfortunes(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[MisfortuneView]:
    return await client.list_misfortunes()


@god_router.get("/population-dials", response_model=PopulationDialsView)
async def get_population_dials(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> PopulationDialsView:
    return await client.get_population_dials()


@god_router.put("/population-dials", response_model=PopulationDialsView)
async def update_population_dials(
    req: PopulationDialsUpdate,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> PopulationDialsView:
    return await client.update_population_dials(req)


spend_router = APIRouter(prefix="/spend", tags=["spend"])


@spend_router.get("/status", response_model=SpendStatusView)
async def spend_status(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> SpendStatusView:
    return await client.get_spend_status()


alerts_router = APIRouter(prefix="/alerts", tags=["alerts"])


@alerts_router.get("", response_model=list[AlertView])
async def list_alerts(
    limit: int = 50,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[AlertView]:
    return await client.list_alerts(limit=limit)


town_router = APIRouter(prefix="/town", tags=["town"])


@town_router.get("/overview")
async def town_overview(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> dict[str, Any]:
    """The whole town on one screen: who is about to go hungry, which
    businesses are dying, whether the government can pay for itself, and
    what the place is good at."""
    at_risk = await client.town_at_risk()
    businesses = await client.town_businesses()
    pol = await client.town_policy_and_treasury()
    skills = await client.town_skills()
    flow = await client.town_money_flow()

    days_observed = max((len(b.get("by_day", []) or []) for b in businesses), default=0)

    return {
        "days_observed": days_observed,
        "headline": frame_at_risk(at_risk, days_observed).to_dict(),
        "treasury": frame_treasury(
            pol.get("treasury", {}), pol.get("policy", {}), days_observed
        ).to_dict(),
        "skills": frame_skill_spread(skills).to_dict(),
        "at_risk": [frame_household(h, days_observed).to_dict() for h in at_risk],
        "businesses": [frame_business(b).to_dict() for b in businesses],
        "money_flow": flow,
    }


@town_router.get("/households")
async def town_households(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[dict[str, Any]]:
    households = await client.town_households()
    businesses = await client.town_businesses()
    days = max((len(b.get("by_day", []) or []) for b in businesses), default=0)
    return [frame_household(h, days).to_dict() for h in households]


@town_router.get("/wealth")
async def town_wealth(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> dict[str, Any]:
    return await client.town_wealth()


@town_router.get("/terms")
async def town_terms(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> list[dict[str, Any]]:
    return await client.town_terms()


control_router = APIRouter(prefix="/control", tags=["control"])


@control_router.get("/status", response_model=SimControlStatus)
async def control_status(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> SimControlStatus:
    return await client.get_control_status()


@control_router.post("/pause", response_model=SimControlStatus)
async def pause_sim(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> SimControlStatus:
    return await client.pause()


@control_router.post("/resume", response_model=SimControlStatus)
async def resume_sim(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> SimControlStatus:
    return await client.resume()


@control_router.put("/tick-speed", response_model=SimControlStatus)
async def set_tick_speed(
    req: TickSpeedRequest,
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> SimControlStatus:
    return await client.set_tick_speed(req)


@control_router.post("/checkpoint-now", response_model=CheckpointSummary)
async def checkpoint_now(
    token: str = Depends(require_operator_token),
    client: WorldAdminClient = Depends(get_world_client),
) -> CheckpointSummary:
    return await client.checkpoint_now()


def create_app(
    settings: ConsoleSettings | None = None,
    world_client: WorldAdminClient | None = None,
) -> FastAPI:
    """Build a fully wired console FastAPI app.

    `settings`/`world_client` overrides exist for tests (see
    tests/fake_world_admin.py: a WorldAdminClient built with
    httpx.MockTransport instead of a real connection), the same seam
    broker/app.py's create_app() uses for BrokerService. Production and
    `make run-console` both call create_app() with no arguments, which
    loads settings from the environment and opens a real WorldAdminClient
    against WORLD_BASE_URL.
    """
    resolved_settings = settings or ConsoleSettings.load_validated()
    if resolved_settings.operator_token_was_generated:
        logger.warning(
            "OPERATOR_TOKEN not set - generated an ephemeral token for this process. "
            "Set OPERATOR_TOKEN in the environment for a token that survives a "
            "restart. Ephemeral operator token: %s",
            resolved_settings.operator_token,
        )
    resolved_client = world_client or WorldAdminClient(
        base_url=resolved_settings.world_base_url,
        operator_token=resolved_settings.operator_token,
        timeout_s=resolved_settings.world_http_timeout_s,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await resolved_client.close()

    app = FastAPI(title="kasukabe-console", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.world_client = resolved_client

    @app.exception_handler(ConsoleError)
    async def console_error_handler(request: Request, exc: ConsoleError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("game.console.app: unhandled exception in %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"internal error: {exc}"})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "console"}

    app.include_router(president_router, prefix="/api")
    app.include_router(god_router, prefix="/api")
    app.include_router(spend_router, prefix="/api")
    app.include_router(alerts_router, prefix="/api")
    app.include_router(control_router, prefix="/api")
    app.include_router(town_router, prefix="/api")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        @app.get("/console")
        async def console_page() -> FileResponse:
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()
