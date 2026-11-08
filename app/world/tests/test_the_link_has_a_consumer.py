"""The engine→world link is measured AND something reads the measurement.

`WIRE-02`, both halves. The first half put counters on each engine's
`WorldClient` and served them on that engine's `GET /health`. Then
nothing read them — the runner polls that endpoint once, at startup, and
looks only at the status code.

**An instrument with no consumer is ENG-16**: a fix that reaches nobody
is indistinguishable from no fix, and this project has shipped that
mistake before. It was caught here by being asked whether the gap was
actually closed, which it was not.

Run `deni-a6f9eb4-5h` lost 97 calls — 8% of every tool call the town
made — and the loss was discoverable only by grepping session logs after
the run had ended. Three consumers now exist, and each answers a
different question:

* `GET /world/link` — *is it happening right now?* 97 lost calls would
  have been worth knowing at tick 40, not at the end;
* the shutdown line — *did it happen at all?* At or above 1% it is an
  ERROR, because a lost call is indistinguishable from a refusal to the
  resident that made it, so the run's behaviour is contaminated rather
  than merely slow;
* `runner.wire_health` — *what were the numbers?* Kept so the report and
  the manifest can read them without asking the engines again, which
  they cannot do after teardown.
"""
from __future__ import annotations

import logging

import pytest
import pytest_asyncio

from world.run_genesis import (
    WIRE_LOSS_ALARM,
    GenesisRunner,
    GenesisRunnerConfig,
)
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


@pytest_asyncio.fixture
async def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp",
        world_mode="embedded", world_port=0,
    ))
    await runner.boot(resume=False)
    return runner


@pytest.mark.asyncio
async def test_the_runner_can_collect_the_link_counters(runner):
    """The collector exists and answers a stable shape even with no
    engines to ask - mock mode has none, and a shutdown must not depend
    on telemetry succeeding."""
    totals = await runner.collect_wire_health()

    for key in (
        "world_calls_attempted", "world_calls_refused",
        "world_transport_failures", "world_transport_recovered",
        "engines_answering",
    ):
        assert key in totals, f"{key} missing from the link report"
        assert isinstance(totals[key], int)


@pytest.mark.asyncio
async def test_a_lossy_link_is_reported_at_error_level(runner, caplog):
    """8% loss - what the run actually had - must be loud.

    A slow tick is a performance problem. A lost call is a resident
    reasoning from "the world would not let me" about an act the world
    never heard of, which contaminates the behaviour the run exists to
    measure. Those deserve different volumes.
    """
    with caplog.at_level(logging.WARNING):
        runner.report_wire_health({
            "world_calls_attempted": 1224,
            "world_calls_refused": 215,
            "world_transport_failures": 97,
            "world_transport_recovered": 0,
            "engines_answering": 50,
        })

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an 8% loss rate was not reported as an error"
    said = errors[0].getMessage()
    assert "97" in said and "1224" in said
    assert "ABOVE THE ALARM LINE" in said


@pytest.mark.asyncio
async def test_a_healthy_link_is_not_shouted_about(runner, caplog):
    """A run that lost nothing must not produce an alarm, or the alarm
    stops being read."""
    with caplog.at_level(logging.WARNING):
        runner.report_wire_health({
            "world_calls_attempted": 1224,
            "world_calls_refused": 12,
            "world_transport_failures": 0,
            "world_transport_recovered": 0,
            "engines_answering": 50,
        })

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_a_link_carried_by_retries_is_still_worth_saying(runner, caplog):
    """`recovered` separates "the link is fine" from "the link is bad and
    the retry is carrying us". The second is a warning even though
    nothing was lost, because it will not carry us forever."""
    with caplog.at_level(logging.WARNING):
        runner.report_wire_health({
            "world_calls_attempted": 1000,
            "world_calls_refused": 0,
            "world_transport_failures": 0,
            "world_transport_recovered": 90,
            "engines_answering": 50,
        })

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a link surviving only on retries said nothing"
    assert "90 recovered" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_the_numbers_are_kept_where_a_report_can_read_them(runner):
    """Collected at shutdown, before teardown - a stopped engine cannot
    answer, and this is the one number a run cannot reconstruct
    afterwards from anything it wrote down."""
    assert hasattr(runner, "wire_health")
    await runner.shutdown()
    assert isinstance(runner.wire_health, dict)


def test_shutdown_collects_before_it_tears_down():
    """Ordering, not taste. `collect_wire_health` asks each engine over
    HTTP; after `shutdown_all` there is nobody to ask."""
    import inspect

    source = inspect.getsource(GenesisRunner.shutdown)
    collected = source.index("collect_wire_health")
    torn_down = source.index("shutdown_all")
    assert collected < torn_down, (
        "the link is measured after the engines are stopped, so it will "
        "always read zero"
    )


def test_the_alarm_line_is_a_named_number():
    """Not a literal buried in a comparison: an operator changing what
    counts as a lossy run should find one place."""
    assert 0 < WIRE_LOSS_ALARM < 0.5


@pytest.mark.asyncio
async def test_the_route_exists_and_says_whether_it_is_above_the_alarm(runner):
    """`GET /world/link`. The shutdown line answers "did it happen"; this
    answers "is it happening", which is what 97 lost calls needed at tick
    40 rather than at the end.

    This test USED to assert `'@app.get("/world/link")' in source`. A
    route's source text is not a route: the handler closed over a bare
    `runner` name that exists in no scope in the app factory, so every
    real request raised NameError and returned 500 - and this test passed
    the whole time, because the string was right there in the file. Run 25
    found it by curling the route on a live town.

    So: a real request, through the real app, asserting on the body.
    """
    from httpx import ASGITransport, AsyncClient

    await runner.start_embedded_world_server()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=runner.world_app), base_url="http://world"
        ) as client:
            response = await client.get("/world/link")
    finally:
        await runner.shutdown()

    assert response.status_code == 200, response.text
    body = response.json()
    assert "lost_share" in body
    assert "above_alarm" in body
    assert body["above_alarm"] is False
