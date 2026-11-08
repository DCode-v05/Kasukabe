"""Real tests for the ``defer_loading`` plumbing across the tool registry
and the herald → Anthropic adapter.

Why this exists
---------------
Anthropic's Beta tool API supports ``defer_loading: true`` on tool schemas
(GA on anthropic-python 0.97+). Engine inherits that capability so we can
mark rarely-called heavy-schema tools as deferred — the API ships their
*names* to the model but withholds the full schema until tool-search pulls
them in. Cuts cache-write tokens on cold turns without removing capability.

These tests pin three contracts:

1. The engine ``ToolDef`` (in ``src/utility_directory/tool_runtime.py``)
   carries a ``defer_loading: bool`` field defaulting to ``False`` — so no
   existing registration is changed by the upgrade.
2. ``ToolRegistry.to_tool_defs()`` propagates the flag onto the
   provider-neutral herald ``ToolDef`` (in ``src/herald/types.py``).
3. ``to_wire_tools()`` (in ``src/herald/provider.py``) emits
   ``defer_loading: true`` on the schema dict only for tools that opted in,
   and never on tools that did not — so the Anthropic API sees byte-for-byte
   identical tool arrays on existing deployments.

References:
- /Others/src/Tool.ts:439  (defer_loading field on Tool spec)
- /Others/src/utils/api.ts:127, 224  (deferLoading → defer_loading wiring)
- /Others/src/utils/toolSearch.ts  (consumer side)
"""

from __future__ import annotations

from src.herald.provider import to_wire_tools
from src.herald.types import ToolDef as HeraldToolDef
from src.utility_directory.tool_runtime import ToolDef as RegistryToolDef, ToolRegistry


# --------------------------------------------------------------------------
# RegistryToolDef field
# --------------------------------------------------------------------------


async def noop_handler(_: dict) -> str:
    return ""


class TestRegistryToolDefFlag:
    def test_defaults_to_false(self):
        tool = RegistryToolDef(
            name="t",
            description="d",
            input_schema={"type": "object"},
            handler=noop_handler,
        )
        assert tool.defer_loading is False

    def test_can_be_set_true(self):
        tool = RegistryToolDef(
            name="t",
            description="d",
            input_schema={"type": "object"},
            handler=noop_handler,
            defer_loading=True,
        )
        assert tool.defer_loading is True


# --------------------------------------------------------------------------
# Registry → herald adapter propagation
# --------------------------------------------------------------------------


class TestRegistryPropagation:
    def test_to_tool_defs_propagates_defer_loading(self):
        reg = ToolRegistry()
        reg.register(
            RegistryToolDef(
                name="eager_one",
                description="eager",
                input_schema={"type": "object"},
                handler=noop_handler,
            )
        )
        reg.register(
            RegistryToolDef(
                name="deferred_one",
                description="deferred",
                input_schema={"type": "object"},
                handler=noop_handler,
                defer_loading=True,
            )
        )

        defs = reg.to_tool_defs()
        by_name = {d.name: d for d in defs}
        assert isinstance(by_name["eager_one"], HeraldToolDef)
        assert by_name["eager_one"].defer_loading is False
        assert by_name["deferred_one"].defer_loading is True


# --------------------------------------------------------------------------
# Herald adapter → Anthropic schema
# --------------------------------------------------------------------------


class TestAnthropicSchemaEmission:
    def test_no_deferred_tools_emits_no_defer_key(self):
        # Critical regression contract: existing deployments must produce
        # byte-for-byte identical tool schemas after this change.
        defs = [
            HeraldToolDef(name="a", description="d_a", input_schema={"type": "object"}),
            HeraldToolDef(name="b", description="d_b", input_schema={"type": "object"}),
        ]
        schemas = to_wire_tools(defs)
        for s in schemas:
            assert "defer_loading" not in s, (
                "defer_loading must not appear when no tool opted in — "
                "would change the cache fingerprint Anthropic sees."
            )

    def test_deferred_tool_emits_defer_key(self):
        defs = [
            HeraldToolDef(
                name="rare",
                description="d",
                input_schema={"type": "object"},
                defer_loading=True,
            ),
        ]
        schemas = to_wire_tools(defs)
        assert schemas[0]["defer_loading"] is True

    def test_mixed_set_only_marks_deferred_tools(self):
        defs = [
            HeraldToolDef(name="hot", description="d", input_schema={"type": "object"}),
            HeraldToolDef(
                name="rare",
                description="d",
                input_schema={"type": "object"},
                defer_loading=True,
            ),
            HeraldToolDef(name="hotter", description="d", input_schema={"type": "object"}),
        ]
        schemas = to_wire_tools(defs)
        by_name = {s["name"]: s for s in schemas}
        assert "defer_loading" not in by_name["hot"]
        assert by_name["rare"]["defer_loading"] is True
        assert "defer_loading" not in by_name["hotter"]

    def test_schema_carries_required_anthropic_fields(self):
        defs = [
            HeraldToolDef(
                name="t",
                description="d",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                defer_loading=True,
            ),
        ]
        schemas = to_wire_tools(defs)
        s = schemas[0]
        assert s["name"] == "t"
        assert s["description"] == "d"
        assert s["input_schema"] == {"type": "object", "properties": {"x": {"type": "string"}}}
        assert s["defer_loading"] is True
