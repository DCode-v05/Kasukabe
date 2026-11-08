"""Tests for the connected_services prompt section.

Verifies that ExecutionThread.refresh_connected_services_snapshot() reads
from Search.ad_connections() and emits structured data only — no prose,
no per-service hardcoding, no imperative LLM corrections.
"""

from __future__ import annotations

import pytest

from src.engine_core.thread import ExecutionThread


class FakeSearch:
    """Stand-in for the Search instance with a controllable ad_connections() return."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def ad_connections(self) -> dict:
        return self.payload


def stub_thread(search) -> ExecutionThread:
    t = ExecutionThread.__new__(ExecutionThread)
    t.search = search
    t.connected_services_text = ""
    return t


@pytest.mark.asyncio
async def test_no_search_yields_empty_snapshot():
    t = stub_thread(search=None)
    await t.refresh_connected_services_snapshot()
    assert t.connected_services_text == ""


@pytest.mark.asyncio
async def test_no_active_connections_yields_empty_snapshot():
    search = FakeSearch(
        {
            "servers": [{"server_name": "gmail", "display_name": "Gmail"}],
            "connections": [{"server_name": "gmail", "status": "INITIATED"}],
            "action_names_by_server": {"gmail": ["list_messages"]},
        }
    )
    t = stub_thread(search)
    await t.refresh_connected_services_snapshot()
    assert t.connected_services_text == ""


@pytest.mark.asyncio
async def test_active_connections_render_structured_block():
    search = FakeSearch(
        {
            "servers": [
                {"server_name": "gmail", "display_name": "Gmail"},
                {"server_name": "slack", "display_name": "Slack"},
                {"server_name": "ignored", "display_name": "Ignored"},
            ],
            "connections": [
                {"server_name": "gmail", "status": "ACTIVE"},
                {"server_name": "slack", "status": "ACTIVE"},
                {"server_name": "ignored", "status": "INACTIVE"},
            ],
            "action_names_by_server": {
                "gmail": ["send_message", "list_messages"],
                "slack": ["post_message"],
                "ignored": ["never_listed"],
            },
        }
    )
    t = stub_thread(search)
    await t.refresh_connected_services_snapshot()

    text = t.connected_services_text
    assert text.startswith("CONNECTED SERVICES:")
    assert "Gmail (gmail):" in text
    assert "Slack (slack):" in text
    assert "Ignored" not in text
    assert "ad:gmail.list_messages" in text
    assert "ad:gmail.send_message" in text
    assert "ad:slack.post_message" in text
    forbidden = [
        "do not tell",
        "authorization is required",
        "credentials are missing",
        "WHEN TO USE",
        "decryption",
        "live",
        "inbox",
    ]
    for token in forbidden:
        assert token.lower() not in text.lower(), (
            f"connected_services prompt must not contain prose token {token!r}; got:\n{text}"
        )


@pytest.mark.asyncio
async def test_servers_sorted_alphabetically():
    search = FakeSearch(
        {
            "servers": [
                {"server_name": "zoom", "display_name": "Zoom"},
                {"server_name": "asana", "display_name": "Asana"},
                {"server_name": "gmail", "display_name": "Gmail"},
            ],
            "connections": [
                {"server_name": "zoom", "status": "ACTIVE"},
                {"server_name": "asana", "status": "ACTIVE"},
                {"server_name": "gmail", "status": "ACTIVE"},
            ],
            "action_names_by_server": {
                "zoom": ["create_meeting"],
                "asana": ["list_tasks"],
                "gmail": ["list_messages"],
            },
        }
    )
    t = stub_thread(search)
    await t.refresh_connected_services_snapshot()

    text = t.connected_services_text
    asana_idx = text.index("Asana")
    gmail_idx = text.index("Gmail")
    zoom_idx = text.index("Zoom")
    assert asana_idx < gmail_idx < zoom_idx


@pytest.mark.asyncio
async def test_server_with_no_actions_renders_placeholder():
    search = FakeSearch(
        {
            "servers": [{"server_name": "newsvc", "display_name": "New Service"}],
            "connections": [{"server_name": "newsvc", "status": "ACTIVE"}],
            "action_names_by_server": {},
        }
    )
    t = stub_thread(search)
    await t.refresh_connected_services_snapshot()
    assert "New Service (newsvc): (no tools registered)" in t.connected_services_text


@pytest.mark.asyncio
async def test_search_failure_yields_empty_snapshot():
    class Boom:
        async def ad_connections(self) -> dict:
            raise RuntimeError("db down")

    t = stub_thread(Boom())
    await t.refresh_connected_services_snapshot()
    assert t.connected_services_text == ""
