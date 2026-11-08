"""The broker's ledger client, against the world that actually runs.

`INFRA-25`. `HttpLedgerClient`'s contract was pinned only against its own
`httpx.MockTransport` double. Both world hosts implement the shape, and
no test ever started one and drove the real client at it - so a field
rename on either side would have passed every suite in this repo while
breaking every inference call in a live run.

This file lives in `app/tests/` because it imports both `broker.ledger`
and `world.run_genesis`, which app/CONVENTIONS.md's test conventions put
here: "things no single package owns ... live in the top-level
app/tests/".

Second half of the finding: the embedded world answers 409 when its
ledger has HALTED, and the client flattened every non-success into
`LedgerUnavailableError` - a 502 that reads on every dashboard as "the
world is down". It is not down; it stopped itself because its $1000
invariant was violated, and an operator who cannot tell those apart
spends the outage looking in the wrong place.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from broker.errors import LedgerHaltedError, LedgerUnavailableError
from broker.ledger import HttpLedgerClient
from world.admin_api import AdminExtras
from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)


@pytest.fixture(scope="module")
def world():
    """A booted runner and its embedded app - the real routes, the real
    ledger, no transport double anywhere."""

    async def build():
        import tempfile
        from pathlib import Path

        directory = tempfile.mkdtemp()
        runner = GenesisRunner(GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=Path(directory) / "checkpoints",
            world_mode="embedded",
            world_port=0,
        ))
        await runner.boot()
        return runner

    runner = asyncio.run(build())
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service,
        admin_extras=AdminExtras(operator_token="t"),
    )
    return runner, app


def _client(app) -> HttpLedgerClient:
    """The REAL client, pointed at the REAL app over ASGI. ASGITransport
    skips only the TCP layer - the routing, the pydantic validation and
    the ledger itself are all the production ones."""
    return HttpLedgerClient(
        "http://world.test",
        10.0,
        client=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://world.test"
        ),
    )


def _somebody(runner) -> str:
    return next(iter(runner.world_state.agents))


@pytest.mark.asyncio
async def test_the_payer_lookup_agrees_end_to_end(world) -> None:
    """The finding itself: this shape has never been checked against the
    world that serves it."""
    runner, app = world
    who = _somebody(runner)

    payer = await _client(app).get_payer_balance(who)

    assert payer.payer_agent_id, (
        "INFRA-25: the broker asked the real world who pays for this "
        "resident and could not read the answer - a field rename on either "
        "side would have passed every suite in this repo"
    )
    assert isinstance(payer.balance_cents, int)


@pytest.mark.asyncio
async def test_a_debit_moves_real_money(world) -> None:
    runner, app = world
    who = _somebody(runner)
    client = _client(app)

    before = (await client.get_payer_balance(who)).balance_cents
    result = await client.debit(agent_id=who, amount_cents=1, purpose="a test call", metadata={})

    assert result.balance_cents_after == before - 1, (
        "INFRA-25: the broker debited the real ledger and the two disagree "
        "about the balance afterwards"
    )
    assert runner.ledger.usd_balance(result.payer_agent_id) == before - 1


@pytest.mark.asyncio
async def test_an_unknown_resident_is_not_reported_as_an_outage(world) -> None:
    """404 is the caller naming somebody who does not exist. Reporting it
    as "the ledger is unreachable" points the operator at the wrong thing
    - the same confusion this finding's second half is about."""
    _runner, app = world
    with pytest.raises(Exception) as caught:
        await _client(app).get_payer_balance("nobody-by-that-name")
    assert not isinstance(caught.value, LedgerHaltedError)


@pytest.mark.asyncio
async def test_a_halted_ledger_says_it_halted(world) -> None:
    runner, app = world
    who = _somebody(runner)
    runner.ledger.halted = True
    runner.ledger.halt_reason = "the invariant was violated"
    try:
        with pytest.raises(LedgerHaltedError) as caught:
            await _client(app).debit(agent_id=who, amount_cents=1, purpose="x", metadata={})
    finally:
        runner.ledger.halted = False
        runner.ledger.halt_reason = None

    assert caught.value.status_code == 503, (
        "INFRA-25: a halted ledger is reported as 502 - an upstream outage - "
        "so every dashboard says the world is down when it has in fact "
        "refused on purpose"
    )
    assert caught.value.failed_at == "ledger"


def test_the_two_failures_are_different_types() -> None:
    """Same hop, different thing to do about it. A single type forces the
    operator to read the message to find out which."""
    assert LedgerHaltedError is not LedgerUnavailableError
    assert LedgerHaltedError.status_code != LedgerUnavailableError.status_code
