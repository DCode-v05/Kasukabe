"""A tool's JSON schema is built once, not per agent per tick.

`CORE-22`. `tool_registry_rows` called
`spec.params_model.model_json_schema()` for every tool on every request.
With `KASUKABE_LOCAL_TOOL_SCHEMAS` on - the default - each engine re-GETs
the whole registry every tick with `force=True`, so at 250 residents on a
0.3 cadence that is roughly 83 requests a tick, each rebuilding about 82
pydantic schemas: some 6,800 schema builds per tick, on the world's own
event loop, producing bytes that are byte-identical every single time.

A params model is a class defined at import. Its JSON schema cannot change
while the process runs, so the only question was whether anybody had said
so. Nobody had.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from world.tools import registry as registry_module
from world.tools.registry import json_schema_for


class _Params(BaseModel):
    quantity: int
    item: str = "bread"


class _OtherParams(BaseModel):
    destination_location_id: str


@pytest.fixture(autouse=True)
def _clear_cache():
    registry_module._SCHEMA_CACHE.clear()
    yield
    registry_module._SCHEMA_CACHE.clear()


def test_the_schema_is_correct() -> None:
    """First, that caching did not change the answer."""
    assert json_schema_for(_Params) == _Params.model_json_schema()


def test_the_model_is_only_asked_once(monkeypatch) -> None:
    calls = {"n": 0}
    real = _Params.model_json_schema

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(_Params, "model_json_schema", counting)

    for _ in range(50):
        json_schema_for(_Params)

    assert calls["n"] == 1, (
        f"CORE-22: the schema was rebuilt {calls['n']} times; at 250 "
        "residents this is thousands of identical builds per tick on the "
        "world's event loop"
    )


def test_two_models_do_not_share_an_entry() -> None:
    """Keyed on the CLASS, not the tool name: `toolify` builds a fresh
    params model per invented tool, and two of them sharing a schema would
    hand a resident somebody else's parameters."""
    assert json_schema_for(_Params) != json_schema_for(_OtherParams)
    assert "destination_location_id" in str(json_schema_for(_OtherParams))


def test_a_caller_cannot_poison_the_cache() -> None:
    """These dicts are handed to FastAPI and to the engine. One mutation
    of a shared dict would corrupt every later reader, which is a worse
    bug than the one being fixed."""
    first = json_schema_for(_Params)
    first["properties"] = {"tampered": True}

    second = json_schema_for(_Params)
    assert "quantity" in second["properties"]
    assert "tampered" not in second["properties"]
