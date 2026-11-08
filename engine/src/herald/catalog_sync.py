"""Dynamic model catalog — poll citadel for model updates.

THE catalog: 100 percent citadel-synced, no static seed. Engine boots,
pulls citadel's ``/v1/models`` for its pool into the runtime overlay
(empty until that first successful pull), and a periodic poll re-pulls so
further changes (model added, removed, retired, price edits) propagate -
no engine restart needed.

Two mechanisms:
    1. Runtime overlay - dict updated by sync. The only model source.
       Lives in this module's process state.
    2. Periodic resync: a full /v1/models re-pull on a timer (default 5
       minutes), the sole update mechanism. The catalog is poll-only:
       there is no push or webhook receiver. While the catalog has NEVER
       synced, retries run on the accelerated first-sync schedule and the
       first success re-runs model validation warn-only.

Public API:
    await initialize(settings) — one-time at boot. Returns ok/degraded.
    get_catalog_overlay() — current overlay dict (model_id → ModelDef).
    await shutdown() — stop the periodic poll before process exit.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from september import AsyncSeptember

from src.config import Settings
from src.herald.catalog import ModelCost, ModelDef

logger = logging.getLogger(__name__)


runtime_overlay: dict[str, ModelDef] = {}
periodic_task: asyncio.Task | None = None
active_settings: Settings | None = None
sept_client: AsyncSeptember | None = None
ever_synced: bool = False

FIRST_SYNC_RETRY_SCHEDULE_S: tuple[float, ...] = (10.0, 30.0)
FIRST_SYNC_RETRY_STEADY_S = 60.0


RESYNC_INTERVAL_S = 300

SYNC_TIMEOUT_S = 10.0

FULL_RESYNC_SHRINK_FLOOR = 0.5


def _resync_interval_s() -> float:
    """Full-resync interval — env/settings-driven, falling back to the
    module default when no settings are active (e.g. very early boot)."""
    if active_settings is not None and active_settings.catalog_resync_interval_s > 0:
        return float(active_settings.catalog_resync_interval_s)
    return float(RESYNC_INTERVAL_S)


def get_client() -> AsyncSeptember | None:
    """Shared SDK client — built once, reused by every poll so the
    5-minute resync rides one connection pool instead of paying a fresh
    TLS handshake per pull. Closed by shutdown()."""
    global sept_client
    if active_settings is None or not active_settings.cdt_url or not active_settings.cdt_key:
        return None
    if sept_client is None:
        sept_client = AsyncSeptember(
            citadel={
                "api_key": active_settings.cdt_key,
                "base_url": active_settings.cdt_url,
            },
            timeout=SYNC_TIMEOUT_S,
        )
    return sept_client


def get_catalog_overlay() -> dict[str, ModelDef]:
    """Current runtime catalog (the only model source; empty pre-sync)."""
    return dict(runtime_overlay)


def lookup(model_id: str) -> ModelDef | None:
    """Synced-catalog lookup. None for unknown ids and before first sync."""
    return runtime_overlay.get(model_id)


def row_to_modeldef(row: dict[str, Any]) -> ModelDef:
    """Convert a citadel /v1/models poll row to ModelDef.

    Citadel uses an OpenAI-style ``id`` field on /v1/models responses;
    ``model_name``/``model_id`` are accepted as fallbacks. Pricing fields
    appear as ``*_price_per_mtok`` on /v1/models; the bare ``*_per_mtok``
    fallback is kept defensively for robustness against field-name drift.
    """
    model_id = row.get("id") or row.get("model_name") or row.get("model_id") or ""
    in_price = row.get("input_price_per_mtok") or row.get("input_per_mtok") or 0
    out_price = row.get("output_price_per_mtok") or row.get("output_per_mtok") or 0
    cache_read = row.get("cache_read_per_mtok") or 0
    cache_write = row.get("cache_write_per_mtok") or 0
    cost = None
    if any((in_price, out_price, cache_read, cache_write)):
        cost = ModelCost(
            input_per_1m=float(in_price or 0),
            output_per_1m=float(out_price or 0),
            cache_read_per_1m=float(cache_read or 0),
            cache_create_per_1m=float(cache_write or 0),
        )
    else:
        logger.warning(
            "catalog_sync: model %r has no resolvable price "
            "(input/output/cache all absent or 0) — it will be cost-estimated "
            "at $0; check citadel pricing / field names",
            model_id or "<unknown>",
        )
    provider = row.get("provider") or row.get("owned_by") or "anthropic"
    return ModelDef(
        id=model_id,
        provider=provider,
        name=model_id,
        context_window=int(row.get("context_window") or 200_000),
        max_output_tokens=int(row.get("max_output") or 4096),
        supports_tools=bool(row.get("supports_tools", True)),
        supports_vision=bool(row.get("supports_vision", False)),
        cost=cost,
        tier=row.get("tier") or "standard",
    )


async def initialize(settings: Settings) -> bool:
    """Pull /v1/models, populate overlay, start the periodic resync poll.

    Returns True on success. False on degraded (citadel unreachable - engine
    still boots with an EMPTY catalog and self-heals via the accelerated
    first-sync retry; there is no static seed). Never raises.
    """
    global active_settings, periodic_task, sept_client, ever_synced
    if sept_client is not None:
        try:
            await sept_client.aclose()
        except Exception:
            logger.debug("catalog_sync: closing stale client failed", exc_info=True)
        sept_client = None
    active_settings = settings
    ever_synced = False

    if not settings.cdt_url or not settings.cdt_key:
        logger.warning("catalog_sync: CDT_URL/CDT_KEY missing - catalog stays empty")
        return False

    ok = await full_sync()

    if periodic_task is None:
        periodic_task = asyncio.create_task(periodic_resync_loop())

    return ok


async def shutdown() -> None:
    """Stop the resync poll and close the shared client. Best-effort."""
    global periodic_task, sept_client
    if periodic_task is not None:
        periodic_task.cancel()
        try:
            await periodic_task
        except BaseException:  # noqa: BLE001 (CancelledError is BaseException)
            pass
        periodic_task = None
    if sept_client is not None:
        await sept_client.aclose()
        sept_client = None


async def full_sync() -> bool:
    """Pull /v1/models and reconcile the overlay.

    Reconciliation is MERGE-on-success, not clear()+update(): a successful,
    non-empty response is merged over the existing overlay, and a model the
    response OMITS is only dropped when the response looks complete (its row
    count is at least ``FULL_RESYNC_SHRINK_FLOOR`` of the current overlay). A
    suspiciously short/empty response is treated as a partial fetch — its rows
    are still applied (prices/capabilities refresh), but nothing is evicted, so
    a transiently-missing model is not removed from routing for a whole resync
    interval. An entirely empty/failed pull leaves the overlay untouched.
    Idempotent; stable for consumers (router, etc.) when nothing changed."""
    global ever_synced
    sept = get_client()
    if sept is None:
        return False
    try:
        new_overlay: dict[str, ModelDef] = {}
        page = await sept.citadel.models.list()
        async for m in page:
            md = row_to_modeldef(m)
            new_overlay[md.id] = md

        if not new_overlay:
            logger.warning(
                "catalog_sync: /v1/models returned 0 models — keeping existing "
                "overlay (%d models) rather than clearing",
                len(runtime_overlay),
            )
            return False

        prev_count = len(runtime_overlay)
        complete = prev_count == 0 or len(new_overlay) >= prev_count * FULL_RESYNC_SHRINK_FLOOR
        if complete:
            runtime_overlay.clear()
            runtime_overlay.update(new_overlay)
            logger.info(
                "catalog_sync: %d models loaded from citadel",
                len(new_overlay),
            )
        else:
            runtime_overlay.update(new_overlay)
            logger.warning(
                "catalog_sync: pull returned only %d models vs %d held "
                "(< %.0f%%) — treating as partial: merged updates, kept "
                "omitted models instead of dropping them",
                len(new_overlay),
                prev_count,
                FULL_RESYNC_SHRINK_FLOOR * 100,
            )
        ever_synced = True
        return True
    except Exception as e:
        logger.warning("catalog_sync: full_sync failed (%s) - catalog unchanged", e)
        return False


async def periodic_resync_loop() -> None:
    """The catalog poll. Re-pulls /v1/models on the (env-driven) resync
    interval to pick up catalog changes (model added/removed/retired, price
    edits). The sole update path: the catalog is poll-only, there is no push
    or webhook receiver.

    While the catalog has NEVER synced (degraded boot), retries run on the
    accelerated first-sync schedule instead, and the first success re-runs
    model validation warn-only so unverified configured ids finally get
    checked and loudly reported."""
    retry_index = 0
    while True:
        if ever_synced:
            delay = _resync_interval_s()
        elif retry_index < len(FIRST_SYNC_RETRY_SCHEDULE_S):
            delay = FIRST_SYNC_RETRY_SCHEDULE_S[retry_index]
            retry_index += 1
        else:
            delay = FIRST_SYNC_RETRY_STEADY_S
        await asyncio.sleep(delay)
        try:
            was_synced = ever_synced
            ok = await full_sync()
            if ok and not was_synced:
                logger.info("catalog_sync: first successful sync (late) - catalog live")
                revalidate_models_after_late_sync()
        except Exception:
            logger.warning("catalog_sync: periodic resync errored", exc_info=True)


def revalidate_models_after_late_sync() -> None:
    """Warn-only model re-validation once a late first sync lands.

    Boot already happened (degraded, warnings only), so aborting is no
    longer an option; findings are logged loudly instead. Lazy import
    breaks the cycle (model_validation imports this module)."""
    if active_settings is None:
        return
    try:
        from src.herald.model_validation import validate_resolved_models

        fatal, warnings = validate_resolved_models(active_settings, synced=True)
        for finding in warnings:
            logger.warning("model validation (late sync): %s", finding)
        for finding in fatal:
            logger.error(
                "model validation (late sync): %s - the engine booted degraded "
                "and cannot abort now; fix the config and restart",
                finding,
            )
    except Exception:
        logger.warning("catalog_sync: late model re-validation errored", exc_info=True)
