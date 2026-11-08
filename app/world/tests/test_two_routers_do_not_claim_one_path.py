"""No two routers on the running host may claim the same path.

`CORE-19`: `build_agents_router` and `build_location_router` both defined
`GET /world/state`, and both are mounted on the embedded app. FastAPI
serves whichever registered first - agents_api - so `location_api`'s
richer view (clock, government, ledger) was dead on the only host any run
uses, silently, with no error at boot and no way to tell from the outside
which one answered.

`run_report.py` depends on `run_id` coming from the agents version, so the
collision resolving the other way round - a reordered include, a new
router mounted earlier - would break the report's run identity without
anything failing loudly. That is the real cost: not that one route is
shadowed, but that which one wins is an accident of include order.

Asserted over the WHOLE app rather than over the two routers that were
found, because the next collision will be somewhere else.
"""
from __future__ import annotations

from collections import Counter

import pytest

from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)


def _walk(routes, seen: Counter) -> None:
    """Every effective route, including the ones inside included routers.

    This recursion is not incidental - it is half of why CORE-19 survived.
    This FastAPI keeps an included router as one opaque `_IncludedRouter`
    entry in `app.routes` rather than flattening its paths in, so a flat
    scan of `app.routes` shows nine objects and no collision at all. The
    duplicate `/world/state` is two levels down, which is exactly why
    nothing at boot, in a test, or in the route list ever said a word
    about it.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            _walk(inner.routes, seen)
            continue
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in (getattr(route, "methods", None) or set()):
            seen[(method, path)] += 1


def _duplicate_routes(app) -> dict:
    seen: Counter = Counter()
    _walk(app.routes, seen)
    return {key: n for key, n in seen.items() if n > 1}


@pytest.mark.asyncio
async def test_the_embedded_app_has_no_duplicate_routes(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))
    await runner.boot()
    # Built the way the runner builds it, genesis included - the viewer
    # routers only mount when `genesis is not None`, and they are half the
    # routes a collision could hide in.
    app = build_embedded_world_app(
        runner.world_state,
        runner.registry,
        runner.ledger,
        runner.metrics,
        tick_engine=runner.tick_engine,
        genesis=getattr(runner, "genesis", None),
        map_service=runner.map_service,
    )

    duplicates = _duplicate_routes(app)
    assert not duplicates, (
        "CORE-19: two routers claim the same path on the host that runs; "
        f"whichever is included first silently wins: {sorted(duplicates)}"
    )
