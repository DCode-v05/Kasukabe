"""A real town, booted, with the stories driving it instead of a model.

WHAT IS REAL HERE. All of it except the choosing. `GenesisRunner.boot()`
seeds the genesis population, constructs the real `Ledger`, the real
`ToolRegistry` with all ninety-seven verbs, the real charter engine and
the real trace. Acts go through `registry.execute()` - the one call path,
under its lock, with the snapshot/restore contract and the charge that
happens before the handler.

The only thing a story supplies is the decision. That is the honest split:
a story asserts what the TOWN does when a verb is called, and the agility
suite asserts what the ENGINE does when a model calls oddly. Neither
pretends to be the other.

`mock_mode=True` here means the *agent gateway* is mocked - no engine
subprocesses. It does NOT mock the ledger or the registry, which is the
whole point. Compare `BrokerSettings.mock_mode`, which does swap the
ledger for an in-memory one; that is the trap `proof` refuses.

THE INVARIANT IS CHECKED AFTER EVERY ACT, not at the end. A run that
balances at the end can still have been wrong in the middle, and the
middle is where a refund, a rollback or a double-charge would show. The
ledger's own `check_invariant()` is what decides - this module does not
re-implement the arithmetic it is checking.
"""
from __future__ import annotations

import contextlib
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Act:
    """One thing a resident did, and what the town said back."""

    actor: str
    tool: str
    params: dict
    ok: bool
    detail: str

    def __str__(self) -> str:
        return f"{self.actor} {self.tool}({self.params}) -> " + (
            "ok" if self.ok else f"refused: {self.detail}"
        )


@dataclass
class Town:
    runner: Any
    acts: list[Act] = field(default_factory=list)

    @property
    def world(self) -> Any:
        return self.runner.world_state

    @property
    def registry(self) -> Any:
        return self.runner.registry

    @property
    def ledger(self) -> Any:
        return self.runner.ledger

    # -- doing things -----------------------------------------------------

    async def do(self, actor: str, tool: str, **params: Any) -> Act:
        """Call one verb the way a resident's turn calls it.

        The ledger invariant is checked immediately afterwards, so the act
        that broke it is the act named in the failure rather than whatever
        happened to run last.
        """
        try:
            result = await self.registry.execute(self.world, actor, tool, params)
            ok = bool(getattr(result, "success", True))
            detail = str(
                getattr(result, "error", "") or getattr(result, "message", "") or ""
            )
        except Exception as exc:  # noqa: BLE001 - a raise is an outcome
            ok, detail = False, f"{type(exc).__name__}: {exc}"

        act = Act(actor=actor, tool=tool, params=params, ok=ok, detail=detail)
        self.acts.append(act)
        self.ledger.check_invariant()
        return act

    # -- asking questions -------------------------------------------------

    def refusals_for(self, actor: str) -> list[Any]:
        return [r for r in self.world.tool_refusals if r.agent_id == actor]

    def last_refusal(self, actor: str) -> Any | None:
        rows = self.refusals_for(actor)
        return rows[-1] if rows else None

    def usd_total(self) -> int:
        return self.ledger.usd_total()

    def sees(self, actor: str, tool: str) -> bool:
        """Is this verb visible to this resident right now?

        The GOV-3 distinction: a verb the resident cannot see and a verb
        the resident did not choose are different findings, and a story
        that confuses them proves nothing.
        """
        spec = self.registry.tools.get(tool)
        if spec is None:
            return False
        return bool(self.registry.visible_to(spec, self.world, actor))

    def residents(self, n: int = 2) -> list[str]:
        return sorted(self.world.agents)[:n]


@contextlib.asynccontextmanager
async def town(**overrides: Any) -> AsyncIterator[Town]:
    """Boot a real town for the duration of one story."""
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    tmp = Path(tempfile.mkdtemp(prefix="proof-town-"))
    kwargs: dict[str, Any] = dict(
        mock_mode=True,  # the agent gateway only - see the module docstring
        checkpoint_dir=tmp / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    )
    kwargs.update(overrides)
    runner = GenesisRunner(GenesisRunnerConfig(**kwargs))
    await runner.boot(resume=False)
    built = Town(runner=runner)
    built.ledger.check_invariant()  # the town must start sound
    try:
        yield built
    finally:
        with contextlib.suppress(Exception):
            await _shutdown_without_the_final_reading(runner)


async def _shutdown_without_the_final_reading(runner: Any) -> None:
    """`runner.shutdown()`, minus the two things only a paid run needs.

    MEASURED, because the first guess was wrong. Booting a 314-resident
    town costs 0.4s; tearing one down cost 23.1s, and the stories were
    paying it fifteen times - 347 of the suite's 487 seconds. The guess
    before measuring was "each story boots a town", and pooling the boot
    would have saved almost nothing.

    Where it actually goes:

        20.6s  capture_final_reading
         2.4s  archive_engine_homes
         0.0s  collect_wire_health

    `capture_final_reading` asks the world everything a report will want
    while it can still answer - eight routes including `/agents` for every
    resident and `/world/utterances?limit=5000` - over an in-process ASGI
    transport. It is not slow because anything is broken; it is eight
    serialisations of a large world, and it exists because the 50-agent run
    ended with a report that said "The world reported no roster" about
    fifty residents. For a paid run that is cheap insurance bought once.

    A story needs none of it. It holds the world object, asserts against
    the live ledger and charter, and reads its evidence from the trace on
    disk. Archiving each resident's `brain.sqlite` is likewise for a run
    somebody will resume; these towns are thrown away.

    Everything else still runs exactly as production runs it - the
    watchdog and groundskeeper stop, the learning drain, the supervisor,
    the gateway close, uvicorn's graceful exit, the final checkpoint - so
    this stays a real shutdown with two expensive, run-only steps lifted
    out rather than a teardown of its own that would drift from the real
    one.
    """
    from world import run_genesis as rg

    async def no_final_reading(*_a: Any, **_k: Any) -> None:
        return None

    def no_archive(*_a: Any, **_k: Any) -> None:
        return None

    reading, archive = rg.capture_final_reading, rg.archive_engine_homes
    rg.capture_final_reading, rg.archive_engine_homes = no_final_reading, no_archive
    try:
        await runner.shutdown()
    finally:
        rg.capture_final_reading, rg.archive_engine_homes = reading, archive
