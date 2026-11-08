"""250 engines have to be asked at once, not one after another.

`CORE-33`. `wait_for_engines_ready` looped over every pending agent in
sequence with a 2-second client timeout on each. At rung 3 - 250 residents,
the scale the roadmap is aiming at - one pass over a set of booting engines
costs up to 250 x 2 s = over eight minutes, which is longer than the
readiness deadline itself. So the loop could not complete a single pass
before giving up, and every engine would be declared absent no matter how
healthy it was.

It never bit because no run has gone past twenty residents.
`preflight.py` already parallelises its own port scan for exactly this
reason.

Timing is a poor thing to assert on, so this measures the SHAPE instead:
how many probes are in flight at once.
"""
from __future__ import annotations

import asyncio

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest.mark.asyncio
async def test_the_probes_overlap(tmp_path, monkeypatch):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=False,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
        engine_ready_timeout_s=5.0,
    ))
    agents = [f"n{i:02d}" for i in range(1, 21)]
    runner.ports_by_agent = {a: 9100 + i for i, a in enumerate(agents)}

    in_flight = 0
    peak = 0

    class FakeResponse:
        status_code = 200

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.01)
                return FakeResponse()
            finally:
                in_flight -= 1

    monkeypatch.setattr(
        "world.run_genesis.httpx.AsyncClient", lambda *a, **k: FakeClient()
    )

    await runner.wait_for_engines_ready(agents)

    assert peak > 1, (
        "CORE-33: the engines were probed one at a time, so 250 of them "
        "cannot be checked inside any sensible readiness deadline"
    )
    assert peak == len(agents), (
        f"only {peak} of {len(agents)} probes overlapped"
    )
