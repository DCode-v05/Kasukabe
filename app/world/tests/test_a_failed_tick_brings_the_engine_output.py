"""A 500 from an engine is logged with what the engine printed.

Run 26: three engines answered `/tick` with a bare 500. The response body
was uvicorn's "Internal Server Error" and nothing else, because the
traceback went to the ENGINE's stdout - which the supervisor keeps in a
bounded tail and nothing read at the moment of failure. By the time anybody
looked the world had shut down, and two of the three are unexplained.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from world.run_genesis import HttpAgentGateway

TRACEBACK = [
    "Traceback (most recent call last):",
    '  File "engine/src/sim/agent_adapter.py", line 212, in tick',
    "KeyError: 'program_stack'",
]


def _gateway(status: int, tail) -> HttpAgentGateway:
    gateway = HttpAgentGateway({"a0090": 9105}, output_tail=tail)
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="Internal Server Error"))
    gateway.clients["a0090"] = httpx.AsyncClient(transport=transport, base_url="http://engine")
    return gateway


@pytest.mark.asyncio
async def test_a_500_carries_the_engines_last_lines(caplog):
    gateway = _gateway(500, lambda agent_id: list(TRACEBACK))

    with caplog.at_level(logging.WARNING):
        outcome = await gateway.send_tick("a0090", 100, False, "perception")

    assert not outcome.succeeded
    assert "KeyError: 'program_stack'" in caplog.text, caplog.text


@pytest.mark.asyncio
async def test_an_engine_that_printed_nothing_says_so(caplog):
    gateway = _gateway(500, lambda agent_id: [])

    with caplog.at_level(logging.WARNING):
        await gateway.send_tick("a0090", 100, False, "perception")

    assert "printed nothing" in caplog.text


@pytest.mark.asyncio
async def test_a_4xx_is_not_decorated(caplog):
    """A refusal is an answer, not a crash; the tail would be noise."""
    gateway = _gateway(422, lambda agent_id: list(TRACEBACK))

    with caplog.at_level(logging.WARNING):
        await gateway.send_tick("a0090", 100, False, "perception")

    assert "KeyError" not in caplog.text


@pytest.mark.asyncio
async def test_a_tail_that_fails_to_read_does_not_replace_the_failure(caplog):
    def broken(agent_id):
        raise RuntimeError("supervisor went away")

    gateway = _gateway(500, broken)

    with caplog.at_level(logging.WARNING):
        outcome = await gateway.send_tick("a0090", 100, False, "perception")

    assert not outcome.succeeded
    assert "tick dispatch failed for a0090" in caplog.text


@pytest.mark.asyncio
async def test_no_tail_reader_is_the_old_behaviour(caplog):
    gateway = _gateway(500, None)

    with caplog.at_level(logging.WARNING):
        outcome = await gateway.send_tick("a0090", 100, False, "perception")

    assert not outcome.succeeded
