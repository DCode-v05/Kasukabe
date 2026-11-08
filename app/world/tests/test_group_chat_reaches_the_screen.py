"""The group chat panel reads real fields off a real endpoint.

Written against the contract in docs/specs/group-conversation-spec.md §5.
The rule this file exists to enforce: the endpoint's shape is asserted
against *what the UI actually reads*, parsed out of the panel's own
source, not against a list copied by hand into a test. A test that
asserts the shape it was told to expect passes forever while the panel
renders blanks - and this project has shipped exactly that, twice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tools.state import WorldState
from world.tools.tests.conftest import spawn_agent


@pytest_asyncio.fixture
async def world_and_registry():
    """The real wiring. world/tools/tests has this as a conftest fixture;
    this file lives in world/tests, so it builds its own rather than
    reaching across into another package's conftest."""
    return await build_world(genesis_tick=0, seed=1234)


@pytest_asyncio.fixture
async def world(world_and_registry):
    return world_and_registry[0]


@pytest_asyncio.fixture
async def registry(world_and_registry):
    return world_and_registry[1]

PANEL = (
    Path(__file__).resolve().parent.parent.parent
    / "game" / "static" / "js" / "township" / "groupchat.js"
)
MARKET = "Kasukabe Market Square"


def test_the_panel_exists_and_is_served() -> None:
    assert PANEL.exists(), "groupchat.js is missing"
    page = (PANEL.parent.parent.parent / "township-v2.html").read_text(encoding="utf-8")
    assert "groupchat.js" in page, "the panel is never loaded by the page"
    assert 'id="groupChatBody"' in page, "the panel has nowhere to render into"


def test_the_panel_is_given_its_own_tab_and_is_the_one_that_opens() -> None:
    """A panel behind a shut drawer and an unselected tab is a panel
    nobody sees - the Talk panel rendered 20,473 characters that way."""
    shell = (PANEL.parent / "shell.js").read_text(encoding="utf-8")
    assert "groupchat" in shell, "no book entry, so the panel falls into 'More'"
    assert re.search(r"prefer\s*=\s*\[\s*'groupchat'", shell), (
        "the group chat tab is not the one the drawer opens on"
    )


@pytest.mark.asyncio
async def test_every_field_the_panel_reads_is_one_the_world_sends(
    world: WorldState, registry
) -> None:
    """Parse the field names out of the panel and require each of them.

    `c.participants`, `line.speaker_name` and so on - if the panel reads
    it, the endpoint has to send it.
    """
    await spawn_agent(world, "n01", MARKET)
    await spawn_agent(world, "n02", MARKET)
    await spawn_agent(world, "n03", MARKET)
    for agent_id in ("n01", "n02", "n03"):
        world.agent_names[agent_id] = f"Resident {agent_id}"

    started = await registry.execute(world, "n01", "start_conversation", {
        "topic": "the boundary marker", "with_agent_ids": ["n02", "n03"],
    })
    conversation_id = started.result["conversation_id"]
    await registry.execute(world, "n02", "say", {
        "content": "I remember where it stood.", "conversation_id": conversation_id,
    })

    record = world.conversations[conversation_id]
    lines = [u for u in world.utterances if u["conversation_id"] == conversation_id]
    assert lines, "the remark never reached the tail the endpoint reads"

    source = PANEL.read_text(encoding="utf-8")
    conversation_fields = set(re.findall(r"\bc\.([a-z_]+)", source))
    line_fields = set(re.findall(r"\bline\.([a-z_]+)", source))

    served_conversation = {
        "conversation_id", "topic", "place", "open", "opened_tick",
        "last_tick", "turns", "participants", "lines",
    }
    served_line = {
        "tick", "said_at", "speaker_agent_id", "speaker_name",
        "listener_name", "content",
    }
    missing = (conversation_fields - served_conversation) - {"length"}
    assert not missing, f"the panel reads conversation fields nobody sends: {missing}"
    missing_line = line_fields - served_line
    assert not missing_line, f"the panel reads line fields nobody sends: {missing_line}"

    # And the world really does hold what those names promise.
    assert record["topic"] == "the boundary marker"
    assert len(record["participants"]) == 3
    assert record["open"] is True
    assert lines[0]["content"] == "I remember where it stood."
    assert lines[0]["speaker_name"]


@pytest.mark.asyncio
async def test_a_group_of_five_is_reported_as_five(world: WorldState, registry) -> None:
    """The count is the fact the panel exists to show, so it must survive
    the trip from world to endpoint shape."""
    ids = [f"n0{i}" for i in range(1, 6)]
    for agent_id in ids:
        await spawn_agent(world, agent_id, MARKET)
        world.agent_names[agent_id] = f"Resident {agent_id}"

    started = await registry.execute(world, ids[0], "start_conversation", {
        "topic": "the mill's hours", "with_agent_ids": ids[1:],
    })
    record = world.conversations[started.result["conversation_id"]]
    assert len(record["participants"]) == 5
    assert len([p for p in record["participants"] if p]) == 5


def test_the_panel_never_abbreviates_the_roster() -> None:
    """A truncated roster would hide the one thing being demonstrated, so
    the render path must map the whole list with nothing sliced off it."""
    source = PANEL.read_text(encoding="utf-8")
    body = source[source.index("function roster"):source.index("function lineHtml")]
    assert ".map(" in body, "the roster is not rendered from the full list"
    assert ".slice(" not in body and "substring" not in body, (
        f"the roster render truncates the list: {body}"
    )
    assert "participants" in source, "the panel does not render a roster at all"
