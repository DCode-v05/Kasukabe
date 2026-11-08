"""A double that has stopped matching its interface is not a double.

`UI-13`. `WorldClient` is a Protocol with fifteen methods.
`MockWorldClient` implemented seven of them - `get_workshop`,
`get_council`, `get_talk`, `get_conversations`, `get_authored`,
`get_vitals`, `get_preflight` and `get_activity` were all missing.

Nothing failed, and that is the finding. `server.py` reaches every one of
them through `getattr(client, "get_vitals", None)`, so an incomplete double
does not raise - it makes a panel quietly empty. The same guard would
swallow a REAL client that lost a method in a rename, which is the case
that actually costs something.

This test is the reason to prefer a Protocol over a duck: it can be
CHECKED. Asserted over the whole interface rather than the eight that were
found, because the next one to drift will be a different one.
"""
from __future__ import annotations

import inspect

import pytest

from game.mock_world import MockWorldClient
from game.world_client import HttpWorldClient, WorldClient


def _protocol_methods() -> set[str]:
    return {
        name
        for name, _ in inspect.getmembers(WorldClient, predicate=callable)
        if not name.startswith("_")
    }


def _implemented(cls) -> set[str]:
    return {
        name
        for name, _ in inspect.getmembers(cls, predicate=callable)
        if not name.startswith("_")
    }


def test_the_protocol_is_not_empty() -> None:
    """A protocol that resolved to nothing would make both tests below
    vacuously true."""
    assert len(_protocol_methods()) >= 10


@pytest.mark.parametrize("client", [MockWorldClient, HttpWorldClient],
                         ids=["mock", "http"])
def test_every_client_implements_the_whole_protocol(client) -> None:
    missing = sorted(_protocol_methods() - _implemented(client))
    assert not missing, (
        f"UI-13: {client.__name__} is missing {len(missing)} Protocol "
        f"method(s), which server.py's getattr() calls will silently render "
        f"as an empty panel: {missing}"
    )


@pytest.mark.asyncio
async def test_the_mock_answers_the_eight_it_was_missing() -> None:
    """Present is not the same as callable. Each returns the shape its real
    counterpart's route returns for a town with nothing of that kind going
    on - the honest answer for a twelve-agent fake."""
    from game.config import GameSettings

    client = MockWorldClient(GameSettings(mock_mode=True))
    try:
        assert await client.get_workshop() == {"skills": [], "tools": [], "patents": []}
        assert (await client.get_council())["seats"] == []
        assert (await client.get_talk())["count"] == 0
        assert (await client.get_conversations())["count"] == 0
        assert (await client.get_authored())["count"] == 0
        assert "alive" in await client.get_vitals()
        assert (await client.get_preflight())["ok"] is False
        assert "agents" in await client.get_activity()
    finally:
        await client.close()
