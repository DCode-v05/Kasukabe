"""A learning drain that fails says which failure it was.

Run 28, 15:34:07, nine lines in the same millisecond:

    WARNING end-of-run learning drain failed for n16:
    WARNING end-of-run learning drain failed for n02:
    ...
    INFO    end-of-run learning drain: 11 of 20 resident(s) consolidated

A blank where the reason should be. `drain_learning` logged `str(exc)`,
and httpx's timeout and transport errors carry no message - the exact trap
`engine_failure_detail` was written for on the `/tick` path ("the line read
'tick dispatch failed for n07: ' with a blank where the reason should
be"). The drain path never adopted it.

The blank also hid that "11 of 20" undercounted: all nine residents'
learning calls still landed in the trace between 15:34:11 and 15:34:23,
after the runner had given up on them. Which of ReadTimeout, ReadError or
RemoteProtocolError it was is the whole diagnosis, and it is not in the log.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from world.run_genesis import HttpAgentGateway


def _gateway(error: Exception) -> HttpAgentGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    gateway = HttpAgentGateway({"n16": 9115})
    gateway.clients["n16"] = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://engine"
    )
    return gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    httpx.ReadTimeout(""),
    httpx.ReadError(""),
], ids=["read-timeout", "read-error"])
async def test_the_failure_class_is_in_the_line(caplog, error):
    gateway = _gateway(error)

    with caplog.at_level(logging.WARNING):
        drained = await gateway.drain_learning()

    assert drained == {"n16": False}
    assert type(error).__name__ in caplog.text, (
        f"the drain failed and the log does not say how: {caplog.text!r}"
    )


@pytest.mark.asyncio
async def test_a_failure_never_stops_the_other_drains(caplog):
    """Best-effort by construction, as before: one engine's failure is a
    line in the log, not an exception out of the drain."""
    gateway = _gateway(httpx.ReadTimeout(""))

    drained = await gateway.drain_learning()

    assert drained == {"n16": False}
