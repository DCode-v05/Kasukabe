"""The observatory: FastAPI on :8500, read-only.

Owns no database and no world state. It opens the broker's trace SQLite file
**read-only** and asks the world server for its refusal log over HTTP; it has
no route that writes anything anywhere. That is not tidiness - an instrument
that can nudge the experiment stops being able to tell you what the town did
on its own, and Part D is explicit that the ONE place the instrument is
allowed to write into the experiment is the perception feed, rate-limited and
logged, and it does not live here.

Degrades rather than fails. A missing trace file, an unreachable world, a
database written before the `failed_at` column existed - each produces a page
that says so, because a monitoring service that 500s when the thing it
monitors is unhealthy is exactly backwards.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from observatory import gaps, health, hops

DEFAULT_TRACE_DB = os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db")
DEFAULT_WORLD_URL = os.environ.get("WORLD_BASE_URL", "http://127.0.0.1:8100")
STATIC = Path(__file__).parent / "static"


def open_trace_db(path: str) -> sqlite3.Connection | None:
    """Read-only, and None rather than an exception when it is not there.

    `mode=ro` in the URI, not a convention: it makes the read-only promise in
    this module's docstring enforced by SQLite rather than by our good
    behaviour, so a future careless INSERT here fails loudly instead of
    quietly editing the run it is meant to be observing.
    """
    if not Path(path).exists():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    except sqlite3.Error:
        return None


def has_failed_at_column(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(llm_calls)")}
    return "failed_at" in cols


def create_app(
    trace_db_path: str = DEFAULT_TRACE_DB, world_base_url: str = DEFAULT_WORLD_URL
) -> FastAPI:
    app = FastAPI(title="kasukabe-observatory")

    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        return {"status": "ok", "service": "observatory", "trace_db": trace_db_path}

    @app.get("/api/health")
    async def run_health() -> JSONResponse:
        conn = open_trace_db(trace_db_path)
        if conn is None:
            return JSONResponse(
                {
                    "available": False,
                    "why": f"no trace database at {trace_db_path} - has a run written one yet?",
                }
            )
        with conn:
            engines = await probe_engines(world_base_url)
            snapshot = health.read_health(
                conn,
                engines_alive=engines.get("alive", 0),
                engines_expected=engines.get("expected", 0),
                dormant_agents=engines.get("dormant", 0),
            )
        return JSONResponse(
            {
                "available": True,
                "calls": snapshot.calls,
                "successes": snapshot.successes,
                "failures": snapshot.failures,
                "dispatch_success": round(snapshot.dispatch_success, 4),
                "believable": snapshot.believable,
                "verdict": snapshot.verdict,
                "spend_usd": round(snapshot.spend_usd, 4),
                "agents_seen": snapshot.agents_seen,
                "dormant_agents": snapshot.dormant_agents,
                "engines_alive": snapshot.engines_alive,
                "engines_expected": snapshot.engines_expected,
            }
        )

    @app.get("/api/hops")
    async def failure_hops() -> JSONResponse:
        conn = open_trace_db(trace_db_path)
        if conn is None:
            return JSONResponse({"available": False, "hops": []})
        with conn:
            if not has_failed_at_column(conn):
                return JSONResponse(
                    {
                        "available": False,
                        "why": "this trace database predates the failed_at column "
                        "(migration 20260825120000) - failures cannot be attributed",
                        "hops": [],
                    }
                )
            rows = hops.failures_by_hop(conn)
            untagged = hops.untagged_share(conn)
        return JSONResponse(
            {
                "available": True,
                "untagged_share": round(untagged, 4),
                "hops": [
                    {
                        "hop": h.hop,
                        "failures": h.failures,
                        "agents": h.agents,
                        "example_error": h.example_error,
                        "example_message": h.example_message[:300],
                    }
                    for h in rows
                ],
            }
        )

    @app.get("/api/gaps")
    async def capability_gaps() -> JSONResponse:
        try:
            async with httpx.AsyncClient(base_url=world_base_url, timeout=5.0) as client:
                resp = await client.get("/world/refusals")
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return JSONResponse(
                {
                    "available": False,
                    "why": f"world at {world_base_url} is not answering - is a run going?",
                    "gaps": [],
                }
            )
        found = gaps.summarise(payload.get("refusals", []))
        return JSONResponse(
            {
                "available": True,
                "refusals_seen": payload.get("count", 0),
                "gaps": [
                    {
                        "unmet_requirement": g.unmet_requirement,
                        "agents": g.agents,
                        "attempts": g.attempts,
                        "tools": g.tools,
                        "example": g.example,
                        "is_missing_verb": g.is_missing_verb,
                    }
                    for g in found
                ],
            }
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app


async def probe_engines(world_base_url: str) -> dict[str, int]:
    """Liveness, best-effort. Never raises: the observatory reporting that it
    cannot see the engines is useful; the observatory falling over because it
    cannot see them is not."""
    try:
        async with httpx.AsyncClient(base_url=world_base_url, timeout=3.0) as client:
            resp = await client.get("/world/engines")
            if resp.status_code != 200:
                return {}
            body = resp.json()
            return {
                "alive": int(body.get("alive", 0)),
                "expected": int(body.get("expected", 0)),
                "dormant": int(body.get("dormant", 0)),
            }
    except (httpx.HTTPError, ValueError, TypeError):
        return {}


app = create_app()
