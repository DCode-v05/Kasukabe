"""Tests for hot API key rotation (src/key_rotation.py).

All tests use real asyncio and real in-process auth state.  Telemetry tests
also use a real in-memory SQLite store via the shared `db` fixture.

``install_pending_key`` is reached via POST /admin/install-pending-key
(orchestrator-only, ``x-admin-key`` auth) and surfaces failures as
``EngineError``:
  - bad new_hash (not 64-hex) / overlap-out-of-bounds -> INVALID_PARAMS
    (http_status 422, not retryable — hard reject).
  - rotation-already-in-progress -> ROTATION_IN_PROGRESS (http_status 409,
    retryable — a transient conflict, distinct from bad input).
  - auth state not initialised -> INTERNAL_ERROR (http_status 500).

The orchestrator owns key generation and sends only the new key's sha256 hex —
the engine never sees the new plaintext.

Test map:
  Unit:
    test_install_basic                  — both keys auth during overlap
    test_install_expires                — after overlap (0s), only new key auths
    test_install_concurrent_rejected    — second install -> ROTATION_IN_PROGRESS (409)
    test_install_requires_current_auth  — wrong current key is not accepted
    test_install_overlap_bounds         — outside env-var bounds -> INVALID_PARAMS (422)
    test_install_zero_overlap           — overlap=0 switches immediately
    test_install_emits_telemetry        — start + complete events with correct fields
    test_install_zeros_old_hash         — old bytearray zeroed after swap
    test_install_bad_hash               — non-64-hex new_hash -> INVALID_PARAMS (422)
    test_install_auth_not_initialised   — no auth state -> INTERNAL_ERROR (500)
    test_overlap_zero_persists_immediately      — overlap=0 writes new hash to /data at once
    test_overlap_persists_at_promotion_not_install — overlap>0: /data flips to new hash only at promotion
  Admin auth (src/auth.py):
    test_verify_admin_key_valid/wrong/unconfigured
  Integration:
    test_install_full_product_path      — install then next verify uses new key only
    test_primary_hash_never_none        — invariant: primary_hash is always set
    test_no_plaintext_key_in_state      — plaintext never stored in AuthState
    test_durable_hash_preferred_on_boot — a boot that reads /data prefers it
    test_corrupt_persisted_hash_falls_back_to_env — invalid /data ignored, env key used
    test_install_during_inflight_streaming — in-flight SSE survives rotation
  Route (through the app):
    test_install_pending_key_route_*    — POST /admin/install-pending-key auth + happy path
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from src import auth
from src.auth import (
    AuthState,
    generate_key,
    get_auth_state,
    hash_key,
    init_admin_state,
    init_auth_state,
    is_valid_key_hash,
    reset_admin_state,
    reset_auth_state,
    verify_admin_key,
    verify_key_against_state,
)
from src.errors import EngineError, ErrorCode
from src.key_rotation import expire_rotation, install_pending_key


@pytest_asyncio.fixture(autouse=True)
async def fresh_auth_state(tmp_path, monkeypatch):
    """Isolate AuthState + admin state, and point the engine root at a tmp dir.

    Redirecting SEPTEMBER_HOME rather than patching a module constant means
    these tests exercise the real path resolution, so a change to where
    credentials live is caught here instead of silently bypassed.
    """
    from src.infra.september_home import ensure_layout

    reset_auth_state()
    reset_admin_state()
    monkeypatch.setenv("SEPTEMBER_HOME", str(tmp_path))
    ensure_layout(tmp_path)
    yield
    reset_auth_state()
    reset_admin_state()


@pytest_asyncio.fixture
async def auth_state() -> AuthState:
    """AuthState initialised with a freshly-generated key."""
    key = generate_key()
    state = init_auth_state(hash_key(key))
    state.current_key = key
    return state


@pytest_asyncio.fixture
async def raven(db):
    """Initialised raven store. Requires Docker (vec0 SQLite extension)."""
    from src.raven import api as raven_api

    raven_api.init(db)
    yield raven_api
    raven_api.store = None
    raven_api.db_pool = None


def make_key() -> str:
    return generate_key()


async def call_install(new_key: str, overlap_seconds: int = 60) -> dict:
    """Hash a plaintext key and call install_pending_key (the engine receives
    only the hex; the HTTP auth layer already validated the caller)."""
    return await install_pending_key(new_hash=hash_key(new_key), overlap_seconds=overlap_seconds)


class TestInstallBasic:
    @pytest.mark.asyncio
    async def test_install_basic(self, auth_state):
        """Both old and new keys authenticate during the overlap window."""
        old_key = auth_state.current_key
        new_key = make_key()

        result = await call_install(new_key, overlap_seconds=60)
        assert "rotation_id" in result
        assert result["rotation_id"].startswith("rot_")
        assert result["overlap_expires_at"] > time.monotonic()

        state = get_auth_state()
        assert verify_key_against_state(old_key, state), "old key should still auth during overlap"
        assert verify_key_against_state(new_key, state), "new key should auth during overlap"


class TestInstallExpires:
    @pytest.mark.asyncio
    async def test_install_expires(self, auth_state):
        """After the overlap window expires, only the new key authenticates."""
        old_key = auth_state.current_key
        new_key = make_key()

        await call_install(new_key, overlap_seconds=0)
        await asyncio.sleep(0)

        state = get_auth_state()
        assert not verify_key_against_state(old_key, state), "old key must not auth after expiry"
        assert verify_key_against_state(new_key, state), "new key must auth after expiry"


class TestInstallConcurrentRejected:
    @pytest.mark.asyncio
    async def test_install_concurrent_rejected(self, auth_state):
        """Second install while a rotation is pending -> ROTATION_IN_PROGRESS
        (409, retryable — a transient conflict, not a hard-reject 422)."""
        new_key_1 = make_key()
        new_key_2 = make_key()

        await call_install(new_key_1, overlap_seconds=300)

        with pytest.raises(EngineError) as exc_info:
            await call_install(new_key_2, overlap_seconds=60)

        assert exc_info.value.code is ErrorCode.ROTATION_IN_PROGRESS
        assert exc_info.value.http_status == 409
        assert exc_info.value.retryable is True


class TestInstallRequiresCurrentAuth:
    @pytest.mark.asyncio
    async def test_install_requires_current_auth(self, auth_state):
        """A key that was never installed does not authenticate."""
        wrong_key = make_key()
        state = get_auth_state()
        assert not verify_key_against_state(wrong_key, state), (
            "a key that was never installed must not authenticate"
        )


class TestInstallOverlapBounds:
    @pytest.mark.asyncio
    async def test_overlap_above_max_rejected(self, auth_state, monkeypatch):
        monkeypatch.setenv("BAP_KEY_ROTATION_MAX_OVERLAP_SECONDS", "120")
        monkeypatch.setenv("BAP_KEY_ROTATION_MIN_OVERLAP_SECONDS", "0")
        with pytest.raises(EngineError) as exc_info:
            await call_install(make_key(), overlap_seconds=121)
        assert exc_info.value.code is ErrorCode.INVALID_PARAMS
        assert exc_info.value.http_status == 422

    @pytest.mark.asyncio
    async def test_overlap_below_min_rejected(self, auth_state, monkeypatch):
        monkeypatch.setenv("BAP_KEY_ROTATION_MIN_OVERLAP_SECONDS", "10")
        monkeypatch.setenv("BAP_KEY_ROTATION_MAX_OVERLAP_SECONDS", "300")
        with pytest.raises(EngineError) as exc_info:
            await call_install(make_key(), overlap_seconds=5)
        assert exc_info.value.code is ErrorCode.INVALID_PARAMS
        assert exc_info.value.http_status == 422

    @pytest.mark.asyncio
    async def test_overlap_at_exact_max_accepted(self, auth_state, monkeypatch):
        monkeypatch.setenv("BAP_KEY_ROTATION_MAX_OVERLAP_SECONDS", "120")
        monkeypatch.setenv("BAP_KEY_ROTATION_MIN_OVERLAP_SECONDS", "0")
        result = await call_install(make_key(), overlap_seconds=120)
        assert "rotation_id" in result

    @pytest.mark.asyncio
    async def test_overlap_at_exact_min_accepted(self, auth_state, monkeypatch):
        monkeypatch.setenv("BAP_KEY_ROTATION_MIN_OVERLAP_SECONDS", "0")
        monkeypatch.setenv("BAP_KEY_ROTATION_MAX_OVERLAP_SECONDS", "300")
        result = await call_install(make_key(), overlap_seconds=0)
        assert "rotation_id" in result


class TestInstallZeroOverlap:
    @pytest.mark.asyncio
    async def test_install_zero_overlap(self, auth_state):
        """With overlap_seconds=0 the old key is rejected on the next check."""
        old_key = auth_state.current_key
        new_key = make_key()

        await call_install(new_key, overlap_seconds=0)
        await asyncio.sleep(0)

        state = get_auth_state()
        assert not verify_key_against_state(old_key, state)
        assert verify_key_against_state(new_key, state)


class TestInstallEmitsTelemetry:
    @pytest.mark.asyncio
    async def test_install_emits_telemetry(self, auth_state, raven):
        """Both key_rotation_started and key_rotation_completed events are stored."""
        new_key = make_key()
        result = await call_install(new_key, overlap_seconds=0)
        rotation_id = result["rotation_id"]

        await asyncio.sleep(0)

        started = await raven.events(since=0.0, kind="key_rotation_started")
        completed = await raven.events(since=0.0, kind="key_rotation_completed")

        assert len(started) >= 1, "key_rotation_started event must be emitted"
        assert len(completed) >= 1, "key_rotation_completed event must be emitted"

        s_meta = started[-1]["metadata"]
        assert s_meta["rotation_id"] == rotation_id
        assert s_meta["overlap_seconds"] == 0
        assert len(s_meta["initiator_key_hash_prefix"]) == 8
        assert "timestamp" in s_meta

        c_meta = completed[-1]["metadata"]
        assert c_meta["rotation_id"] == rotation_id
        assert "duration_seconds" in c_meta
        assert "timestamp" in c_meta


class TestInstallZerosOldHash:
    @pytest.mark.asyncio
    async def test_install_zeros_old_hash(self, auth_state):
        """After expiry, the old primary_hash bytearray is filled with zeros."""
        state = get_auth_state()
        old_primary_ref = state.primary_hash

        new_key = make_key()
        await call_install(new_key, overlap_seconds=0)
        await asyncio.sleep(0)

        assert old_primary_ref == bytearray(len(old_primary_ref)), (
            "old hash bytearray must be zero-filled after expiry"
        )


class TestInstallBadHash:
    @pytest.mark.asyncio
    async def test_empty_hash_rejected(self, auth_state):
        with pytest.raises(EngineError) as exc_info:
            await install_pending_key(new_hash="", overlap_seconds=60)
        assert exc_info.value.code is ErrorCode.INVALID_PARAMS
        assert exc_info.value.http_status == 422

    @pytest.mark.asyncio
    async def test_short_hash_rejected(self, auth_state):
        with pytest.raises(EngineError) as exc_info:
            await install_pending_key(new_hash="abc123", overlap_seconds=60)
        assert exc_info.value.code is ErrorCode.INVALID_PARAMS
        assert exc_info.value.http_status == 422

    @pytest.mark.asyncio
    async def test_uppercase_hash_rejected(self, auth_state):
        """The contract is 64 *lowercase* hex — uppercase is rejected."""
        upper = hash_key(make_key()).upper()
        with pytest.raises(EngineError) as exc_info:
            await install_pending_key(new_hash=upper, overlap_seconds=60)
        assert exc_info.value.code is ErrorCode.INVALID_PARAMS
        assert exc_info.value.http_status == 422

    @pytest.mark.asyncio
    async def test_non_hex_chars_rejected(self, auth_state):
        with pytest.raises(EngineError) as exc_info:
            await install_pending_key(new_hash="z" * 64, overlap_seconds=60)
        assert exc_info.value.code is ErrorCode.INVALID_PARAMS
        assert exc_info.value.http_status == 422


class TestInstallAuthNotInitialised:
    @pytest.mark.asyncio
    async def test_auth_not_initialised_internal_error(self):
        """With no AuthState installed, install_pending_key -> INTERNAL_ERROR (500).

        The autouse ``fresh_auth_state`` fixture leaves auth state reset; this
        test deliberately does not request the ``auth_state`` fixture.
        """
        assert get_auth_state() is None
        with pytest.raises(EngineError) as exc_info:
            await call_install(make_key(), overlap_seconds=60)
        assert exc_info.value.code is ErrorCode.INTERNAL_ERROR
        assert exc_info.value.http_status == 500


class TestInstallPersistsHash:
    @pytest.mark.asyncio
    async def test_overlap_zero_persists_immediately(self, auth_state):
        """overlap=0 is an immediate swap, so the new hash is the live primary at
        once and must be persisted to /data immediately."""
        new_key = make_key()
        new_hash = hash_key(new_key)

        await install_pending_key(new_hash=new_hash, overlap_seconds=0)

        with open(auth.persisted_key_hash_path(), "r", encoding="utf-8") as f:
            assert f.read().strip() == new_hash

    @pytest.mark.asyncio
    async def test_overlap_persists_at_promotion_not_install(self, auth_state):
        """With overlap>0 the OLD hash is still the live primary during the
        window, so /data must NOT yet hold the new hash right after install. Only
        at promotion (the atomic swap in expire_rotation) does /data flip to the
        new hash — so the persisted value always tracks the live primary."""
        old_key = auth_state.current_key
        old_hash = hash_key(old_key)
        new_key = make_key()
        new_hash = hash_key(new_key)

        result = await install_pending_key(new_hash=new_hash, overlap_seconds=60)

        try:
            with open(auth.persisted_key_hash_path(), "r", encoding="utf-8") as f:
                persisted = f.read().strip()
            assert persisted != new_hash, "must not persist the pending hash at install"
            assert persisted == old_hash
        except FileNotFoundError:
            pass

        state = get_auth_state()
        await expire_rotation(state, result["rotation_id"], 0, time.time())

        with open(auth.persisted_key_hash_path(), "r", encoding="utf-8") as f:
            assert f.read().strip() == new_hash


class TestAdminAuth:
    def test_verify_admin_key_valid(self):
        admin = generate_key()
        init_admin_state(hash_key(admin))
        assert verify_admin_key(admin) is True

    def test_verify_admin_key_wrong(self):
        init_admin_state(hash_key(generate_key()))
        assert verify_admin_key(generate_key()) is False

    def test_verify_admin_key_unconfigured(self):
        """No admin key configured -> verify returns False (never raises)."""
        from src.auth import get_admin_hash

        assert get_admin_hash() is None
        assert verify_admin_key("anything") is False


class TestInstallFullProductPath:
    @pytest.mark.asyncio
    async def test_install_full_product_path(self, auth_state):
        """Full rotation lifecycle: both keys valid during overlap, only new after expiry."""
        old_key = auth_state.current_key
        new_key = make_key()
        state = get_auth_state()

        assert verify_key_against_state(old_key, state)
        assert not verify_key_against_state(new_key, state)

        result = await call_install(new_key, overlap_seconds=60)
        assert result["rotation_id"].startswith("rot_")
        assert verify_key_against_state(old_key, state)
        assert verify_key_against_state(new_key, state)

        await expire_rotation(state, result["rotation_id"], 0, time.time())

        assert not verify_key_against_state(old_key, state)
        assert verify_key_against_state(new_key, state)

    @pytest.mark.asyncio
    async def test_primary_hash_never_none(self, auth_state):
        """primary_hash must remain set throughout the entire rotation lifecycle."""
        state = get_auth_state()
        new_key = make_key()

        assert state.primary_hash is not None

        await call_install(new_key, overlap_seconds=0)
        assert state.primary_hash is not None

        await asyncio.sleep(0)
        assert state.primary_hash is not None

    @pytest.mark.asyncio
    async def test_no_plaintext_key_in_state(self, auth_state):
        """The plaintext new key must not appear in AuthState after rotation."""
        new_key = make_key()
        state = get_auth_state()

        await call_install(new_key, overlap_seconds=60)

        new_key_bytes = new_key.encode()
        for field_val in (state.primary_hash, state.pending_hash):
            if field_val is not None:
                assert bytes(field_val) != new_key_bytes, (
                    "plaintext key must never appear in AuthState fields"
                )


class TestDurableHashPreferredOnBoot:
    @pytest.mark.asyncio
    async def test_durable_hash_preferred_on_boot(self, auth_state, monkeypatch):
        """A hot rotation persists the new hash; a fresh boot that reads /data
        prefers it over the (now stale) env hash — so the rotation survives a
        container restart.

        Mirrors the server's persisted-first resolution
        (``open(persisted_key_hash_path())`` else ENGINE_KEY_HASH else
        ENGINE_API_KEY) without importing the lifespan.
        """
        old_key = auth_state.current_key
        new_key = make_key()

        await call_install(new_key, overlap_seconds=0)
        await asyncio.sleep(0)

        import os

        reset_auth_state()
        monkeypatch.setenv("ENGINE_KEY_HASH", hash_key(old_key))

        key_hash_hex = ""
        try:
            with open(auth.persisted_key_hash_path(), "r", encoding="utf-8") as f:
                key_hash_hex = f.read().strip()
        except FileNotFoundError:
            pass
        if not key_hash_hex:
            key_hash_hex = os.environ.get("ENGINE_KEY_HASH", "")
        init_auth_state(key_hash_hex)

        state = get_auth_state()
        assert verify_key_against_state(new_key, state), (
            "persisted (rotated) key must auth after reboot"
        )
        assert not verify_key_against_state(old_key, state), (
            "stale env key must NOT auth after reboot"
        )


class TestCorruptPersistedHashIgnoredOnBoot:
    @pytest.mark.asyncio
    async def test_corrupt_persisted_hash_falls_back_to_env(self, auth_state, monkeypatch):
        """A corrupt persisted /data value (partial write, garbage) is rejected by
        is_valid_key_hash, so boot ignores it and falls back to the valid
        ENGINE_KEY_HASH — without raising and using the env key.

        Mirrors the server's persisted-first-but-validated resolution
        (prefer persisted only when is_valid_key_hash, else ENGINE_KEY_HASH).
        """
        import os

        env_key = make_key()
        env_hash = hash_key(env_key)

        for corrupt in ("zzz", "a" * 63):
            with open(auth.persisted_key_hash_path(), "w", encoding="utf-8") as f:
                f.write(corrupt)
            assert not is_valid_key_hash(corrupt)

            reset_auth_state()
            monkeypatch.setenv("ENGINE_KEY_HASH", env_hash)

            persisted_hex = ""
            try:
                with open(auth.persisted_key_hash_path(), "r", encoding="utf-8") as f:
                    persisted_hex = f.read().strip()
            except FileNotFoundError:
                pass

            key_hash_hex = ""
            if persisted_hex and is_valid_key_hash(persisted_hex):
                key_hash_hex = persisted_hex
            if not key_hash_hex:
                key_hash_hex = os.environ.get("ENGINE_KEY_HASH", "")

            assert key_hash_hex == env_hash
            assert is_valid_key_hash(key_hash_hex)
            init_auth_state(key_hash_hex)

            state = get_auth_state()
            assert verify_key_against_state(env_key, state), (
                "env key must auth when persisted file is corrupt"
            )


class TestInstallDuringInflightStreaming:
    """Acceptance criterion: in-flight SSE stream is NOT disconnected by rotation.

    Exercises the contract "auth at request entry, no re-auth mid-stream" against
    an inline FastAPI app mirroring the server's pattern (real auth state, real
    POST /admin/install-pending-key handler shape with x-admin-key, real httpx
    ASGI transport).  The production ``/execute`` handler in ``src/server.py``
    follows the same shape — a regression that added mid-stream auth re-checks
    would need separate coverage there.
    """

    TICK_COUNT = 3
    TICK_INTERVAL_S = 0.02

    @pytest.mark.asyncio
    async def test_install_during_inflight_streaming(self, auth_state):
        import httpx
        from fastapi import Body, FastAPI, Header, HTTPException
        from fastapi.responses import JSONResponse, StreamingResponse

        old_key = auth_state.current_key
        admin_key = make_key()
        init_admin_state(hash_key(admin_key))

        app = FastAPI()

        def auth(x_engine_key: str = Header(...)) -> str:
            state = get_auth_state()
            if state is None or not verify_key_against_state(x_engine_key, state):
                raise HTTPException(status_code=401, detail="invalid_key")
            return x_engine_key

        @app.get("/stream")
        async def stream(x_engine_key: str = Header(...)):
            auth(x_engine_key)

            async def gen():
                for i in range(self.TICK_COUNT):
                    yield f"event: tick\ndata: {i}\n\n"
                    await asyncio.sleep(self.TICK_INTERVAL_S)
                yield "event: done\ndata: {}\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        @app.post("/admin/install-pending-key")
        async def install(body: dict = Body(...), x_admin_key: str = Header(...)):
            if not verify_admin_key(x_admin_key):
                raise HTTPException(status_code=401, detail="invalid admin key")
            try:
                return await install_pending_key(
                    new_hash=body["new_hash"],
                    overlap_seconds=body.get("overlap_seconds", 60),
                )
            except EngineError as err:
                return JSONResponse(status_code=err.http_status, content=err.to_dict())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=5.0) as c:
            new_key = make_key()
            streamed: list[str] = []

            async with c.stream("GET", "/stream", headers={"x-engine-key": old_key}) as resp:
                assert resp.status_code == 200
                rotated = False
                async for chunk in resp.aiter_text():
                    streamed.append(chunk)
                    if not rotated:
                        rot = await c.post(
                            "/admin/install-pending-key",
                            headers={"x-admin-key": admin_key},
                            json={"new_hash": hash_key(new_key), "overlap_seconds": 0},
                        )
                        assert rot.status_code == 200, (
                            f"install failed: {rot.status_code} {rot.text}"
                        )
                        rotated = True

            full = "".join(streamed)
            assert "event: done" in full, "in-flight stream lost the terminal event after rotation"
            for i in range(self.TICK_COUNT):
                assert f"data: {i}\n" in full, f"missing tick {i}; stream was cut short"

            r_old, r_new = await asyncio.gather(
                c.get("/stream", headers={"x-engine-key": old_key}),
                c.get("/stream", headers={"x-engine-key": new_key}),
            )
            assert r_old.status_code == 401, (
                "old key should be invalid for new requests post-rotation"
            )
            assert r_new.status_code == 200, "new key must work for new requests post-rotation"


class TestInstallPendingKeyRoute:
    """Drive the real app (src.server.app) through ASGITransport, asserting the
    admin-auth contract: 200 with a valid x-admin-key, 401 with a wrong one,
    503 when no admin key is configured. Real auth state, real handler, real
    EngineError rendering — no mocks on the wire path."""

    @pytest_asyncio.fixture
    async def app_client(self, monkeypatch):
        import httpx
        import src.server as server

        engine_key = make_key()
        admin_key = make_key()
        init_auth_state(hash_key(engine_key))
        init_admin_state(hash_key(admin_key))

        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=5.0) as c:
            yield c, admin_key

    @pytest.mark.asyncio
    async def test_route_valid_admin_key_200(self, app_client):
        c, admin_key = app_client
        new_key = make_key()
        resp = await c.post(
            "/admin/install-pending-key",
            headers={"x-admin-key": admin_key},
            json={"new_hash": hash_key(new_key), "overlap_seconds": 60},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rotation_id"].startswith("rot_")
        assert isinstance(body["overlap_expires_at"], (int, float))

    @pytest.mark.asyncio
    async def test_route_wrong_admin_key_401(self, app_client):
        c, admin_key = app_client
        new_key = make_key()
        resp = await c.post(
            "/admin/install-pending-key",
            headers={"x-admin-key": make_key()},
            json={"new_hash": hash_key(new_key), "overlap_seconds": 60},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == ErrorCode.INVALID_KEY.value

    @pytest.mark.asyncio
    async def test_route_no_admin_configured_503(self, monkeypatch):
        """With no admin key configured, the route reports key_not_configured (503)."""
        import httpx
        import src.server as server

        init_auth_state(hash_key(make_key()))

        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=5.0) as c:
            resp = await c.post(
                "/admin/install-pending-key",
                headers={"x-admin-key": make_key()},
                json={"new_hash": hash_key(make_key()), "overlap_seconds": 60},
            )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == ErrorCode.KEY_NOT_CONFIGURED.value
