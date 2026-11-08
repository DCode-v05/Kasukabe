"""ENG-20: boot wrote a compaction threshold nothing reads.

From `docs/review/10-engine-seam-and-mind.md`: "`pinned_boot.py:297-336`
acknowledged dead (`needs_compaction` reads the module constant). Remove;
it misleads readers into thinking the threshold is 0.12."
`docs/review/VERIFICATION-2026-09-05.md:341` marked the finding still open
because only an explanatory NOTE had been added: the writer was still
defined and still called at boot.

What made it dead. `CompactionConfig.load` really does read
`compaction_threshold` out of SQLite
(`compaction_orchestrator.py:133-134`) and really does store it on the
config object - and then nothing anywhere asks the config object for it.
The function that decides whether to compact is
`context_engineering.needs_compaction(used_tokens, context_window)`
(`:1016-1021`), which has no parameter a persisted value could arrive
through and compares against the module constant `COMPACTION_THRESHOLD =
0.80`. So the row said 0.12 and every compaction in all 23 runs fired on
0.80, and a reader of the seam who wanted to know what bounded a
resident's prompt was pointed at the wrong number.

The seam also wrote the row BEFORE `run_migrations`, so it created the
table itself and migration 009's `INSERT OR IGNORE` then left the 0.12 in
place. That is what the wiring test at the bottom reads back: with the
writer gone, an agent's own database carries the engine's seeded default,
because the seam no longer has an opinion about a knob it cannot use.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import httpx
import pytest

SEAM = Path(__file__).resolve().parents[1] / "src" / "sim"


def test_the_persisted_threshold_has_nowhere_to_enter_the_decision():
    """The evidence for the removal, so nobody has to re-derive it: the
    function that decides has no parameter the stored value could reach."""
    from src.engine_core.context_engineering import COMPACTION_THRESHOLD, needs_compaction

    params = list(inspect.signature(needs_compaction).parameters)
    assert params == ["used_tokens", "context_window"], (
        "needs_compaction grew a way to be configured - if a persisted "
        "threshold can now reach it, this finding needs re-reading"
    )
    assert COMPACTION_THRESHOLD == 0.80

    # A 12,500-token window half full is far past 0.12 and nowhere near
    # 0.80, and the engine does not compact: the stored knob is not
    # consulted, which is the whole finding.
    window = 12_500
    used = window // 2
    assert used >= window * 0.12
    assert needs_compaction(used, window) is False


def test_the_boot_no_longer_defines_a_writer_for_it():
    """Read as text on purpose. A function can be deleted from the call
    site and left defined, which is what the verification pass found the
    first time this was reported fixed."""
    source = (SEAM / "pinned_boot.py").read_text(encoding="utf-8")
    assert "_tighten_compaction" not in source, (
        "ENG-20: the writer for the dead knob is back in the seam"
    )


def world_client(agent_id: str):
    from src.sim.world_tools import WorldClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": 0})
        raise AssertionError(f"unexpected world call: {request.url.path}")

    return WorldClient(
        "http://world.test",
        agent_id=agent_id,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_a_booted_agents_database_carries_the_engines_own_default(
    tmp_path, monkeypatch
):
    """THE WIRING TEST. The text check above passes the moment the function
    is deleted, whether or not boot still writes the row by some other
    route. This boots a real agent through `boot_sim_engine` - the one
    entry point - and reads the row back out of that agent's own SQLite.

    0.80 is migration 009's seed. Anything else means the seam formed an
    opinion about a knob that cannot be acted on.
    """
    from src.engine_core.compaction_orchestrator import CompactionConfig
    from src.sim.config import SimProfile
    from src.sim.pinned_boot import boot_sim_engine
    from tests.sim_fake_citadel import FakeBroker, install_fake_broker

    monkeypatch.setenv("FORK_SUBAGENT", "false")
    install_fake_broker(monkeypatch, FakeBroker())

    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(
        profile, "http://world.test", world_client=world_client("agent-boot")
    )
    try:
        config = await CompactionConfig.load(engine.db)
        assert config.compaction_threshold == CompactionConfig().compaction_threshold, (
            "ENG-20: boot still writes a compaction threshold of its own. "
            "Nothing reads it, and it tells the next reader of this file "
            "that a resident's prompt is bounded by a number that has "
            "never bounded anything."
        )
    finally:
        await engine.shutdown()
