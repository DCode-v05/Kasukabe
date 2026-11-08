"""One process, one namespace per agent. No container, no sandbox, no Docker.

**The measurement this exists for** (8 GB M1, 2026-08-26):

    separate OS process per engine ....  ~110 MB each  -> 50 agents = 5.5 GB
    one process, namespaced per agent .    2.5 MB each -> 50 agents = 182 MB

A 44x difference, and it is the difference between a 50-agent run and a laptop
that stops responding. The cost of a second engine in a fresh process is
mostly the Python interpreter and the engine's own imports - roughly 56 MB of
it - and every process pays that again. Share the process and it is paid once.

**The namespace is `SEPTEMBER_HOME`, and nothing here invents it.**
`engine/src/infra/september_home.py` already declares one directory per engine
holding "the database, config, credentials, skills, plugins, workspaces,
sidechains, tool results, caches, logs. Nothing the engine writes lives outside
it." `ProcessSupervisor` already sets it to `<home_root>/<agent_id>`. That is a
complete isolation boundary for everything an engine owns, so this backend
keeps exactly that and drops only the process around it.

**What isolation is kept, and what is given up.** Kept: every byte an engine
writes stays in its own directory, agents cannot read each other's SQLite,
memory, or skills, and a corrupted home affects one agent. Given up: an engine
that raises an unhandled exception is no longer contained by a process
boundary. That is the trade, stated plainly - and it is the right one here,
because the alternative on this hardware is not "50 isolated agents", it is
"no run at all".

Not Docker, not Firecracker, not bubblewrap: an OS process and a directory per
agent. Both backends remain for machines that have the memory.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _Namespace:
    """One agent's slot: its home directory, its engine, its task."""

    unit_id: str
    agent_id: str
    home: Path
    engine: Any = None
    started_at: float | None = None
    failures: int = 0
    last_error: str = ""


class NamespaceSupervisor:
    """A `Supervisor` that boots engines as coroutines in this process.

    Satisfies the same protocol as ProcessSupervisor - start/stop/restart/
    status/list/health/shutdown_all - so `GenesisRunner` cannot tell the
    difference, and the watchdog keeps working unchanged.
    """

    def __init__(
        self,
        home_root: str,
        world_base_url: str,
        *,
        boot: Any = None,
        world_client_factory: Any = None,
        engine_repo: str | None = None,
    ) -> None:
        self.home_root = Path(home_root).expanduser()
        self.world_base_url = world_base_url
        self.engine_repo = engine_repo
        self.units: dict[str, _Namespace] = {}
        self._boot = boot
        self._world_client_factory = world_client_factory
        self._lock = asyncio.Lock()


    def home_for(self, agent_id: str) -> Path:
        """`<home_root>/<agent_id>` - the same path ProcessSupervisor exports
        as SEPTEMBER_HOME. Identical layout means a run can be moved between
        backends without touching a single file on disk."""
        return self.home_root / agent_id

    def _prepare_namespace(self, agent_id: str) -> Path:
        home = self.home_for(agent_id)
        home.mkdir(parents=True, exist_ok=True)
        return home

    def _ensure_engine_importable(self) -> None:
        """Put the engine repo on `sys.path` so `src.*` resolves in-process.

        ProcessSupervisor never needed this: it spawned each engine with
        `cwd=engine_repo`, so Python found `src` on its own. Sharing one
        process means sharing one `sys.path`, and the first run without this
        failed all 50 boots with `ModuleNotFoundError: No module named 'src'`
        - correctly recorded as 50 failed units rather than crashing the run,
        which is the isolation contract working, but a run of nothing.
        """
        import sys

        if self.engine_repo is None:
            return
        repo = str(Path(self.engine_repo).expanduser().resolve())
        if repo not in sys.path:
            sys.path.insert(0, repo)


    async def start(self, spec: Any) -> Any:
        from world.run_genesis import UnitAlreadyExistsError, UnitState, UnitStatus

        async with self._lock:
            if spec.unit_id in self.units:
                raise UnitAlreadyExistsError(spec.unit_id)
            agent_id = spec.extra_env.get("AGENT_ID") or spec.engine_id
            ns = _Namespace(
                unit_id=spec.unit_id, agent_id=agent_id, home=self._prepare_namespace(agent_id)
            )
            self.units[spec.unit_id] = ns

        try:
            ns.engine = await self._boot_engine(ns, spec)
            ns.started_at = time.time()
            state = UnitState.RUNNING
            detail = f"namespace {ns.home}"
        except Exception as exc:  # noqa: BLE001 - one agent must not take the run down
            ns.failures += 1
            ns.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("namespace %s failed to boot: %s", ns.agent_id, ns.last_error)
            state = UnitState.FAILED
            detail = ns.last_error

        return UnitStatus(
            unit_id=spec.unit_id,
            state=state,
            backend_handle=str(ns.home),
            started_at=ns.started_at,
            last_health_check_at=time.time(),
            detail=detail,
        )

    async def _boot_engine(self, ns: _Namespace, spec: Any) -> Any:
        if self._boot is None:
            self._ensure_engine_importable()
            from src.sim.config import SimProfile
            from src.sim.pinned_boot import boot_sim_engine

            profile = SimProfile(
                agent_id=ns.agent_id,
                broker_base_url=spec.extra_env.get("BROKER_BASE_URL", ""),
                home_root=str(self.home_root),
            )
            client = self._world_client_factory(ns.agent_id) if self._world_client_factory else None
            return await boot_sim_engine(profile, self.world_base_url, world_client=client)
        return await self._boot(ns, spec)

    async def stop(self, unit_id: str, timeout_s: float = 10.0) -> Any:
        from world.run_genesis import UnitState, UnitStatus, UnknownUnitError

        ns = self.units.get(unit_id)
        if ns is None:
            raise UnknownUnitError(unit_id)
        if ns.engine is not None:
            try:
                await asyncio.wait_for(ns.engine.shutdown(), timeout=timeout_s)
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("namespace %s did not shut down within %ss", ns.agent_id, timeout_s)
            except Exception as exc:  # noqa: BLE001
                logger.warning("namespace %s shutdown raised: %s", ns.agent_id, exc)
            ns.engine = None
        ns.started_at = None
        return UnitStatus(
            unit_id=unit_id,
            state=UnitState.STOPPED,
            backend_handle=str(ns.home),
            started_at=None,
            last_health_check_at=time.time(),
            detail="stopped",
        )

    async def restart(self, unit_id: str) -> Any:
        from world.run_genesis import UnitSpec, UnknownUnitError

        ns = self.units.get(unit_id)
        if ns is None:
            raise UnknownUnitError(unit_id)
        await self.stop(unit_id)
        async with self._lock:
            self.units.pop(unit_id, None)
        return await self.start(
            UnitSpec(
                unit_id=unit_id,
                engine_id=ns.agent_id,
                cdt_url=self.world_base_url,
                cdt_key="",
                extra_env={"AGENT_ID": ns.agent_id},
            )
        )

    async def status(self, unit_id: str) -> Any:
        from world.run_genesis import UnitState, UnitStatus, UnknownUnitError

        ns = self.units.get(unit_id)
        if ns is None:
            raise UnknownUnitError(unit_id)
        alive = ns.engine is not None
        return UnitStatus(
            unit_id=unit_id,
            state=UnitState.RUNNING if alive else UnitState.STOPPED,
            backend_handle=str(ns.home),
            started_at=ns.started_at,
            last_health_check_at=time.time(),
            detail=ns.last_error if not alive else f"namespace {ns.home}",
        )

    async def list(self) -> list[Any]:
        return [await self.status(uid) for uid in list(self.units)]

    async def health(self, unit_id: str) -> bool:
        ns = self.units.get(unit_id)
        return ns is not None and ns.engine is not None

    async def shutdown_all(self, timeout_s: float = 30.0) -> None:
        await asyncio.gather(
            *(self.stop(uid, timeout_s=min(timeout_s, 10.0)) for uid in list(self.units)),
            return_exceptions=True,
        )


    async def send_tick(self, unit_id: str, perception_text: str) -> str:
        """Straight into the engine, no HTTP.

        ProcessSupervisor reaches its engines over a port per agent; here the
        engine object is in hand. That removes 50 listening sockets and a
        round trip per agent per tick as a side effect of the memory work.
        """
        ns = self.units.get(unit_id)
        if ns is None or ns.engine is None:
            raise RuntimeError(f"namespace {unit_id} is not running")
        return await ns.engine.send_tick(perception_text)
