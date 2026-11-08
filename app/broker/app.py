"""Bedrock broker FastAPI entrypoint.

Exposes an Anthropic-Messages-compatible `POST /v1/messages` so the
engine's herald can point `base_url` at this service unmodified
(DESIGN.md section 9, TASK spec). Module boundary (app/CONVENTIONS.md):
this is the ONLY package that may import boto3 - see broker/bedrock.py,
the one file in this package that actually does.

`create_app()` wires BrokerSettings.mock_mode to either the real
boto3/world/Supabase side of every Protocol in this package or the
in-memory test doubles - see build_service(). The module-level `app`
below is what `make run-broker` (uvicorn broker.app:app) and pytest's
`from broker.app import app` both import; it boots in mock mode by
default (BrokerSettings' own default - see config.py's docstring for
why), so `make test` never needs AWS credentials, a running world
process, or a Supabase project.

TRACE-FAILURE-IS-FATAL (TASK spec, trace-store track): "a failed batch
write currently vanishes into a log line while the sim keeps spending...
losing observations must never again be survivable." `GET /admin/trace`
below reports both BatchWriters' failure counts (trace.BatchWriter.stats()
- batches attempted/failed, rows dropped, consecutive failures); the
`POST /v1/messages` handler (both the streaming and non-streaming path,
via `trace_writers_halt_error()`) checks `is_halted` on both writers
BEFORE calling into BrokerService at all, and returns a 503 instead of
dispatching. This lives here rather than inside broker/service.py's
pipeline (a file this track does not own - see this repo's task split)
deliberately: gating at the one HTTP entry point every dispatch already
funnels through gets the identical guarantee (no Bedrock call, no spend,
without an un-recorded trace) without touching BrokerService's pipeline
at all.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from broker.bedrock import BedrockGateway, Boto3BedrockGateway, MockBedrockGateway
from broker.config import OPENAI_MODELS, BrokerSettings, export_aws_env, lane_key
from broker.errors import BrokerError, InvalidRequestError
from broker.lanes import MODEL_PREFERENCE_ORDER, CallClass, build_lane_pool
from broker.ledger import HttpLedgerClient, InMemoryLedgerClient
from broker.openai_gateway import OpenAIGateway
from broker.pricing import CostAccountant
from broker.service import BrokerService
from broker.spend import SpendGovernor, spend_already_recorded
from broker.trace import (
    BatchWriter,
    InMemorySink,
    SqliteTraceSink,
    broker_event_to_row,
    trace_record_to_row,
)
from broker.translate import sse_line

logger = logging.getLogger("broker.app")


def _governor_seeded_from(trace_client, settings: BrokerSettings) -> SpendGovernor:
    """A governor that knows what the run has already spent.

    INFRA-14. `spent_nano_dollars` started at 0 on every boot, so a broker
    restarted mid-run forgot the whole run's spend and would burn the cap a
    second time. The cap is the only thing between a bug and an unbounded
    Bedrock bill, and it was reset by a `docker compose restart`.
    """
    governor = SpendGovernor(
        cap_usd=settings.spend_cap_usd, warn_fraction=settings.spend_warn_fraction
    )
    if trace_client is None:
        return governor
    already = spend_already_recorded(trace_client)
    if already:
        governor.spent_nano_dollars = already
        logger.info(
            "spend governor resumed at $%.2f of a $%.2f cap, from %s",
            already / 1_000_000_000, settings.spend_cap_usd, settings.trace_sqlite_path,
        )
    return governor


def build_service(settings: BrokerSettings) -> BrokerService:
    # gateway_factory is called once per (model, region) lane by
    # build_lane_pool - broker/lanes.py's module docstring: "each lane
    # owns its own ... regional boto3 client", literally a fresh
    # Boto3BedrockGateway per lane in real mode. Mock mode shares no
    # state across lanes either; each lane still gets its own
    # MockBedrockGateway instance so per-lane call counts (visible via
    # GET /admin/lanes) reflect that specific lane's traffic, not every
    # lane's combined.
    if settings.mock_mode:

        def gateway_factory(model: str, region: str) -> BedrockGateway:
            return MockBedrockGateway()

        # autoseed=None keeps the strict fake every test uses; a positive
        # setting provisions wallets on demand so a real engine can
        # actually get through a turn. See InMemoryLedgerClient's field.
        ledger = InMemoryLedgerClient(
            autoseed_balance_cents=settings.mock_ledger_balance_cents or None
        )
        trace_sink = InMemorySink()
        event_sink = InMemorySink()
        # INFRA-14: mock mode has no durable store, so there is no prior
        # spend to recover and the governor correctly starts at zero.
        trace_client = None
    else:

        def gateway_factory(model: str, region: str) -> BedrockGateway:
            # An OpenAI model does not go through Bedrock at all. Returned
            # here rather than inside lanes.py so the boto3-import boundary
            # stays where app/CONVENTIONS.md puts it: this function is
            # already the one place that knows which client a lane gets.
            if model in OPENAI_MODELS:
                return OpenAIGateway(settings.openai_api_key, model)
            # The lane's inference profile, if this account invokes through
            # one. .get() returning None is the correct "bare model id"
            # default; a PARTIALLY filled mapping is already a boot-time
            # config error (BrokerSettings.validate), so None here always
            # means "no profiles configured at all", never "this one lane
            # was forgotten".
            return Boto3BedrockGateway(
                region,
                inference_profile_id=settings.bedrock_inference_profiles.get(
                    lane_key(model, region)
                ),
            )

        ledger = HttpLedgerClient(settings.world_base_url, settings.world_http_timeout_s)
        # Trace store is SQLite, not Supabase (DESIGN.md section 9). One
        # shared client for both sinks: two SqliteClients on the same file
        # would be two writers competing for the same WAL.
        from supabase.config import SupabaseConfig
        from supabase.sqlite_client import SqliteClient

        trace_client = SqliteClient(SupabaseConfig(sqlite_path=settings.trace_sqlite_path))
        # INFRA-08: the archiver that had no caller. It routes to S3 once
        # KASUKABE_S3_BUCKET is set and to a local directory otherwise -
        # that choice lives entirely in the client, so this line is the
        # same either way.
        from supabase.archiver import PayloadArchiver

        trace_sink = SqliteTraceSink(
            trace_client, "llm_calls", trace_record_to_row,
            archiver=PayloadArchiver(trace_client),
        )
        event_sink = SqliteTraceSink(trace_client, "events", broker_event_to_row)

    return BrokerService(
        settings=settings,
        ledger=ledger,
        lane_pool=build_lane_pool(settings, gateway_factory),
        spend_governor=_governor_seeded_from(trace_client, settings),
        cost_accountant=CostAccountant(),
        trace_writer=BatchWriter(
            sink=trace_sink,
            batch_size=settings.trace_batch_size,
            flush_interval_s=settings.trace_flush_interval_s,
        ),
        event_writer=BatchWriter(
            sink=event_sink,
            batch_size=settings.trace_batch_size,
            flush_interval_s=settings.trace_flush_interval_s,
        ),
    )


def get_service(request: Request) -> BrokerService:
    return request.app.state.service


def trace_writers_halt_error(service: BrokerService) -> "BrokerError | None":
    """None if both the trace and event BatchWriters are healthy; a
    BrokerError ready to return as the whole response otherwise.

    Checks trace_writer first only for a clearer message when both are
    halted at once (the common case: one sink failing usually means the
    same SqliteClient / same disk / same SQLite file backs both) - both
    are still checked, and either alone is enough to halt (see module
    docstring: "losing observations must never again be survivable" is
    not scoped to llm_calls only, events is a real observation too).
    503, not 500: the underlying sink recovering (BatchWriter.is_halted
    flips back false on the next successful flush - see trace.py) makes
    this a genuinely retryable condition, which is what herald's client
    already knows how to do with a 5xx.
    """
    for name, writer in (("trace_writer", service.trace_writer), ("event_writer", service.event_writer)):
        if writer.is_halted:
            error = BrokerError(
                f"broker.{name}: {writer.consecutive_failures} consecutive batch write "
                f"failures (last: {writer.last_failure_message!r}) - dispatch halted so no "
                f"more inference can go unrecorded. See GET /admin/trace."
            )
            error.status_code = 503
            error.anthropic_error_type = "overloaded_error"
            return error
    return None


def create_app(
    settings: "BrokerSettings | None" = None, service: "BrokerService | None" = None
) -> FastAPI:
    """Build a fully wired broker FastAPI app.

    `settings`/`service` overrides exist for tests (broker/tests/*.py
    construct a BrokerService by hand with a MockBedrockGateway seeded
    with a `canned_response_fn`, an InMemoryLedgerClient pre-seeded with
    known balances, and InMemorySink trace/event sinks, so a test can
    assert on exactly what got traced and exactly what got debited).
    Production and `make run-broker` both call `create_app()` with no
    arguments, which loads settings from the environment.
    """
    resolved_settings = settings or BrokerSettings.load_validated()

    # Move any AWS credentials from the .env into the process environment
    # BEFORE build_service constructs the six regional boto3 clients, since
    # that is the only chain boto3 will consult. Real mode only: mock mode
    # never touches boto3, so there is no reason to lift secret material
    # into the environment of a process that cannot use it.
    if not resolved_settings.mock_mode:
        exported = export_aws_env()
        if exported:
            # Names only. See export_aws_env's docstring.
            logger.info("loaded AWS credentials from .env: %s", ", ".join(exported))

    resolved_service = service or build_service(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # Flush every buffered trace/event and close outbound HTTP
        # clients on a clean shutdown. BatchWriter also self-flushes on
        # its own timer (see trace.py), so this is belt-and-suspenders
        # for the last partial batch, not the only flush path - a
        # TestClient used without `with` (most of broker's tests) never
        # runs this, which is fine: those tests call
        # `await service.trace_writer.flush()` directly instead of
        # relying on shutdown, for a deterministic assertion point.
        await resolved_service.close()

    app = FastAPI(title="kasukabe-broker", version="0.2.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.service = resolved_service

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Last-resort safety net so a bug anywhere in the pipeline never
        # leaks a raw traceback to an agent's herald (which would try, and
        # fail, to parse it as an Anthropic error body). Every EXPECTED
        # failure mode already raises a BrokerError and is handled above -
        # reaching this handler at all means an untested code path, so it
        # logs the full traceback (visible in server logs / pytest's
        # caplog) rather than swallowing it silently.
        logger.exception("broker.app: unhandled exception in %s", request.url.path)
        # INFRA-05: the traceback goes to the log, not to the caller.
        # `f"internal error: {exc}"` echoed raw exception text - file
        # paths, SQL, connection strings, whatever the bug happened to
        # carry - to whatever asked. The log line above already has the
        # full traceback for whoever is meant to see it.
        error = BrokerError("internal error - see the broker log")
        return JSONResponse(status_code=error.status_code, content=error.to_anthropic_body())

    @app.get("/health")
    async def health() -> dict[str, str]:
        # `inference` is the third and final provenance dimension, and the
        # only one this process can answer: are completions coming from
        # real Bedrock, or from MockBedrockGateway?
        #
        # It matters because the other two can BOTH be real while this one
        # is mock: real engine subprocesses, talking to a real world, being
        # really charged for every call - and receiving the literal string
        # "mock response" every time. That is a town whose plumbing works
        # and whose minds are canned, and nothing downstream could tell.
        return {
            "status": "ok",
            "service": "broker",
            "inference": "mock" if resolved_settings.mock_mode else "bedrock",
        }

    @app.get("/admin/lanes")
    async def admin_lanes(service: BrokerService = Depends(get_service)) -> list[dict[str, object]]:
        # broker/lanes.py's LanePool.status() - one row per lane (model x
        # region), circuit state, tokens available, trip count. TASK spec:
        # "the trace MUST record which lane+model actually served each
        # call" is satisfied by TraceRecord; this endpoint is the live
        # operational counterpart, current state per lane right now.
        return service.lane_pool.status()

    @app.get("/admin/trace")
    async def admin_trace(service: BrokerService = Depends(get_service)) -> dict[str, object]:
        # TASK spec: "Count failures, expose them on the broker admin
        # API." trace.BatchWriter.stats() per writer, plus the combined
        # gate POST /v1/messages actually enforces (see
        # trace_writers_halt_error) so an operator can see both "are we
        # about to lose data" (nonzero rows_dropped, rising
        # consecutive_failures) and "have we already stopped dispatching"
        # in one call.
        return {
            "trace_writer": service.trace_writer.stats(),
            "event_writer": service.event_writer.stats(),
            "dispatch_halted": trace_writers_halt_error(service) is not None,
            # INFRA-27. Calls billed for output that did not survive into
            # the record (Build Spec v2 section 7.3, threshold 9.4).
            # `content_loss` was appended to correctly and read by nothing -
            # not the runner, not preflight, not the observatory - so its
            # own comment's "a live alarm for a health surface to read" had
            # no health surface, and the threshold was enforced by grepping
            # a process that had already exited.
            #
            # The count, not the list: this is a gauge for a poller, and the
            # rows themselves are already in `events` as `broker.content.lost`
            # for anyone who wants the detail.
            "content_loss": len(service.content_loss),
        }

    @app.get("/admin/spend")
    async def admin_spend(service: BrokerService = Depends(get_service)) -> dict[str, object]:
        # TASK spec: "Expose spend, burn-rate $/hr, and projected
        # exhaustion over the admin API."
        snapshot = service.spend_governor.snapshot()
        # `model` so an operator watching the HUD can see WHICH model is
        # spending, not just how much. The wrong model leading is silent and
        # costs 3.6x, and a figure that does not say whose it is cannot
        # answer the question the HUD exists for.
        return {
            "model": resolved_settings.only_model
                     or resolved_settings.cognition_first_model
                     or (MODEL_PREFERENCE_ORDER[CallClass.COGNITION][0]),
            "model_is_exclusive": bool(resolved_settings.only_model),
            "spent_usd": snapshot.spent_usd,
            "cap_usd": snapshot.cap_usd,
            "fraction_used": snapshot.fraction_used,
            "state": snapshot.state.value,
            "burn_rate_usd_per_hr": snapshot.burn_rate_usd_per_hr,
            "projected_exhaustion_s": snapshot.projected_exhaustion_s,
        }

    @app.post("/v1/messages")
    async def messages(request: Request, service: BrokerService = Depends(get_service)):
        halt_error = trace_writers_halt_error(service)
        if halt_error is not None:
            return JSONResponse(status_code=halt_error.status_code, content=halt_error.to_anthropic_body())

        try:
            raw_body = await request.json()
        except json.JSONDecodeError:
            error = InvalidRequestError("request body is not valid JSON")
            return JSONResponse(status_code=error.status_code, content=error.to_anthropic_body())

        if not isinstance(raw_body, dict):
            error = InvalidRequestError("request body must be a JSON object")
            return JSONResponse(status_code=error.status_code, content=error.to_anthropic_body())

        headers = dict(request.headers)
        # INFRA-05: who is actually calling. Stamped by the broker from the
        # connection, never read from the request - a header a client could
        # set would be an authentication check the caller performs on
        # itself. Overwrites any inbound value of the same name for that
        # reason.
        headers["x-kasukabe-peer"] = request.client.host if request.client else ""

        if raw_body.get("stream"):
            return await stream_messages_response(service, raw_body, headers)

        try:
            response_body = await service.handle_messages(raw_body, headers)
        except BrokerError as e:
            return JSONResponse(status_code=e.status_code, content=e.to_anthropic_body())
        return JSONResponse(content=response_body)

    return app


async def stream_messages_response(
    service: BrokerService, raw_body: dict, headers: dict[str, str]
) -> "StreamingResponse | JSONResponse":
    """Fetch the first SSE event eagerly so a rejection that happens
    before any inference occurs (auth, allowlist, wallet check, or
    Bedrock refusing the call before streaming anything) still gets a
    real HTTP error status - see broker/service.py's
    handle_messages_stream docstring for why this is only possible
    before the first event, never after."""
    generator = service.handle_messages_stream(raw_body, headers)
    try:
        first_event = await generator.__anext__()
    except StopAsyncIteration:
        first_event = None
    except BrokerError as e:
        return JSONResponse(status_code=e.status_code, content=e.to_anthropic_body())

    async def body() -> AsyncIterator[str]:
        if first_event is not None:
            yield sse_line(first_event)
        async for event in generator:
            yield sse_line(event)

    return StreamingResponse(body(), media_type="text/event-stream")


# EVAL-17 / INFRA-11: BUILT WHEN ASKED FOR, NOT WHEN IMPORTED.
#
# This line used to run at import. `create_app()` builds a real
# `SqliteClient(SupabaseConfig(sqlite_path=settings.trace_sqlite_path))`,
# and that path defaults to `/data/kasukabe.db` - so on any machine
# without a `/data` directory, which is every developer machine and this
# one, `import broker.app` RAISED. `broker/tests/test_broker_app.py`
# imports it, so pytest reported "Interrupted: 1 error during collection"
# and the entire app suite ran zero tests.
#
# With `TRACE_SQLITE_PATH` set to a relative default instead, the other
# failure mode appeared: importing the module created `app/data/` inside
# the checkout, so merely collecting tests wrote to the working tree.
#
# PEP 562 module `__getattr__` keeps `broker.app:app` working unchanged -
# uvicorn asks for the attribute and gets a built app - while `import
# broker.app` now builds nothing. That matters because four launchers
# name `broker.app:app` (docker-compose, the systemd unit, run50.sh,
# runseries.sh) and a `--factory` flag would have had to reach all four.
#
# Built once and cached: two calls must not be two brokers holding two
# SQLite writers on one WAL.
_app = None


def __getattr__(name: str):
    """`broker.app:app`, resolved on demand."""
    global _app
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if _app is None:
        _app = create_app()
    return _app
