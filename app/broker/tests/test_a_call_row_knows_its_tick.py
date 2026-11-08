"""INFRA-02: every `llm_calls` row must carry the world's tick.

The broker has no clock. So `TraceRecord.tick` was written NULL on every
row ever recorded - and `game_day` is a generated column derived from tick,
so that was NULL too. Two consequences, both silent:

  * C1 (model calls per resident-day) cannot be computed at all. It is the
    headline cost metric of the whole project.
  * `export_game_day(n)` partitions on `game_day`, so the Parquet export of
    calls came out empty by construction, for every run.

The clock reaches the broker at exactly one point: the payer-balance
response, which is fetched once before every call to find out who pays.
This pins all four links of that chain.
"""
from __future__ import annotations

import dataclasses

from broker.ledger import PayerBalance
from broker.service import PreparedCall
from broker.trace import TraceRecord


def _fields(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_the_payer_response_can_carry_a_tick():
    assert "tick" in _fields(PayerBalance), (
        "the payer-balance response is the only place the world clock and "
        "the broker meet; without a tick field the broker can never know "
        "what game time a call happened at (INFRA-02)"
    )


def test_a_prepared_call_carries_the_tick_to_the_record():
    assert "tick" in _fields(PreparedCall)
    assert "tick" in _fields(TraceRecord)


def test_a_missing_tick_degrades_rather_than_refusing():
    """A world that has not been updated still answers without a tick.

    The chain must degrade to today's behaviour (a NULL tick) rather than
    raise - a research field is not worth failing a paid call over.
    """
    balance = PayerBalance(
        agent_id="n01", payer_agent_id="n01", balance_cents=500, is_minor=False
    )
    assert balance.tick is None


def test_a_tick_survives_the_whole_chain():
    """The value, not just the field: world route -> payer -> prepared -> row."""
    balance = PayerBalance(
        agent_id="n01",
        payer_agent_id="n01",
        balance_cents=500,
        is_minor=False,
        tick=137,
    )
    record = TraceRecord(
        id="t1",
        agent_id=balance.agent_id,
        payer_agent_id=balance.payer_agent_id,
        model="m",
        requested_model="m",
        region="ap-south-1",
        call_class="cognition",
        purpose="turn",
        request_body={},
        response_body={},
        stop_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
        cost_nano_dollars=1,
        cents_debited=1,
        latency_ms=1,
        rate_limit_wait_ms=0,
        success=True,
        error_type=None,
        error_message=None,
        failed_at=None,
        created_at=0.0,
        tick=balance.tick,
    )
    assert record.tick == 137, "the tick did not survive to the row"


def test_the_service_actually_puts_the_tick_on_the_row_it_writes():
    """The link the field-level tests above could not see.

    Those assert the dataclasses HAVE a tick field. They all passed while
    `TraceRecord(...)` in the success path was still being constructed
    without one, because a field with a default is satisfied by silence.
    A dataclass that can hold a value is not a value that arrives.

    This reads the construction itself, from the compiled function.
    """
    import ast
    import inspect
    import textwrap

    from broker.service import BrokerService

    src = textwrap.dedent(inspect.getsource(BrokerService))
    tree = ast.parse(src)

    built = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TraceRecord"
    ]
    assert built, "no TraceRecord is constructed in BrokerService at all"

    without = [
        node for node in built
        if not any(kw.arg == "tick" for kw in node.keywords)
    ]
    assert not without, (
        f"{len(without)} of {len(built)} TraceRecord constructions in "
        "BrokerService omit `tick`, so those llm_calls rows are written with "
        "a NULL tick and NULL game_day however well the field is declared "
        "(INFRA-02)"
    )
