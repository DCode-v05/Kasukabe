"""Hot API key rotation for engine containers.

Backs the ``/admin/install-pending-key`` route via a single
``install_pending_key`` function. The orchestrator owns key generation and
sends only the new key's sha256 hex (``new_hash``) — the engine never sees the
new plaintext. After calling this, the engine accepts both the old and new key
for ``overlap_seconds`` (bounded by env vars). A background asyncio task then
zeros the old key hash and promotes the new one, with no container restart. The
new hash is persisted to /data so the rotation survives a container restart.

Environment variables:
    BAP_KEY_ROTATION_MIN_OVERLAP_SECONDS  — default 0
    BAP_KEY_ROTATION_MAX_OVERLAP_SECONDS  — default 300
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time

from src.auth import (
    AuthState,
    get_auth_state,
    install_pending_hash,
    persist_key_hash,
)
from src.errors import EngineError, ErrorCode

logger = logging.getLogger(__name__)

# 64 lowercase hex chars = a SHA-256 digest. Matches init_auth_state(hex)'s
# expectation; rejecting non-hex here keeps bytes.fromhex from raising deep in
# the swap path.
HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def get_overlap_bounds() -> tuple[int, int]:
    min_s = int(os.environ.get("BAP_KEY_ROTATION_MIN_OVERLAP_SECONDS", "0"))
    max_s = int(os.environ.get("BAP_KEY_ROTATION_MAX_OVERLAP_SECONDS", "300"))
    return min_s, max_s


async def install_pending_key(new_hash: str, overlap_seconds: int = 60) -> dict:
    """Install a pre-hashed new API key into the running engine.

    The caller (the orchestrator) must present the engine's per-engine admin
    secret in the ``x-admin-key`` header (enforced by ``validate_admin``
    before this function is called).

    Args:
        new_hash: The new key's SHA-256 digest as 64 lowercase hex chars. The
            engine never sees the new plaintext.
        overlap_seconds: How long (in wall-clock seconds) both the old and
            new keys remain valid simultaneously.  0 means immediate switch.
            Bounded by BAP_KEY_ROTATION_MIN/MAX_OVERLAP_SECONDS env vars.

    Returns:
        {"rotation_id": str, "overlap_expires_at": float}

    Raises:
        EngineError(INVALID_PARAMS): new_hash is not 64-char lowercase hex, or
            overlap is outside the env-var bounds (http_status 422).
        EngineError(ROTATION_IN_PROGRESS): a rotation is already pending
            (http_status 409, retryable).
        EngineError(INTERNAL_ERROR): auth state not initialised (http_status 500).
    """
    state = get_auth_state()
    if state is None:
        raise EngineError(code=ErrorCode.INTERNAL_ERROR, message="auth state not initialised")

    # Validate the hash payload: must be a 64-char lowercase hex SHA-256 digest.
    if not HEX64_RE.match(new_hash or ""):
        raise EngineError(
            code=ErrorCode.INVALID_PARAMS,
            message="new_hash must be 64 lowercase hex characters",
        )

    # Validate overlap bounds.
    min_s, max_s = get_overlap_bounds()
    if overlap_seconds < min_s or overlap_seconds > max_s:
        raise EngineError(
            code=ErrorCode.INVALID_PARAMS,
            message=f"overlap_seconds must be between {min_s} and {max_s}",
            details={"min": min_s, "max": max_s},
        )

    # Under the lock: reject concurrent rotations, then install or immediately swap.
    start_wall = time.time()
    async with state.swap_lock:
        if state.pending_hash is not None:
            # Transient conflict (retryable 409), distinct from the hard-reject
            # INVALID_PARAMS used for bad hash / out-of-bounds overlap.
            raise EngineError(code=ErrorCode.ROTATION_IN_PROGRESS)

        rotation_id = "rot_" + secrets.token_hex(6)
        initiator_prefix = state.primary_hash.hex()[:8]

        if overlap_seconds == 0:
            # Immediate switch: swap and zero in-place without creating a background task.
            old_primary = state.primary_hash
            state.primary_hash = bytearray(bytes.fromhex(new_hash))
            state.pending_hash = None
            state.overlap_expires_at = None
            old_primary[:] = bytes(len(old_primary))
            expires_at = time.monotonic()
        else:
            expires_at = install_pending_hash(state, new_hash, overlap_seconds)

    # Persist only the LIVE primary so /data always reflects the key that
    # actually authenticates after a restart. On the immediate-swap path the
    # new hash is already primary, so persist it now. On the overlap path the
    # OLD hash is still primary during the window — expire_rotation persists
    # the new hash at promotion, not here.
    if overlap_seconds == 0:
        persist_key_hash(new_hash)

    await emit_started(rotation_id, overlap_seconds, initiator_prefix)

    if overlap_seconds == 0:
        await emit_completed(rotation_id, time.time() - start_wall)
        logger.info("Key rotation completed immediately: rotation_id=%s", rotation_id)
    else:
        # Schedule expiry in the background.
        asyncio.create_task(
            expire_rotation(state, rotation_id, overlap_seconds, start_wall),
            name=f"key-rotation-{rotation_id}",
        )

    return {"rotation_id": rotation_id, "overlap_expires_at": expires_at}


async def expire_rotation(
    state: AuthState,
    rotation_id: str,
    overlap_seconds: int,
    start_wall: float,
) -> None:
    """Background task: wait for the overlap window, then swap hashes."""
    if overlap_seconds > 0:
        await asyncio.sleep(overlap_seconds)

    async with state.swap_lock:
        if state.pending_hash is None:
            # Already handled (e.g., state was reset in tests).
            return

        old_primary = state.primary_hash

        # Atomic swap — primary_hash is never None at any observable point.
        state.primary_hash = state.pending_hash
        state.pending_hash = None
        state.overlap_expires_at = None

        # Capture the now-live primary inside the lock so the persisted /data
        # value cannot race a concurrent swap.
        promoted_hex = state.primary_hash.hex()

        # Best-effort zeroing of the old hash bytes from process memory.
        old_primary[:] = bytes(len(old_primary))

    # Persist the promoted hash AFTER releasing the lock — /data now reflects
    # the new live primary (it held the OLD hash during the overlap window).
    persist_key_hash(promoted_hex)

    duration = time.time() - start_wall
    await emit_completed(rotation_id, duration)
    logger.info(
        "Key rotation completed: rotation_id=%s duration=%.3fs",
        rotation_id,
        duration,
    )


async def emit_started(
    rotation_id: str,
    overlap_seconds: int,
    initiator_prefix: str,
) -> None:
    try:
        from src.raven import api as raven_api

        await raven_api.track_untyped(
            "key_rotation_started",
            {
                "rotation_id": rotation_id,
                "overlap_seconds": overlap_seconds,
                "initiator_key_hash_prefix": initiator_prefix,
                "timestamp": time.time(),
            },
        )
    except Exception:
        logger.exception("Failed to emit key_rotation_started telemetry")


async def emit_completed(rotation_id: str, duration_seconds: float) -> None:
    try:
        from src.raven import api as raven_api

        await raven_api.track_untyped(
            "key_rotation_completed",
            {
                "rotation_id": rotation_id,
                "duration_seconds": duration_seconds,
                "timestamp": time.time(),
            },
        )
    except Exception:
        logger.exception("Failed to emit key_rotation_completed telemetry")
