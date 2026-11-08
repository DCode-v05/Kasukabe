"""A world that falls over has not refused you.

`ENG-18`. `world_tools.py` recorded a correction for every status at or
above 400. So a world server returning 500 wrote "the world refused
`harvest`" into the resident's learning store, and the learning pipeline
consolidated that during sleep into a durable belief about what the
resident is not allowed to do.

An outage became a lesson. Worse, a lesson that survives the outage: the
server comes back, the resident does not un-learn it, and every later
decision is made by somebody who believes harvesting is forbidden.

A refusal is a 4xx WITH a reason - the world declining a specific act and
saying why. A 4xx carrying no `error` body is not one either: it is a
malformed answer, and there is nothing in it a resident could learn from.

The resident still SEES the error text this turn. Only the durable record
is withheld, because teaching nothing is the right outcome when there is
nothing true to teach.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src.sim.world_tools import WorldClient


class _Store:
    """A signal store that only counts what was written to it."""

    def __init__(self) -> None:
        self.written: list[dict] = []

    async def write(self, *args, **kwargs):
        self.written.append({"args": args, "kwargs": kwargs})
        return True

    def __getattr__(self, name):
        async def _any(*a, **k):
            self.written.append({"via": name, "kwargs": k})
            return True
        return _any


def _client(store, handler) -> WorldClient:
    """The real constructor, with a mock transport underneath - so this
    exercises `call_tool` exactly as production does."""
    transport = httpx.MockTransport(handler)
    client = WorldClient(
        base_url="http://world",
        agent_id="a0001",
        client=httpx.AsyncClient(transport=transport, base_url="http://world"),
    )
    client.signal_store = store
    client.thread_id = "t1"
    return client


def _call(client, status: int, body):
    """Drive one tool call against a canned response."""
    async def go():
        return await client.call_tool("harvest", {"quantity": 1})

    return asyncio.run(go())


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_broken_world_teaches_nothing(status) -> None:
    """The finding. A 5xx is the world breaking, not refusing."""
    store = _Store()
    client = _client(
        store, lambda request: httpx.Response(status, json={"error": "boom"})
    )
    result = _call(client, status, None)

    assert "Error" in result, "the resident is still told this turn"
    assert client.corrections_recorded == 0, (
        f"ENG-18: a {status} was written to the resident's learning store "
        "as a refusal, so an outage becomes a durable belief that the act "
        "is forbidden"
    )


def test_a_real_refusal_is_still_learned() -> None:
    """The control, and it matters: this mechanism exists so a town that
    refuses the same resident the same way every morning teaches it
    something between mornings."""
    store = _Store()
    client = _client(
        store,
        lambda request: httpx.Response(
            403, json={"error": "requires location 'farmland:wheat'"}
        ),
    )
    result = _call(client, 403, None)

    assert "farmland" in result
    assert client.corrections_recorded == 1, (
        "ENG-18: a genuine refusal with a stated reason is no longer "
        "recorded, so the resident learns nothing from being told no"
    )


def test_a_4xx_with_no_reason_teaches_nothing() -> None:
    """A refusal the world cannot explain is not a refusal a resident can
    learn from - it is a malformed answer."""
    store = _Store()
    client = _client(store, lambda request: httpx.Response(400, text="Bad Request"))
    _call(client, 400, None)

    assert client.corrections_recorded == 0


def test_the_resident_is_told_either_way() -> None:
    """Withholding the LESSON must not withhold the message. A resident
    that gets no error text cannot react to the outage at all."""
    store = _Store()
    client = _client(
        store, lambda request: httpx.Response(500, json={"error": "database gone"})
    )
    result = _call(client, 500, None)

    assert "database gone" in result


def test_a_success_records_nothing() -> None:
    store = _Store()
    client = _client(
        store, lambda request: httpx.Response(200, json={"harvested": 3})
    )
    _call(client, 200, None)
    assert client.corrections_recorded == 0
