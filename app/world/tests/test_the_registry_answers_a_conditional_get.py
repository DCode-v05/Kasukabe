"""ENG-19: /tools/registry must be able to say "nothing has changed".

Every engine refetches this whole payload once a TICK. It is forced to:
`engine/src/sim/pinned_boot.py` passes `force=LOCAL_TOOL_SCHEMAS` into
`refresh_world_tool_registry`, because when schemas are served by location
the registry's version counter cannot see a resident WALK - the world's
registry did not change, the resident did. So the version check that exists
(`GET /tools/registry/version`) cannot answer this question and the full
~53 KB body went out every tick, per engine. At the roadmap's 250 agents
that is around 13 MB a tick to tell everybody nothing happened.

The fix is the ordinary HTTP one: stamp an `ETag` on the payload and honour
`If-None-Match`. The finding asked for a validator "keyed on (version,
location, seat)"; `world/main.py::registry_etag` hashes the serialised rows
instead, which is those three inputs and any future one, and cannot go
stale by somebody forgetting to add a term to the key.

Driven through a real ASGI request against the real `world.main:app` - the
module `Makefile`'s `run-world` and `docker-compose.yml`'s `world` service
both launch - booted over a real `WorldServer`, because a test that called
`registry_etag()` directly would stay green if the route never used it.

SCOPE, stated because a reader will ask. `world/run_genesis.py`'s embedded
app serves this same route and is the host every real run uses today; it
was out of this change's reach and still answers unconditionally. The
engine side degrades to a body digest against such a host, so the ToolDef
rebuild is skipped there either way and only the transfer is still paid -
see `engine/tests/test_the_registry_is_not_refetched_every_tick.py`
::test_a_world_with_no_etag_still_stops_the_rebuild.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from world.wiring import PACKAGE_DIR, WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map

SMALL_GENESIS = PACKAGE_DIR / "genesis_10.json"


async def booted_client() -> TestClient:
    """A TestClient over the real `world.main:app`, with a real WorldServer.

    Not `with TestClient(...)`: the context manager runs main.py's lifespan,
    which boots a SECOND, full-size WorldServer of its own from the 250-agent
    genesis. `server_dep()` reads `app.state.world_server`, so setting it is
    the whole of what the lifespan does that these routes need.
    """
    import world.main as world_main

    server = await build_world_server(
        WorldConfig(genesis_json_path=SMALL_GENESIS, genesis_tick=0)
    )
    world_main.app.state.world_server = server
    return TestClient(world_main.app)


@pytest.mark.asyncio
async def test_the_payload_carries_an_etag():
    client = await booted_client()
    first = client.get("/tools/registry")
    assert first.status_code == 200
    assert first.headers.get("ETag"), (
        "no ETag on the registry payload, so an engine has no way to ask "
        "whether it has changed and must pull all 53 KB every tick (ENG-19)"
    )
    rows = first.json()
    assert rows and all("name" in row for row in rows), (
        "the shape every engine parses must be unchanged by the ETag work"
    )


@pytest.mark.asyncio
async def test_if_none_match_gets_a_304_and_no_body():
    client = await booted_client()
    first = client.get("/tools/registry")
    etag = first.headers["ETag"]

    second = client.get("/tools/registry", headers={"If-None-Match": etag})

    assert second.status_code == 304, (
        "the world re-sent the whole registry to an engine that already had "
        f"it (status {second.status_code}) - this is the per-tick refetch"
    )
    assert not second.content, "a 304 that still carries the body saves nothing"
    assert second.headers.get("ETag") == etag


@pytest.mark.asyncio
async def test_a_stale_validator_gets_the_whole_payload():
    """The failure that would matter: a resident stuck on old schemas."""
    client = await booted_client()
    answer = client.get("/tools/registry", headers={"If-None-Match": '"not-the-current-one"'})
    assert answer.status_code == 200
    assert answer.json(), "an engine with a stale ETag was served nothing at all"


@pytest.mark.asyncio
async def test_a_caller_that_asks_nothing_gets_what_it_always_got():
    """The observatory, world/preflight.py and every existing test send no
    If-None-Match. None of them may notice this change happened."""
    client = await booted_client()
    plain = client.get("/tools/registry")
    assert plain.status_code == 200
    assert plain.headers["content-type"].startswith("application/json")
    rows = plain.json()
    assert isinstance(rows, list)
    for row in rows:
        assert set(row) >= {"name", "description", "input_schema"}


@pytest.mark.asyncio
async def test_the_validator_is_stable_across_identical_answers():
    """An ETag that changed when nothing did would turn every conditional
    GET back into a full one, silently, and look like it worked."""
    client = await booted_client()
    assert client.get("/tools/registry").headers["ETag"] == (
        client.get("/tools/registry").headers["ETag"]
    )
