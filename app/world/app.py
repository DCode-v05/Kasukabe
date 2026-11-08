"""World server FastAPI entrypoint - scaffold placeholder, superseded.

The real world server (map, ticks, tool registry, ledger, charter, health,
crime, genesis generator - DESIGN.md section 9) is world/main.py + world/
wiring.py's build_world_server() ("wire-world" track) - `make run-world`
and docker-compose.yml's `world` service both launch that module now,
not this one. This file is kept only as a standalone health-check
scaffold with its own test (world/tests/test_world_app.py); it is not
booted by anything else in this repo.

Module boundary (see app/CONVENTIONS.md): this package owns ALL world
state and must never import boto3 - every model call goes through
broker/'s HTTP API instead.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="kasukabe-world", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe.

    Real readiness (map loaded, genesis complete, ledger balanced against
    the $1000 invariant) is the world module owner's responsibility, not
    this scaffold's.
    """
    return {"status": "ok", "service": "world"}
