"""An explicit ASK rule survives every auto-allow path.

An operator writes "ask me before touching billing config" and expects to be
asked. Four separate fast paths in check_permission returned allow without ever
consulting the matched ASK rule:

  - bypass mode, which our own code documents as the supported mode for
    headless/agentic runs, so the guardrail was inert in exactly the mode
    production uses;
  - the sandbox safe-command allowlist;
  - the sed safe-pattern validator's standalone passthrough;
  - and an ALLOW rule, because `matched_allow` returned on the line *before*
    ask_forced was even assigned.

That last one is the quiet one: a persisted "always allow" from some earlier
session permanently shadows a narrower operator ASK rule added later, and
neither party ever sees that the ask stopped firing.

These drive the real check_permission with real rules. No mocks: the only
injected thing is an ask callback, which is the actual production seam.
"""

from __future__ import annotations

import pytest

from src.sandbox.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    ToolSource,
    check_permission,
)

BILLING_ASK = PermissionRule(
    tool="ad:stripe.update_subscription",
    behavior=PermissionBehavior.ASK,
    reason="billing changes need a human",
    source="operator_global",
)


def asked_recorder():
    """A real approval channel that records what it was asked and says yes."""
    seen: list[dict] = []

    async def ask(payload):
        seen.append(payload)
        return "allow"

    return ask, seen


# --- bypass mode ------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_mode_does_not_defeat_an_ask_rule():
    """The headline defect: the guardrail was inert in the mode headless runs use."""
    ask, seen = asked_recorder()
    result = await check_permission(
        tool_name="ad:stripe.update_subscription",
        tool_input={"plan": "enterprise"},
        source=ToolSource.MCP,
        rules=[BILLING_ASK],
        ask_user_fn=ask,
        permission_mode=PermissionMode.BYPASS,
    )
    assert result.allowed is True
    assert len(seen) == 1, "bypass mode auto-allowed without asking"


@pytest.mark.asyncio
async def test_bypass_with_an_ask_rule_and_no_channel_fails_loudly():
    """No approval channel means denied with a reason, never a silent allow."""
    result = await check_permission(
        tool_name="ad:stripe.update_subscription",
        tool_input={"plan": "enterprise"},
        source=ToolSource.MCP,
        rules=[BILLING_ASK],
        ask_user_fn=None,
        permission_mode=PermissionMode.BYPASS,
    )
    assert result.allowed is False
    assert result.reason


@pytest.mark.asyncio
async def test_bypass_still_auto_allows_when_no_ask_rule_matches():
    """The guard must not disable bypass mode itself."""
    result = await check_permission(
        tool_name="ad:stripe.list_invoices",
        tool_input={},
        source=ToolSource.MCP,
        rules=[BILLING_ASK],
        ask_user_fn=None,
        permission_mode=PermissionMode.BYPASS,
    )
    assert result.allowed is True


# --- ALLOW vs ASK -----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_allow_rule_does_not_shadow_a_narrower_ask_rule():
    """A persisted "always allow" must not silently cancel an operator ASK."""
    ask, seen = asked_recorder()
    result = await check_permission(
        tool_name="ad:stripe.update_subscription",
        tool_input={"plan": "enterprise"},
        source=ToolSource.MCP,
        rules=[
            PermissionRule(
                tool="ad:stripe.*",
                behavior=PermissionBehavior.ALLOW,
                reason="always allow (persisted from an earlier session)",
                source="operator_global",
            ),
            BILLING_ASK,
        ],
        ask_user_fn=ask,
    )
    assert result.allowed is True
    assert len(seen) == 1, "the allow rule shadowed the ask rule"


@pytest.mark.asyncio
async def test_rule_order_does_not_change_the_outcome():
    """ASK wins whichever order the rules were collected in."""
    allow = PermissionRule(
        tool="ad:stripe.*", behavior=PermissionBehavior.ALLOW, source="operator_global"
    )
    for rules in ([allow, BILLING_ASK], [BILLING_ASK, allow]):
        ask, seen = asked_recorder()
        await check_permission(
            tool_name="ad:stripe.update_subscription",
            tool_input={},
            source=ToolSource.MCP,
            rules=list(rules),
            ask_user_fn=ask,
        )
        assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_plain_allow_rule_still_allows_without_prompting():
    """The guard must not turn every allow into a prompt."""
    ask, seen = asked_recorder()
    result = await check_permission(
        tool_name="ad:stripe.list_invoices",
        tool_input={},
        source=ToolSource.MCP,
        rules=[
            PermissionRule(
                tool="ad:stripe.*",
                behavior=PermissionBehavior.ALLOW,
                source="operator_global",
            )
        ],
        ask_user_fn=ask,
    )
    assert result.allowed is True
    assert seen == []


# --- DENY still outranks everything ----------------------------------------


@pytest.mark.asyncio
async def test_deny_still_beats_ask_and_allow():
    """Reordering ASK above ALLOW must not disturb DENY's precedence."""
    ask, seen = asked_recorder()
    result = await check_permission(
        tool_name="ad:stripe.update_subscription",
        tool_input={},
        source=ToolSource.MCP,
        rules=[
            BILLING_ASK,
            PermissionRule(
                tool="ad:stripe.update_subscription",
                behavior=PermissionBehavior.DENY,
                reason="frozen during audit",
                source="operator_global",
            ),
        ],
        ask_user_fn=ask,
        permission_mode=PermissionMode.BYPASS,
    )
    assert result.allowed is False
    assert seen == [], "a denied call must never reach the approval channel"


# --- sandbox safe-command allowlist ----------------------------------------


@pytest.mark.asyncio
async def test_sandbox_auto_allow_does_not_defeat_an_ask_rule():
    ask, seen = asked_recorder()
    result = await check_permission(
        tool_name="bash",
        tool_input={"command": "ls -la"},
        source=ToolSource.SANDBOXED,
        rules=[
            PermissionRule(
                tool="bash",
                behavior=PermissionBehavior.ASK,
                reason="this tenant reviews every shell command",
                source="operator_global",
            )
        ],
        ask_user_fn=ask,
    )
    assert result.allowed is True
    assert len(seen) == 1, "the sandbox allowlist auto-allowed past the ask rule"


@pytest.mark.asyncio
async def test_sandbox_auto_allow_still_works_without_an_ask_rule():
    ask, seen = asked_recorder()
    result = await check_permission(
        tool_name="bash",
        tool_input={"command": "ls -la"},
        source=ToolSource.SANDBOXED,
        rules=[],
        ask_user_fn=ask,
    )
    assert result.allowed is True
    assert seen == []


# --- sed validator passthrough ---------------------------------------------


@pytest.mark.asyncio
async def test_sed_passthrough_does_not_defeat_an_ask_rule():
    ask, seen = asked_recorder()
    result = await check_permission(
        tool_name="bash",
        tool_input={"command": "sed -i 's/foo/bar/' notes.txt"},
        source=ToolSource.SANDBOXED,
        rules=[
            PermissionRule(
                tool="bash",
                behavior=PermissionBehavior.ASK,
                reason="this tenant reviews every shell command",
                source="operator_global",
            )
        ],
        ask_user_fn=ask,
    )
    assert result.allowed is True
    assert len(seen) == 1, "the sed passthrough auto-allowed past the ask rule"
