"""Engine API key generation, hashing, and validation.

Key format: sk-sept-<48 URL-safe random chars>
Storage: SHA-256 hash passed via ENGINE_KEY_HASH env var (raw key never stored by engine)

Used by:
- Orchestrator: provision_engine_key() generates key, stores hash in orchestrator DB
- Server: reads hash from env var at startup, verify_key_against_state() per request

Hot rotation:
- AuthState holds primary_hash (always valid) and optional pending_hash (valid during overlap)
- init_auth_state() sets up the singleton at boot
- verify_key_against_state() checks both hashes (lock-free, GIL-safe in CPython)
- key_rotation.py owns the install_pending_key control op that mutates AuthState
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

KEY_PREFIX = "sk-sept-"

# A SHA-256 digest rendered as 64 lowercase hex chars. Used to validate any
# hash before it reaches bytes.fromhex() so a corrupt persisted/admin value can
# never raise out of boot.
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def is_valid_key_hash(hex_str: str) -> bool:
    """True iff ``hex_str`` is exactly 64 lowercase hex chars (a SHA-256 digest)."""
    return bool(HEX64_RE.match(hex_str or ""))


KEY_HASH_FILENAME = "engine_key_hash"


def persisted_key_hash_path() -> str:
    """Where the active key hash lives, under the engine root.

    Persisting it means a hot rotation survives a restart; without it boot
    would revert to the stale env-var hash. It is a hash, not a key — same
    exposure class as ENGINE_KEY_HASH — but it lives in ``credentials/`` under
    the 0700 root rather than a world-readable volume path.

    Resolved per call rather than captured at import, so the root can be set
    (or relocated in a test) without this module having already frozen it.
    """
    from src.infra.september_home import resolve_september_home, september_paths

    return str(september_paths(resolve_september_home())["credentials"] / KEY_HASH_FILENAME)


def generate_key() -> str:
    """Generate a new engine API key with the sk-sept- prefix."""
    return KEY_PREFIX + secrets.token_urlsafe(48)


def hash_key(raw_key: str) -> str:
    """SHA-256 hash of a raw key for secure storage (hex string)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def hash_key_bytes(raw_key: str) -> bytearray:
    """SHA-256 of raw_key as a mutable bytearray (for in-place zeroing)."""
    return bytearray(hashlib.sha256(raw_key.encode("utf-8")).digest())


def validate_format(key: str) -> bool:
    """Check that a key has the correct prefix and minimum length."""
    return key.startswith(KEY_PREFIX) and len(key) > len(KEY_PREFIX) + 16


# ---------------------------------------------------------------------------
# Hot-rotation auth state
# ---------------------------------------------------------------------------


@dataclass
class AuthState:
    """In-process auth state supporting zero-downtime key rotation.

    primary_hash: always valid; never None after boot.
    pending_hash: valid during the overlap window; None when no rotation active.
    overlap_expires_at: monotonic deadline for the pending hash; None when idle.
    swap_lock: guards the atomic swap in expire_rotation; NOT held during auth checks.
    """

    primary_hash: bytearray
    pending_hash: bytearray | None = None
    overlap_expires_at: float | None = None
    swap_lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False, repr=False)


auth_state: AuthState | None = None


def init_auth_state(key_hash_hex: str) -> AuthState:
    """Create the global AuthState from a hex-encoded SHA-256 hash.

    Call once at server boot after resolving ENGINE_KEY_HASH / ENGINE_API_KEY.
    """
    global auth_state
    auth_state = AuthState(primary_hash=bytearray(bytes.fromhex(key_hash_hex)))
    return auth_state


def get_auth_state() -> AuthState | None:
    """Return the global AuthState, or None if not yet initialised."""
    return auth_state


def reset_auth_state() -> None:
    """Clear the global AuthState. For testing only."""
    global auth_state
    auth_state = None


def verify_key_against_state(incoming_key: str, state: AuthState) -> bool:
    """Constant-time check of incoming_key against primary and pending hashes.

    Lock-free: reads `state` fields without acquiring swap_lock.  Under
    CPython the GIL makes bytearray attribute reads atomic enough that the
    brief swap window (a few microseconds) is handled by eventual consistency:
    a request sees either "both hashes valid" or "new hash only", both of
    which accept a valid new-key request.
    """
    if not validate_format(incoming_key):
        return False
    incoming = hash_key_bytes(incoming_key)
    if hmac.compare_digest(bytes(incoming), bytes(state.primary_hash)):
        return True
    if (
        state.pending_hash is not None
        and state.overlap_expires_at is not None
        and time.monotonic() < state.overlap_expires_at
    ):
        return hmac.compare_digest(bytes(incoming), bytes(state.pending_hash))
    return False


def install_pending_hash(state: AuthState, new_hash_hex: str, overlap_seconds: int) -> float:
    """Install a pre-hashed new key as the pending hash and arm the overlap.

    The hash analogue of the plaintext rotation path: the caller has already
    validated ``new_hash_hex`` (64-char hex) and the overlap bounds, and holds
    ``state.swap_lock``. Sets ``pending_hash`` so the new key validates during
    the window and returns the monotonic deadline at which it will be promoted.
    """
    state.pending_hash = bytearray(bytes.fromhex(new_hash_hex))
    deadline = time.monotonic() + overlap_seconds
    state.overlap_expires_at = deadline
    return deadline


def persist_key_hash(key_hash_hex: str) -> None:
    """Best-effort atomic, durable write of the active key hash.

    On boot the server prefers this persisted hash over the env-var hash so a
    hot rotation survives a restart. Never raises — a failed write only loses
    restart-durability, not the live (in-memory) rotation.

    Atomicity: write to a temp file in the SAME dir (so os.replace is a same-fs
    rename), fsync the bytes to disk, then os.replace onto the target — atomic
    on POSIX. A crash mid-write leaves only the temp file, never a partial
    target that would brick boot. The temp file is unlinked on any error.
    """
    target_path = persisted_key_hash_path()
    target_dir = os.path.dirname(target_path) or "."
    tmp_path = None
    try:
        # Create the parent at the write site rather than relying on boot
        # having run ensure_layout — the same discipline codex and Claude Code
        # apply, so this works from any entry point.
        os.makedirs(target_dir, mode=0o700, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".engine_key_hash.")
        # os.fdopen takes ownership of fd; its context manager closes fd on exit
        # (including on error), so no separate os.close is needed.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key_hash_hex)
            f.flush()
            os.fsync(f.fileno())
        # Atomic rename onto the target (same filesystem — same dir).
        os.replace(tmp_path, target_path)
        tmp_path = None
    except Exception:
        logger.exception("Failed to persist engine key hash to %s", target_path)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception:
                logger.exception("Failed to clean up temp key hash file %s", tmp_path)


# ---------------------------------------------------------------------------
# Admin auth state — orchestrator-only control credential (x-admin-key)
# ---------------------------------------------------------------------------
# Mirrors the engine-key path exactly: the orchestrator mints a per-engine
# admin secret, injects it as ENGINE_ADMIN_KEY (raw), and the engine hashes it
# at boot and validates x-admin-key against that hash. No overlap/rotation —
# the admin secret is a static control credential, not a product credential.

admin_hash: bytes | None = None


def init_admin_state(admin_hash_hex: str) -> None:
    """Set the global admin key hash from a hex-encoded SHA-256 hash.

    Call once at server boot after resolving ENGINE_ADMIN_KEY_HASH /
    ENGINE_ADMIN_KEY. If neither is present the server skips this and the admin
    surface reports key_not_configured.
    """
    global admin_hash
    admin_hash = bytes.fromhex(admin_hash_hex)


def get_admin_hash() -> bytes | None:
    """Return the global admin key hash, or None if no admin key is configured."""
    return admin_hash


def reset_admin_state() -> None:
    """Clear the global admin key hash. For testing only."""
    global admin_hash
    admin_hash = None


def verify_admin_key(incoming_key: str) -> bool:
    """Constant-time check of an incoming admin key against the configured hash.

    Returns False when no admin key is configured (``admin_hash`` is None) so
    callers can distinguish "not configured" via ``get_admin_hash()`` first.
    """
    if admin_hash is None:
        return False
    incoming = bytes(hash_key_bytes(incoming_key))
    return hmac.compare_digest(incoming, admin_hash)


# provision_engine_key, load_key_hash, revoke_key — moved to orchestrator.
# The engine reads ENGINE_KEY_HASH from env var at startup.
# These functions are no longer called by the engine container.
