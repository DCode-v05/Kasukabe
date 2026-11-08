"""The memory guard must not contradict itself, and preflight must not
pass a run the runner is about to refuse.

Both failures below are real and both happened on the same launch, one
second apart, on a 20-engine run:

    preflight:  [✓] Memory fits the engine count
                    1380 MB needed, 1883 MB free · 69 MB/engine
    runner:     Not enough free memory to start this run.
                20 engines need about 1380 MB, but only 1881 MB is free
                after reserving 512 MB ...

Read either message on its own and the run fits: 1380 is less than 1881.
The decision is made on `free - RESERVED_SYSTEM_MB` (1369), which is the
one number neither message prints. So the guard refused for a true
reason and explained it with figures that argue the opposite, and the
checklist above it reported PASS on the same verdict it had just
computed as `fits = False`.

A check that fails loudly with numbers that say "this should have
worked" costs more than no check, because the next person raises the
wrong lever.
"""

from __future__ import annotations

import re

import pytest

from world import memory_budget
from world.memory_budget import RESERVED_SYSTEM_MB, MemoryVerdict, check


def _numbers(text: str) -> tuple[int, int]:
    """(needed, stated-available) as the operator reads them off the refusal."""
    needed = int(re.search(r"need about ([\d,]+) MB", text).group(1).replace(",", ""))
    stated = int(re.search(r"only ([\d,]+) MB", text).group(1).replace(",", ""))
    return needed, stated


@pytest.mark.parametrize("free_mb", [1881, 2000, 1400, 900, 600])
def test_a_refusal_never_prints_numbers_that_argue_it_should_have_worked(
    free_mb: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_budget, "available_mb", lambda: free_mb)
    verdict = check(20, backend="process", mb_per_engine=69)
    if verdict.fits:
        pytest.skip(f"{free_mb} MB fits 20 engines; nothing is refused")

    needed, stated = _numbers(verdict.explain())
    assert needed > stated, (
        "the refusal says "
        f"{needed} MB needed against {stated} MB available, which reads as a fit. "
        f"The decision was made against {free_mb - RESERVED_SYSTEM_MB} MB usable."
    )


@pytest.mark.parametrize("free_mb", [1881, 1400, 900])
def test_the_refusal_states_the_number_the_decision_used(
    free_mb: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever the wording, the usable figure has to appear, because that
    is the quantity the operator has to move."""
    monkeypatch.setattr(memory_budget, "available_mb", lambda: free_mb)
    verdict = check(20, backend="process", mb_per_engine=69)
    if verdict.fits:
        pytest.skip("fits")
    assert str(free_mb - RESERVED_SYSTEM_MB) in verdict.explain()


def test_the_affordable_count_is_one_the_guard_would_actually_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal tells you to re-run at `affordable` engines. If that
    number is itself refused, the operator is sent round the loop again."""
    for free_mb in (1881, 1500, 1200, 800):
        monkeypatch.setattr(memory_budget, "available_mb", lambda f=free_mb: f)
        verdict = check(20, backend="process", mb_per_engine=69)
        if verdict.fits:
            continue
        retry = check(verdict.affordable, backend="process", mb_per_engine=69)
        assert retry.fits, (
            f"at {free_mb} MB free the guard suggests {verdict.affordable} engines "
            "and would refuse that too"
        )


def test_verdict_exposes_the_usable_figure() -> None:
    verdict = MemoryVerdict(requested=1, affordable=1, available_mb=2000, needed_mb=69)
    assert verdict.usable_mb == 2000 - RESERVED_SYSTEM_MB


def test_preflight_does_not_pass_a_run_the_runner_will_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The escape hatch (`free > MIN_FREE_MEMORY_MB`) is what turned a
    non-fitting verdict into a green tick. It may downgrade a blocking
    FAIL to a WARN - it must not report PASS."""
    from world import preflight as pf

    # 1881 MB free, 69 MB/engine, 20 engines: 1380 needed against 1369
    # usable. These are the figures from the launch this test is named for.
    monkeypatch.setattr(memory_budget, "available_mb", lambda: 1881)
    monkeypatch.setattr(memory_budget, "measured_mb_per_engine", lambda *a, **k: (4, 69))

    report = pf.run(
        broker_url="http://127.0.0.1:59999",  # down; unrelated checks may fail
        expected_agents=20,
        genesis_path="world/genesis_20.json",
        home_root="/tmp/kasukabe-agents",
        trace_db="data/kasukabe.db",
        engine_repo="../engine",
        ports_must_be_free=False,
    )
    memory = next(c for c in report.checks if "Memory" in c.name)
    verdict = check(20, backend="process", mb_per_engine=69)
    assert not verdict.fits, "test needs a non-fitting verdict to be meaningful"
    assert memory.status != "PASS", (
        f"preflight reported {memory.status} on a verdict the runner refuses: "
        f"{memory.detail}"
    )
