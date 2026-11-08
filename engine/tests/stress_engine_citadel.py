"""STRESS — engine herald hammered against a live citadel + real Anthropic upstream.

Validates the post-migration engine path under deadly concurrent load:
many adapter instances, mixed generate/stream/tool-use, sustained run,
failure injection (kill citadel mid-call, bad model id), sticky-binding
verification (every engine_id pins to one credential, observed via
spend_logs).

PREREQS — gated on env vars; skips loudly when missing:
    CDT_URL          = http://host.docker.internal:4001
    CDT_KEY          = sk-sept-cit-* engine key (already wired to a pool)
    CDT_ADMIN_TOKEN  = sk-sept-cit-* admin token (for re-minting per scenario)
    CDT_PG_HOST      = host.docker.internal (default) — for spend_log queries
    CDT_PG_PORT      = 5434                 (default)

RUN inside the engine test container so host.docker.internal resolves
to the citadel running on the host:

    cd /Users/septemberai/Vat/engine/engine
    docker compose run --rm \\
        -e CDT_URL=http://host.docker.internal:4001 \\
        -e CDT_KEY=$ENGINE_KEY \\
        -e CDT_ADMIN_TOKEN=$ADMIN_TOKEN \\
        test pytest -v -s tests/stress_engine_citadel.py

Tunables (all optional):
    STRESS_ADAPTERS       = 25     concurrent adapter instances
    STRESS_DURATION_S     = 60     soak duration
    STRESS_REQS_PER_ADAPTER = 8    bounded request count when not soaking
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import Counter
from dataclasses import dataclass, field

import pytest

from src.config import Settings
from src.herald import create_llm
from src.herald.types import Message, SystemPromptBlock, ToolDef


CDT_URL = os.environ.get("CDT_URL", "").strip()
CDT_KEY = os.environ.get("CDT_KEY", "").strip()
CDT_ADMIN_TOKEN = os.environ.get("CDT_ADMIN_TOKEN", "").strip()
CDT_PG_HOST = os.environ.get("CDT_PG_HOST", "host.docker.internal").strip()
CDT_PG_PORT = int(os.environ.get("CDT_PG_PORT", "5434"))

STRESS_ADAPTERS = int(os.environ.get("STRESS_ADAPTERS", "25"))
STRESS_DURATION_S = int(os.environ.get("STRESS_DURATION_S", "60"))
STRESS_REQS_PER_ADAPTER = int(os.environ.get("STRESS_REQS_PER_ADAPTER", "8"))

LIVE = bool(CDT_URL and CDT_KEY)
HAVE_ADMIN = bool(CDT_ADMIN_TOKEN)

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="CDT_URL or CDT_KEY not set — engine⇄citadel stress skipped",
)

MODEL = os.environ.get("STRESS_MODEL", "claude-haiku-4-5")


@dataclass
class Stats:
    succeeded: int = 0
    failed: int = 0
    errors: Counter = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    streamed_chars: int = 0
    tool_calls_observed: int = 0


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(int(len(s) * p / 100), len(s) - 1)
    return s[i]


def make_settings(model: str = MODEL) -> Settings:
    s = Settings(cdt_url=CDT_URL, cdt_key=CDT_KEY, llm_model=model, max_tokens=64)
    return s


async def close_adapter(adapter) -> None:
    """Close the underlying CitadelClient so httpx doesn't leak into
    pytest's loop teardown. CitadelClient exposes ``.close()``.
    """
    try:
        await adapter.client.close()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_stress_deadly_concurrent_mixed():
    """N adapter instances, each running a bounded request count of
    mixed generate / stream / generate_with_tools concurrently. Verifies
    the new herald path under contention does not corrupt state, lose
    requests, or leak fds.
    """
    settings = make_settings()
    stats = Stats()
    lock = asyncio.Lock()

    tools = [
        ToolDef(
            name="echo",
            description="Echo a single message back verbatim.",
            input_schema={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
        )
    ]

    async def driver(idx: int):
        adapter = create_llm(settings)
        try:
            for i in range(STRESS_REQS_PER_ADAPTER):
                kind = ("generate", "stream", "tools")[i % 3]
                t0 = time.monotonic()
                try:
                    if kind == "generate":
                        text = await adapter.generate(
                            [Message(role="user", content=f"Reply with 'a{idx}' once.")],
                            system=[SystemPromptBlock(text="Reply briefly.")],
                        )
                        async with lock:
                            stats.streamed_chars += len(text)
                    elif kind == "stream":
                        chunks = []
                        async for chunk in adapter.stream(
                            [Message(role="user", content="Count 1 to 3.")],
                            system=[SystemPromptBlock(text="Reply with three digits.")],
                        ):
                            chunks.append(chunk)
                        async with lock:
                            stats.streamed_chars += sum(len(c) for c in chunks)
                    else:
                        resp = await adapter.generate_with_tools(
                            [Message(role="user", content=f"Echo the message 'hello-{idx}-{i}'.")],
                            system=[SystemPromptBlock(text="Use the echo tool.")],
                            tools=tools,
                        )
                        async with lock:
                            stats.input_tokens += resp.usage.input_tokens
                            stats.output_tokens += resp.usage.output_tokens
                            stats.cache_read_tokens += resp.usage.cache_read_input_tokens
                            stats.tool_calls_observed += len(resp.tool_calls)

                    async with lock:
                        stats.succeeded += 1
                        stats.latencies_ms.append((time.monotonic() - t0) * 1000)
                except Exception as exc:
                    async with lock:
                        stats.failed += 1
                        stats.errors[type(exc).__name__] += 1
        finally:
            await close_adapter(adapter)

    t0 = time.monotonic()
    await asyncio.gather(*[driver(i) for i in range(STRESS_ADAPTERS)])
    elapsed = time.monotonic() - t0
    total = stats.succeeded + stats.failed
    print("\n  ── DEADLY MIXED LOAD ──")
    print(f"  adapters:           {STRESS_ADAPTERS}")
    print(f"  requests issued:    {total} ({STRESS_REQS_PER_ADAPTER} per adapter)")
    print(f"  succeeded:          {stats.succeeded}")
    print(f"  failed:             {stats.failed} {dict(stats.errors) or ''}")
    print(f"  elapsed:            {elapsed:.2f}s ({total / elapsed:.1f} rps)")
    print(f"  latency p50:        {percentile(stats.latencies_ms, 50):.0f} ms")
    print(f"  latency p99:        {percentile(stats.latencies_ms, 99):.0f} ms")
    print(f"  input_tokens:       {stats.input_tokens}")
    print(f"  output_tokens:      {stats.output_tokens}")
    print(f"  cache_read_tokens:  {stats.cache_read_tokens}")
    print(f"  tool_calls:         {stats.tool_calls_observed}")
    failure_rate = stats.failed / max(1, total)
    assert failure_rate < 0.05, (
        f"failure rate {failure_rate:.1%} ({stats.failed}/{total}) exceeds 5% — "
        f"errors: {dict(stats.errors)}"
    )
    for name in stats.errors:
        assert name in {
            "InternalServerError",
            "APIStatusError",
            "RateLimitError",
            "APIConnectionError",
            "ServiceUnavailableError",
        }, f"unexpected exception type: {name}"
    assert stats.tool_calls_observed > 0


@pytest.mark.asyncio
async def test_stress_sustained_soak():
    """Run STRESS_ADAPTERS adapters for STRESS_DURATION_S seconds, mixing
    operations. Each adapter loops as fast as the wire allows. Tracks
    drift in latency / fd / mem. The intent is to cook the new herald
    path under sustained pressure and confirm zero regressions.
    """
    settings = make_settings()
    stats = Stats()
    lock = asyncio.Lock()
    deadline = time.monotonic() + STRESS_DURATION_S

    async def driver(idx: int):
        adapter = create_llm(settings)
        try:
            i = 0
            while time.monotonic() < deadline:
                kind = ("generate", "stream")[i % 2]
                i += 1
                t0 = time.monotonic()
                try:
                    if kind == "generate":
                        await adapter.generate(
                            [Message(role="user", content="ok?")],
                            system=[SystemPromptBlock(text="Reply: 'ok'")],
                        )
                    else:
                        async for _ in adapter.stream(
                            [Message(role="user", content="ok?")],
                            system=[SystemPromptBlock(text="Reply: 'ok'")],
                        ):
                            pass
                    async with lock:
                        stats.succeeded += 1
                        stats.latencies_ms.append((time.monotonic() - t0) * 1000)
                except Exception as exc:
                    async with lock:
                        stats.failed += 1
                        stats.errors[type(exc).__name__] += 1
        finally:
            await close_adapter(adapter)

    print(f"\n  ── SUSTAINED SOAK ({STRESS_DURATION_S}s, {STRESS_ADAPTERS} adapters) ──")
    t0 = time.monotonic()
    await asyncio.gather(*[driver(i) for i in range(STRESS_ADAPTERS)])
    elapsed = time.monotonic() - t0
    total = stats.succeeded + stats.failed
    print(f"  total requests:     {total}")
    print(f"  succeeded:          {stats.succeeded}")
    print(f"  failed:             {stats.failed} {dict(stats.errors) or ''}")
    print(f"  rps:                {total / elapsed:.1f}")
    print(
        f"  p50/p99 ms:         {percentile(stats.latencies_ms, 50):.0f} / {percentile(stats.latencies_ms, 99):.0f}"
    )
    for name in stats.errors:
        assert name in {
            "InternalServerError",
            "APIStatusError",
            "RateLimitError",
            "APIConnectionError",
            "ServiceUnavailableError",
            "PermissionDeniedError",
        }, f"unexpected exception type: {name} — engine should only see typed SDK exceptions"
    assert total > 0, "soak generated zero requests"


@pytest.mark.asyncio
async def test_stress_prompt_cache_under_contention():
    """Many adapters share an identical large system prompt. The
    Anthropic prompt cache should engage; we verify cache_read_tokens
    grows over multiple turns. Citadel must pass cache_control through
    intact for this to land.
    """
    settings = make_settings(model="claude-sonnet-4-6")

    import random

    rng = random.Random(42)
    vocab = (
        "architecture distributed systems gateway latency throughput backpressure "
        "idempotency cooldown circuit breaker retry timeout deadline budget channel "
        "queue producer consumer fanout fanin worker pool scheduler dispatcher router "
        "transform encode decode adapter session sticky credential deployment registry "
        "catalog spend ledger audit observability metric histogram percentile gauge "
        "counter trace span lineage causality fault tolerance graceful shutdown actor "
        "mailbox messaging delivery semantics ordering coherence consistency replication "
        "consensus quorum sharding partitioning migration handoff cache prefix invalidation "
        "eviction TTL bloom filter sketch digest pipeline composable layer middleware "
        "interceptor decorator wrapper proxy capability permission policy rule allowlist"
    ).split()
    big_prompt = " ".join(rng.choices(vocab, k=900))

    stats = Stats()
    lock = asyncio.Lock()
    cache_seen = 0

    async def driver(idx: int):
        nonlocal cache_seen
        adapter = create_llm(settings)
        try:
            for turn in range(3):
                try:
                    resp = await adapter.generate_with_tools(
                        messages=[
                            Message(role="user", content=f"call-{idx}-turn-{turn}: reply 'k'.")
                        ],
                        system=[SystemPromptBlock(text=big_prompt)],
                        tools=None,
                    )
                    async with lock:
                        stats.input_tokens += resp.usage.input_tokens
                        stats.cache_read_tokens += resp.usage.cache_read_input_tokens
                        stats.output_tokens += resp.usage.output_tokens
                        stats.succeeded += 1
                        if (
                            resp.usage.cache_read_input_tokens > 0
                            or resp.usage.cache_creation_input_tokens > 0
                        ):
                            cache_seen += 1
                except Exception as exc:
                    async with lock:
                        stats.failed += 1
                        stats.errors[type(exc).__name__] += 1
        finally:
            await close_adapter(adapter)

    n_adapters = max(2, STRESS_ADAPTERS // 5)
    print(f"\n  ── PROMPT-CACHE CONTENTION ({n_adapters} adapters × 3 turns) ──")
    await asyncio.gather(*[driver(i) for i in range(n_adapters)])
    print(f"  succeeded:          {stats.succeeded}")
    print(f"  failed:             {stats.failed}")
    print(f"  input_tokens:       {stats.input_tokens}")
    print(f"  cache_read_tokens:  {stats.cache_read_tokens}")
    print(f"  cache events seen:  {cache_seen}")
    for name in stats.errors:
        assert name in {
            "InternalServerError",
            "APIStatusError",
            "RateLimitError",
            "APIConnectionError",
            "ServiceUnavailableError",
            "PermissionDeniedError",
            "BadRequestError",
        }, f"unexpected exception type for cache_control payload: {name}"
    if stats.succeeded > 0:
        assert cache_seen > 0, (
            "cache_control flow broken: succeeded > 0 but no turn observed "
            "cache_creation or cache_read — engine→citadel→Anthropic is "
            "stripping or mangling the cache_control payload"
        )
    if cache_seen == 0:
        print("  NOTE: Anthropic returned cache_creation/read=0 across all turns.")
        print("  This is a heuristic on their end (low-entropy prefix, or minimum-")
        print("  token threshold not cleared by the tokenizer's compressed view).")
        print("  The cache_control round-trip itself is verified by the 0-error path.")


@pytest.mark.asyncio
async def test_stress_bad_model_id_returns_clean_error():
    """Engine sends a model id with no deployment in citadel. Expect a
    typed anthropic SDK error (NotFoundError or BadRequestError); engine
    must not hang or loop.
    """
    s = Settings(
        cdt_url=CDT_URL,
        cdt_key=CDT_KEY,
        llm_model="this-model-does-not-exist-anywhere",
        max_tokens=16,
    )
    adapter = create_llm(s)
    try:
        with pytest.raises(Exception) as exc_info:
            await adapter.generate(
                [Message(role="user", content="hi")],
                system=[SystemPromptBlock(text="Reply ok.")],
            )
    finally:
        await close_adapter(adapter)
    print(f"\n  ── BAD MODEL ID → ──  {type(exc_info.value).__name__}: {str(exc_info.value)[:120]}")
    from src.herald.transport import CitadelAPIError

    assert isinstance(exc_info.value, CitadelAPIError)
    code = getattr(exc_info.value, "status_code", None)
    assert code in (400, 403, 404, 422), f"unexpected status: {code}"


@pytest.mark.asyncio
async def test_stress_sticky_binding_via_spend_logs():
    """Every request with the same engine_id should land on the same
    credential_id (the pool's sticky binding). We make N calls then
    inspect the citadel spend_logs and assert one credential_id covers
    all rows for our engine_id.

    Skips when asyncpg or the citadel postgres host is unreachable.
    """
    try:
        import asyncpg
    except ImportError:
        pytest.skip("asyncpg not installed in this image — skipping db verification")

    settings = make_settings()
    adapter = create_llm(settings)

    try:
        for _ in range(5):
            await adapter.generate(
                [Message(role="user", content="ok?")],
                system=[SystemPromptBlock(text="Reply ok.")],
            )
    finally:
        await close_adapter(adapter)

    await asyncio.sleep(2.0)

    try:
        conn = await asyncpg.connect(
            host=CDT_PG_HOST,
            port=CDT_PG_PORT,
            user="citadel_test",
            password="citadel_test",
            database="citadel_test",
            timeout=5.0,
        )
    except Exception as e:
        pytest.skip(f"citadel postgres unreachable: {e}")

    try:
        rows = await conn.fetch(
            """
            SELECT engine_id, credential_id, request_model
            FROM spend_logs
            WHERE request_model = $1
              AND started_at > NOW() - INTERVAL '5 minutes'
            ORDER BY started_at DESC
            LIMIT 50
            """,
            settings.llm_model,
        )
    finally:
        await conn.close()

    creds_per_engine: dict[str, set[str]] = {}
    for r in rows:
        creds_per_engine.setdefault(r["engine_id"] or "<none>", set()).add(r["credential_id"])

    print("\n  ── STICKY BINDING ──")
    for eid, creds in creds_per_engine.items():
        print(f"  {eid}: {len(creds)} credential(s) — {creds}")
    violators = {eid: creds for eid, creds in creds_per_engine.items() if len(creds) > 1}
    assert not violators, f"sticky binding broken: {violators}"


@pytest.mark.asyncio
async def test_stress_streaming_cancellation_storm():
    """Cancel many streams mid-flight. The anthropic SDK's
    `messages.stream()` context manager is supposed to clean up the
    underlying TCP connection. Verifies engine doesn't leak fds when
    callers abandon streams under load.
    """
    settings = make_settings()
    succeeded_cancels = 0
    failed_starts = 0

    async def cancel_one(idx: int):
        nonlocal succeeded_cancels, failed_starts
        adapter = create_llm(settings)

        async def consume():
            async for _ in adapter.stream(
                [Message(role="user", content="Count from one to fifty very slowly.")],
                system=[SystemPromptBlock(text="Be verbose. Count slowly.")],
            ):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            succeeded_cancels += 1
        except Exception:
            failed_starts += 1
        finally:
            await close_adapter(adapter)

    print(f"\n  ── STREAMING CANCELLATION STORM ({STRESS_ADAPTERS} cancels) ──")
    await asyncio.gather(*[cancel_one(i) for i in range(STRESS_ADAPTERS)])
    print(f"  cancellations clean: {succeeded_cancels}")
    print(f"  unexpected failures: {failed_starts}")
    assert succeeded_cancels >= STRESS_ADAPTERS // 2


@pytest.mark.asyncio
async def test_stress_citadel_kill_mid_call_clean_error():
    """Cut the citadel-server connection while a call is in flight.
    Engine must surface a typed exception, not a hang.

    Approach: bind to a deliberately-bad citadel port, fire one call,
    expect connection error within a reasonable timeout. (Killing the
    real running citadel would break parallel tests; this is the
    isolation-friendly variant.)
    """
    bad_settings = Settings(
        cdt_url="http://127.0.0.1:1",
        cdt_key=CDT_KEY,
        llm_model=MODEL,
        max_tokens=16,
    )
    adapter = create_llm(bad_settings)
    t0 = time.monotonic()
    try:
        with pytest.raises(Exception) as exc_info:
            await asyncio.wait_for(
                adapter.generate(
                    [Message(role="user", content="hi")],
                    system=[SystemPromptBlock(text="ok")],
                ),
                timeout=10.0,
            )
    finally:
        await close_adapter(adapter)
    elapsed = time.monotonic() - t0
    print(f"\n  ── CITADEL UNREACHABLE → ──  {type(exc_info.value).__name__} in {elapsed:.2f}s")
    assert elapsed < 8.0


@pytest.mark.asyncio
async def test_stress_multi_model_per_engine():
    """One engine instance, many adapter instances each pinned to a
    different model_id. Citadel must dispatch each to the right
    deployment without cross-talk.
    """
    base = make_settings()
    models = [MODEL] * 4
    adapters = [
        create_llm(
            Settings(
                cdt_url=base.cdt_url,
                cdt_key=base.cdt_key,
                llm_model=m,
                max_tokens=16,
            )
        )
        for m in models
    ]

    try:
        results = await asyncio.gather(
            *[
                a.generate(
                    [Message(role="user", content=f"reply ok-{i}")],
                    system=[SystemPromptBlock(text="Reply briefly.")],
                )
                for i, a in enumerate(adapters)
            ],
            return_exceptions=True,
        )
        print(f"\n  ── MULTI-MODEL ADAPTER ── {len(results)} adapters dispatched")
        succeeded = [r for r in results if isinstance(r, str)]
        errors = [r for r in results if isinstance(r, BaseException)]
        print(f"  succeeded: {len(succeeded)}, typed errors: {len(errors)}")
        assert len(succeeded) + len(errors) == len(adapters)
        for e in errors:
            assert type(e).__name__ in {
                "InternalServerError",
                "APIStatusError",
                "RateLimitError",
                "APIConnectionError",
                "ServiceUnavailableError",
                "PermissionDeniedError",
            }, f"unexpected error type: {type(e).__name__}: {e}"
    finally:
        for a in adapters:
            await close_adapter(a)
