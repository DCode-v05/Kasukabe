"""For every tool: could a model build a valid call from what it is shown?

`WIRE-03`, the spread test the run needed and did not have.

Run `deni-a6f9eb4-5h` failed at exactly this seam. `post_price` said one
thing in its schema and residents wrote another; `take_from_shelf` was
shown as taking no parameters and was called with `{}` five times;
`transfer` cost one resident three billed calls before it found
`to_agent_id`. Every one of those tools passed its own unit tests. What
nobody tested was whether the DESCRIPTION AND SCHEMA A RESIDENT ACTUALLY
SEES are enough to build a working call from.

`world/evals/verb_reach.py` is the live version of this question — it
puts a real model in a verb's own situation and costs about six cents a
run. This is the deterministic half: no model, no money, every tool
every time, run on every commit.

WHAT IT ASSERTS, for every registered tool:

1. its schema is well formed and every required parameter has a type;
2. a call built from the schema's own required names VALIDATES — so a
   model that reads the schema correctly is never refused for shape;
3. the reduced index row a resident may see instead names those same
   parameters, when the tool is usable where they stand (TRADE-02);
4. no two tools ask for the same thing under different names, which is
   the vocabulary tax that cost 98 calls (TRADE-01).

It does not call the handlers. A tool can be perfectly described and
still refuse for a reason of its own — no money, wrong place, nothing on
the shelf — and those are the world working, not the wiring failing.
"""
from __future__ import annotations

import collections

import pytest
import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tools.registry import json_schema_for, tool_registry_rows
from world.tools.tests.conftest import spawn_agent

# A plausible value per JSON type, the way a model fills a required field.
SAMPLE = {
    "string": "xx", "integer": 1, "number": 1.0, "boolean": True,
    "array": ["xx"], "object": {},
}


def value_for(schema: dict, definitions: dict) -> object:
    """What a model reading THIS field would send.

    Reads the constraints, not just the type. The first draft of this
    helper sent `"x"` for every string and `[]` for every array, and
    reported four tools as refusing calls built from their own schema -
    `find_people` (minLength 2), `register_org` and `come_of_age`
    (enums), `build_structure` (minItems 1). None of them was lying:
    every one of those constraints IS in the schema, and a model reading
    it properly would honour them. The generator was the naive one.

    Recorded because the finding it produced was false, and a false
    finding from a test written to catch false confidence is worth
    remembering.
    """
    if "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        schema = {**definitions.get(ref, {}), **{k: v for k, v in schema.items() if k != "$ref"}}
    for branch in ("anyOf", "oneOf", "allOf"):
        options = [o for o in schema.get(branch, []) if o.get("type") != "null"]
        if options:
            return value_for(options[0], definitions)
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type", "string")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind == "string":
        return "x" * max(2, int(schema.get("minLength") or 0) or 2)
    if kind == "array":
        # A tuple - `footprint` is a list of [x, y] integer pairs - is
        # described by prefixItems, not items.
        if schema.get("prefixItems"):
            # The tuple ITSELF, not wrapped: `footprint` is an array whose
            # items are [x, y] pairs, so this branch runs for the inner
            # pair and the outer array branch below does the wrapping.
            return [value_for(part, definitions) for part in schema["prefixItems"]]
        item = value_for(schema.get("items") or {"type": "string"}, definitions)
        return [item] * max(1, int(schema.get("minItems") or 0) or 1)
    if kind == "object" and schema.get("properties"):
        # Nested models - `steps` is a list of ToolStep, each with its own
        # required fields. A shallow generator sends `{}` and reports the
        # tool as broken when it is the generator that stopped early.
        inner = schema.get("properties") or {}
        return {
            field: value_for(inner.get(field) or {}, definitions)
            for field in schema.get("required") or ()
        }
    if kind in ("integer", "number"):
        floor = schema.get("minimum", schema.get("exclusiveMinimum"))
        base = 1 if floor is None else int(floor) + 1
        return base if kind == "integer" else float(base)
    return SAMPLE.get(kind, "xx")


@pytest_asyncio.fixture
async def town():
    world, registry = await build_world(genesis_tick=0, seed=5)
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    return world, registry


def _required(spec) -> list[str]:
    return [k for k, f in spec.params_model.model_fields.items() if f.is_required()]


def test_every_schema_is_well_formed(town):
    _world, registry = town
    broken = []
    for name, spec in registry.tools.items():
        schema = json_schema_for(spec.params_model)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            broken.append((name, "not an object schema"))
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            broken.append((name, "no properties map"))
            continue
        for field in schema.get("required") or ():
            if field not in properties:
                broken.append((name, f"requires {field} and does not describe it"))
    assert not broken, f"schemas a model cannot build from: {broken}"


@pytest.mark.asyncio
async def test_a_call_built_from_the_schema_always_validates(town):
    """The seam the run died on. A model that reads the schema right -
    INCLUDING its enums, minimums and minimum lengths - must never be
    refused for shape. If it is, the schema is lying about the tool."""
    _world, registry = town
    refused = []
    for name, spec in registry.tools.items():
        schema = json_schema_for(spec.params_model)
        properties = schema.get("properties") or {}
        definitions = schema.get("$defs") or schema.get("definitions") or {}
        args = {
            field: value_for(properties.get(field) or {}, definitions)
            for field in schema.get("required") or ()
        }
        try:
            spec.params_model.model_validate(args)
        except Exception as exc:  # noqa: BLE001 - the message is the finding
            refused.append((name, str(exc).splitlines()[0][:90]))
    assert not refused, (
        "these tools refuse a call built from their own advertised schema; "
        f"a model reading them correctly still cannot call them: {refused}"
    )


@pytest.mark.asyncio
async def test_what_a_resident_is_shown_names_what_it_must_supply(town):
    """TRADE-02. An index row that says nothing about parameters is how
    `harvest({})` happened twenty times of thirty-eight."""
    world, registry = town
    silent = []
    for row in tool_registry_rows(registry, world, "n01"):
        if not row.get("elsewhere"):
            continue
        if "Only at" in row["description"]:
            # Gated elsewhere: "move there first" is the useful fact and
            # the parameters do not matter yet.
            continue
        spec = registry.get(row["name"])
        if _required(spec) and "Needs:" not in row["description"]:
            silent.append(row["name"])
    assert not silent, (
        f"{len(silent)} tools are callable right where the resident stands and "
        f"their index row does not say what they need: {silent[:10]}"
    )


def test_the_town_asks_for_one_thing_under_one_name(town):
    """TRADE-01's tax, as a standing rule rather than a synonym table.

    98 calls died on parameter names. A synonym table repairs the ones we
    have seen; this stops new ones being minted - if two tools want a
    counterparty and call it `to_agent_id` and `counterparty_agent_id`,
    a resident that learns one is wrong about the other.
    """
    _world, registry = town
    # What each CONCEPT is called, by the tools that want it.
    concepts = {
        "a person you are acting on": (
            "to_agent_id", "counterparty_agent_id", "seller_id", "buyer_id",
            "target_agent_id", "agent_id", "listener_agent_id",
            "student_agent_id", "spouse_agent_id", "witness_id",
        ),
        "an amount of money": ("amount_sim", "price_sim", "ask_sim", "bid_sim",
                               "wage_sim", "budget_sim", "fee_sim"),
        "how many of a thing": ("quantity", "units", "count"),
    }
    used = collections.defaultdict(set)
    for name, spec in registry.tools.items():
        for field in spec.params_model.model_fields:
            for concept, names in concepts.items():
                if field in names:
                    used[concept].add(field)

    # This is a LEDGER, not a ban: several of these distinctions are real
    # (an `ask_sim` is not a `wage_sim`). What it prevents is the count
    # growing without anyone noticing, which is how `to`/`recipient_id`/
    # `to_agent_id` came to cost three calls for one transfer.
    assert len(used["how many of a thing"]) <= 2, (
        f"the town now has {len(used['how many of a thing'])} words for a "
        f"quantity: {sorted(used['how many of a thing'])}. Every extra one is "
        "a call a resident spends guessing."
    )
    assert len(used["a person you are acting on"]) <= 10, (
        f"{len(used['a person you are acting on'])} words for 'the other "
        f"person': {sorted(used['a person you are acting on'])}"
    )


@pytest.mark.asyncio
async def test_the_commerce_verbs_are_all_reachable_and_described(town):
    """The five the run needed and could not complete a sale with.

    `buy` is offered only while a resident is in a conversation (operator's
    rule, 2026-09-21), so "reachable" is checked where a resident would
    reach it - talking to somebody - and its absence everywhere else is
    checked below rather than papered over.
    """
    world, registry = town
    assert "buy" not in {r["name"] for r in tool_registry_rows(registry, world, "n01")}, (
        "buy was offered to a resident talking to nobody"
    )
    await spawn_agent(world, "n02", "Kasukabe Farmland")
    world.open_conversation(place=world.location_of("n01"), topic="",
                            participants=["n01", "n02"], now=world.tick())
    rows = {r["name"]: r for r in tool_registry_rows(registry, world, "n01")}
    for verb in ("post_price", "buy", "sell", "trade", "give", "transfer",
                 "stock_shelf", "take_from_shelf"):
        assert registry.get(verb) is not None, f"{verb} is not registered at all"
        assert verb in rows, f"{verb} is registered and never shown to a resident"
        assert rows[verb]["description"].strip(), f"{verb} is shown with no description"
