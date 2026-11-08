"""A world briefly unreachable is not a world that said no.

`TRADE-05`. Run `deni-a6f9eb4-5h` lost **97 calls** to
`Error: world server unreachable` — 8% of every tool call the town
managed to make. The runner log for that run is clean: four dispatch
failures across 351 ticks. So the world was not down. It was healthy and
briefly unreachable, under fifty engines all posting at once.

What that cost is worse than the calls. A resident cannot tell a
transport blip from a refusal — both arrive as `Error: ...` — so it
reads "the world would not let me", and reasons from that for the rest
of the turn having done nothing. One of the 97 was a `take_from_shelf`.

Two properties, and the second matters more than the first:

* a transport failure is retried, because an `httpx.HTTPError` means no
  response was received at all and the request provably did not land;
* **a refusal is never retried.** A 4xx or a 5xx is an answer. Re-posting
  a request the world may already have applied is how one act becomes
  three, and `POST /tool_calls` is not idempotent.
"""
from __future__ import annotations

import httpx
import pytest

from src.sim.world_tools import (
    WORLD_CALL_ATTEMPTS,
    WorldClient,
)


class _Transport:
    """Answers a scripted sequence, counting how often it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    async def post(self, url, json=None, **kw):
        self.calls += 1
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status, json=body, request=httpx.Request("POST", "http://world/x")
    )


def _client(transport) -> WorldClient:
    client = WorldClient("http://world", agent_id="n01", client=transport)
    return client


@pytest.mark.asyncio
async def test_a_blip_is_retried_and_the_call_lands():
    blip = httpx.ConnectError("connection reset")
    transport = _Transport(blip, _response(200, {"ok": True, "result": "done"}))

    answer = await _client(transport).call_tool("take_from_shelf", {"item": "flour"})

    assert transport.calls == 2, "the call was not retried"
    assert not answer.startswith("Error"), f"a recovered call still read as an error: {answer}"


@pytest.mark.asyncio
async def test_it_gives_up_after_a_bounded_number_of_tries():
    blip = httpx.ConnectError("connection reset")
    transport = _Transport(blip)

    answer = await _client(transport).call_tool("look_around", {})

    assert transport.calls == WORLD_CALL_ATTEMPTS
    assert answer.startswith("Error")


@pytest.mark.asyncio
async def test_when_it_gives_up_it_says_the_call_never_arrived():
    """The half that matters. A resident that cannot tell a blip from a
    refusal reasons for the rest of its turn from "the world said no"."""
    transport = _Transport(httpx.ConnectError("connection reset"))

    answer = await _client(transport).call_tool("take_from_shelf", {})

    assert "never arrived" in answer
    assert "nothing was refused" in answer.lower()
    assert "take_from_shelf" in answer


@pytest.mark.asyncio
async def test_a_refusal_is_never_retried():
    """A 4xx is an answer. `POST /tool_calls` is not idempotent, and
    re-posting a request the world may already have applied is how one
    act becomes three."""
    transport = _Transport(_response(400, {"error": "harvest: crop required"}))

    answer = await _client(transport).call_tool("harvest", {})

    assert transport.calls == 1, "a refusal was re-posted"
    assert "crop required" in answer


@pytest.mark.asyncio
async def test_a_server_error_is_never_retried_either():
    """A 5xx is the world breaking, not the world being unreachable -
    ENG-18's distinction, and it must survive this change. Re-posting
    into a broken world is the worst of both."""
    transport = _Transport(_response(500, {"error": "boom"}))

    await _client(transport).call_tool("harvest", {"crop": "wheat"})

    assert transport.calls == 1, "a 5xx was re-posted"


@pytest.mark.asyncio
async def test_a_healthy_call_is_posted_exactly_once():
    transport = _Transport(_response(200, {"ok": True}))

    await _client(transport).call_tool("look_around", {})

    assert transport.calls == 1


# ── WIRE-02: the failure mode now has an instrument ──────────────────


class TestTheLinkToTheWorldIsCounted:
    """`WIRE-02`. 97 calls died in transport in run `deni-a6f9eb4-5h`
    and **nothing in the running system knew**.

    The world could not count them — it was the thing that was
    unreachable. The runner had no counter. No endpoint reported them.
    They were findable only by grepping session logs after the run was
    over, which is how they went unnoticed for four runs. A failure mode
    with no instrument is a failure mode nobody fixes.

    `recovered` is the number that says whether TRADE-05's retry is
    earning its keep: it separates "the link is fine" from "the link is
    bad and the retry is carrying us", and those need different fixes.
    """

    @pytest.mark.asyncio
    async def test_a_healthy_link_reports_zeroes(self):
        transport = _Transport(_response(200, {"ok": True}))
        client = _client(transport)

        await client.call_tool("look_around", {})
        wire = client.wire_health()

        assert wire["world_calls_attempted"] == 1
        assert wire["world_transport_failures"] == 0
        assert wire["world_transport_recovered"] == 0
        assert wire["world_calls_refused"] == 0

    @pytest.mark.asyncio
    async def test_a_recovered_blip_is_counted_as_recovered_not_failed(self):
        transport = _Transport(
            httpx.ConnectError("reset"), _response(200, {"ok": True})
        )
        client = _client(transport)

        await client.call_tool("take_from_shelf", {"item": "flour"})
        wire = client.wire_health()

        assert wire["world_transport_recovered"] == 1
        assert wire["world_transport_failures"] == 0, (
            "a call that succeeded on retry was booked as a failure"
        )

    @pytest.mark.asyncio
    async def test_a_lost_call_is_counted_as_lost(self):
        client = _client(_Transport(httpx.ConnectError("reset")))

        await client.call_tool("harvest", {"crop": "wheat"})

        assert client.wire_health()["world_transport_failures"] == 1

    @pytest.mark.asyncio
    async def test_a_refusal_is_counted_separately_from_a_blip(self):
        """The distinction the resident could not make, kept in the
        numbers too: a town whose calls are refused has a different
        problem from one whose calls do not arrive."""
        client = _client(_Transport(_response(400, {"error": "crop required"})))

        await client.call_tool("harvest", {})
        wire = client.wire_health()

        assert wire["world_calls_refused"] == 1
        assert wire["world_transport_failures"] == 0

    def test_the_counters_reach_the_health_endpoint(self):
        """The runner already polls `/health` for readiness. Putting the
        counters there means a degrading link is visible WHILE the run is
        going, not after somebody greps the logs."""
        import inspect

        from src.sim import agent_adapter

        source = inspect.getsource(agent_adapter)
        assert "wire_health()" in source, "/health does not read the link counters"
        for field in (
            "world_calls_attempted",
            "world_transport_failures",
            "world_transport_recovered",
        ):
            assert field in source, f"/health does not report {field}"


# ── a read timeout is a third case, and the first two answers were wrong ──
#
# The module docstring above says a transport failure is retried "because an
# `httpx.HTTPError` means no response was received at all and the request
# provably did not land". That holds for a CONNECT failure and not for a READ
# timeout: the request was sent and accepted, and only the answer is missing,
# so the act may already have happened.
#
# election30 is the evidence. Its ~1,800 lost calls logged `ReadTimeout`, and
# every one was re-posted up to twice against a world that may already have
# done the thing, then reported to the resident as "Nothing was done". The
# queue that produced those timeouts is fixed in `app/world/tools/state.py`;
# these are about telling the truth when one happens anyway.


def _read_timeout() -> httpx.ReadTimeout:
    return httpx.ReadTimeout("timed out", request=httpx.Request("POST", "http://world/x"))


@pytest.mark.asyncio
async def test_a_read_timeout_is_never_retried():
    """`POST /tool_calls` is not idempotent, and this one may have landed."""
    transport = _Transport(_read_timeout())

    await _client(transport).call_tool("harvest", {"crop": "wheat"})

    assert transport.calls == 1, (
        "a read timeout was re-posted; the world may already have applied it, "
        "which is how one harvest becomes three"
    )


@pytest.mark.asyncio
async def test_a_read_timeout_does_not_claim_the_call_never_arrived():
    """The resident is told the one true thing: nobody knows."""
    answer = await _client(_Transport(_read_timeout())).call_tool("harvest", {})

    assert "never arrived" not in answer, (
        "told a read timeout meant nothing happened, a resident repeats the "
        "act - and the act may already have been applied"
    )
    assert "MAY have been done" in answer
    assert "harvest" in answer
    assert answer.startswith("Error:"), "still has to read as data, not a crash"


@pytest.mark.asyncio
async def test_a_connect_failure_keeps_the_retry_and_the_old_sentence():
    """The narrowing must not cost TRADE-05 anything it won."""
    transport = _Transport(httpx.ConnectError("no route"))

    answer = await _client(transport).call_tool("move", {})

    assert transport.calls == WORLD_CALL_ATTEMPTS
    assert "never arrived" in answer
    assert "nothing was refused" in answer.lower()


@pytest.mark.asyncio
async def test_the_two_kinds_of_loss_are_counted_apart():
    """WIRE-02's rule: a failure mode with no instrument is invisible.

    A run whose economy figures may be off by a few double-applied acts
    should be able to say so, rather than being quietly wrong - so the
    unknown-outcome calls are countable on their own.
    """
    timed_out = _client(_Transport(_read_timeout()))
    await timed_out.call_tool("harvest", {})

    unreachable = _client(_Transport(httpx.ConnectError("no route")))
    await unreachable.call_tool("harvest", {})

    assert timed_out.transport_unknown == 1
    assert timed_out.transport_failures == 1
    assert unreachable.transport_unknown == 0, (
        "a call that provably never left was counted as one that may have landed"
    )
    assert unreachable.transport_failures == 1
