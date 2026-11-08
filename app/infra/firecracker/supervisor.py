"""The Supervisor contract: what the genesis runner drives, regardless of backend.

DESIGN.md section 9 puts 250 engines on Firecracker microVMs in production
but the genesis-runner track (app/world/run_genesis.py, a sibling track,
not owned here) brings the sim up first on plain supervised OS processes so
launch is not blocked on this track finishing. The instruction in both
cases is identical: "structure the process supervisor so swapping in
microVMs later is a substitution, not a rewrite." This module is that
substitution point.

Neither backend imports the other. The genesis runner imports only this
module's Supervisor protocol plus whichever concrete implementation a
SUPERVISOR_BACKEND config knob selects (process | firecracker); swapping
backends is changing that one string, not touching call sites. Every method
here is async because both backends do I/O to get an answer (a syscall and
a wait() for the process backend, an HTTP call over a unix socket plus a
vsock round trip for this one) - a sync interface would force one backend
to fake blocking or the other to fake async, and either fake is a worse
contract than just being honest that this is all I/O.

UnitSpec is intentionally the same size for both backends: it carries only
what an engine process needs to identify itself and reach the broker
(app/CONVENTIONS.md: "broker owns ALL Bedrock access... agents' herald
base_url points at broker directly"). Nothing Firecracker-specific (vsock
CID, tap device, kernel path) lives on UnitSpec - the firecracker backend
derives all of that itself from unit_id via its own ResourcePool
(resources.py), exactly the way the process backend derives a PID from its
own subprocess.Popen call. If UnitSpec grew backend-specific optional
fields, every future backend would inherit fields it never uses.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class UnitState(str, Enum):
    """Lifecycle states common to a process and a microVM alike.

    Deliberately a subset of what either backend can observe internally -
    a process backend has no "paused" wait status of its own (it would
    have to SIGSTOP to get one) and a microVM backend has no distinct
    "crashed vs exited 0" signal without inspecting its own logs, so both
    map their richer internal states down onto this shared vocabulary
    rather than the protocol growing a union of every backend's states.
    """

    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class UnitSpec:
    """Everything a supervisor needs to bring one engine instance up for one agent.

    unit_id is the stable identity across restarts, snapshots, and backend
    swaps (e.g. "agent-0173") - it is also the S3 snapshot key prefix
    (snapshot_store.py) and the vsock CID seed (resources.py), so the same
    agent always lands on the same resources across a restart instead of
    a fresh allocation racing other units for a CID.
    """

    unit_id: str
    engine_id: str
    cdt_url: str
    cdt_key: str
    extra_env: dict[str, str] = field(default_factory=dict)
    cpu: float = 0.25
    memory_mb: int = 256


@dataclass(frozen=True)
class UnitStatus:
    """A point-in-time read of one unit. Never mutated in place - callers
    hold a status, call a method, get a fresh status back."""

    unit_id: str
    state: UnitState
    backend_handle: str
    started_at: float | None
    last_health_check_at: float | None
    detail: str = ""

    @property
    def is_alive(self) -> bool:
        return self.state in (UnitState.STARTING, UnitState.RUNNING, UnitState.UNHEALTHY)


@dataclass(frozen=True)
class SnapshotHandle:
    """A pointer to durable state for one unit at one tick, not the state itself.

    memfile_uri is None for backends with no separate guest-memory artifact
    (a process backend has nothing analogous to a microVM's memory-file
    snapshot; its "snapshot" is whatever app/world/core/checkpoint.py
    already writes for that agent's in-process state) - present and always
    set for the Firecracker backend, whose snapshot/create endpoint always
    produces both a vmstate file and a memory file.
    """

    unit_id: str
    snapshot_uri: str
    memfile_uri: str | None
    taken_at_tick: int
    taken_at: float = field(default_factory=time.time)


@runtime_checkable
class Supervisor(Protocol):
    """The interface app/world/run_genesis.py programs against.

    A conforming implementation must satisfy every method below for every
    unit_id it has started; app/infra/firecracker/tests/test_supervisor_protocol.py
    runs the same conformance suite against both the fake in-memory backend
    used in this track's own tests and (structurally, via
    isinstance(..., Supervisor) - see runtime_checkable above) the real
    VMManager, so a method signature drifting out of sync fails loudly
    instead of only at genesis-runner import time.
    """

    async def start(self, spec: UnitSpec) -> UnitStatus: ...

    async def stop(self, unit_id: str, timeout_s: float = 10.0) -> UnitStatus: ...

    async def restart(self, unit_id: str) -> UnitStatus: ...

    async def status(self, unit_id: str) -> UnitStatus: ...

    async def list(self) -> list[UnitStatus]: ...

    async def health(self, unit_id: str) -> bool: ...

    async def snapshot(self, unit_id: str, tick: int) -> SnapshotHandle: ...

    async def restore(self, unit_id: str, handle: SnapshotHandle) -> UnitStatus: ...

    async def shutdown_all(self, timeout_s: float = 30.0) -> None: ...


class SupervisorError(Exception):
    """Base class for every error this contract can raise.

    A caller written against Supervisor (the genesis runner) should be able
    to catch this one type regardless of backend; backend-specific error
    detail belongs in the message, not a new exception type per backend,
    or catching errors from the genesis runner would itself become
    backend-aware and defeat the point of the protocol.
    """


class UnknownUnitError(SupervisorError):
    """Raised by status/stop/restart/health/snapshot/restore for a unit_id
    that was never started (or was started and already fully torn down)."""


class UnitAlreadyExistsError(SupervisorError):
    """Raised by start() when unit_id collides with a live or pending unit.

    Restarting an existing unit is restart(), not a second start() - this
    keeps unit_id a reliable idempotency key instead of a hint.
    """

# ---------------------------------------------------------------------------
# B1: the watchdog (The 250 section 33)
# ---------------------------------------------------------------------------
# "The real infrastructure for the second case already exists, partway...
# What's missing is the loop that actually watches those states and calls
# restart() when one goes bad - today, nothing in the codebase calls
# .restart() automatically; a FAILED unit stays failed until someone
# notices."
#
# This is that loop, and deliberately nothing more. It adds no new state to
# the Supervisor protocol and no new method to either backend: it reads
# `list()` and calls the `restart()` that both backends already implement,
# which is why it works unchanged against the process backend today and the
# Firecracker one later.


class RestartPolicy:
    """How hard, and how often, the watchdog is willing to try.

    A watchdog with no policy is worse than no watchdog: a unit that fails
    on startup - a bad config, a missing model profile, a broker that will
    not answer - fails again the moment it comes back, and an unthrottled
    loop turns that into a restart storm that buries the real error in log
    noise and burns the host. So every restart is spaced by an exponential
    backoff, and a unit that has burned through `max_restarts` is given up
    on and reported rather than retried forever.

    `give_up` is not a failure of the watchdog - it is the watchdog
    correctly deciding that this is a person's problem now.
    """

    def __init__(
        self,
        *,
        max_restarts: int = 5,
        initial_backoff_s: float = 2.0,
        max_backoff_s: float = 60.0,
        backoff_factor: float = 2.0,
    ) -> None:
        if max_restarts < 0:
            raise ValueError(f"max_restarts must not be negative, got {max_restarts}")
        if initial_backoff_s <= 0:
            raise ValueError(f"initial_backoff_s must be positive, got {initial_backoff_s}")
        self.max_restarts = max_restarts
        self.initial_backoff_s = initial_backoff_s
        self.max_backoff_s = max_backoff_s
        self.backoff_factor = backoff_factor

    def backoff_for(self, attempt: int) -> float:
        """Seconds to wait before restart number `attempt` (1-based)."""
        delay = self.initial_backoff_s * (self.backoff_factor ** max(0, attempt - 1))
        return min(delay, self.max_backoff_s)


@dataclass
class UnitWatchRecord:
    """What the watchdog remembers about one unit between passes."""

    unit_id: str
    restarts: int = 0
    last_restart_at: float | None = None
    given_up: bool = False
    last_error: str = ""


class SupervisorWatchdog:
    """Restarts engines that have died, without waiting for a person.

    Two conditions count as "gone bad", because section 33 names both and
    they fail differently:

      1. **FAILED** - the unit reported its own death. Unambiguous.
      2. **Gone silent** - the unit still claims to be RUNNING but its
         `last_health_check_at` is older than `silence_timeout_s`. This is
         the one that matters in practice: a wedged engine that never
         crashes looks perfectly healthy to anything that only reads
         `state`, and would otherwise sit there dead-but-green forever.

    UNHEALTHY is deliberately NOT restarted on sight. It is the state a
    backend uses for "failing its health check but still up", which is
    exactly what a unit looks like mid-boot or during a slow broker call;
    restarting on it would kill units that were about to recover on their
    own. An UNHEALTHY unit that stays silent past the timeout is caught by
    condition 2 anyway, which is the correct, slower path for it.
    """

    def __init__(
        self,
        supervisor: "Supervisor",
        *,
        poll_interval_s: float = 5.0,
        silence_timeout_s: float = 90.0,
        policy: RestartPolicy | None = None,
        clock: "Callable[[], float]" = time.time,
        on_event: "Callable[[str, UnitStatus, UnitWatchRecord], None] | None" = None,
    ) -> None:
        self.supervisor = supervisor
        self.poll_interval_s = poll_interval_s
        self.silence_timeout_s = silence_timeout_s
        self.policy = policy or RestartPolicy()
        self.clock = clock
        self.on_event = on_event
        self.records: dict[str, UnitWatchRecord] = {}
        self._task: "asyncio.Task[None] | None" = None
        self._stopping = asyncio.Event()

    # -- the decision ------------------------------------------------------

    def needs_restart(self, status: UnitStatus) -> str | None:
        """Why this unit should be restarted, or None to leave it alone.

        States are compared by VALUE, never by identity. app/world/run_genesis.py
        deliberately declares its own structurally-identical UnitState rather
        than importing this one (see its module docstring), so a UnitStatus
        reaching this method may carry a member of a different enum class with
        the same value. `is` returns False across those two classes - the
        watchdog would have silently never restarted anything on the process
        backend, which is the backend the sim actually boots on today. `==` on
        a str-Enum compares as strings and works across both.
        """
        if status.state == UnitState.FAILED:
            return "reported FAILED"
        if status.state in (UnitState.STOPPED, UnitState.STOPPING, UnitState.PENDING):
            # Stopped on purpose. Restarting a unit someone deliberately
            # stopped would make `stop()` a suggestion rather than a command.
            return None
        if status.last_health_check_at is None:
            # Never health-checked yet: a unit that has only just started.
            # Give it the benefit of the doubt - `started_at` is not a
            # heartbeat and treating it as one would restart every unit
            # during a slow genesis boot.
            return None
        silent_for = self.clock() - status.last_health_check_at
        if silent_for > self.silence_timeout_s:
            return f"silent for {silent_for:.0f}s (>{self.silence_timeout_s:.0f}s)"
        return None

    def _record(self, unit_id: str) -> UnitWatchRecord:
        record = self.records.get(unit_id)
        if record is None:
            record = self.records[unit_id] = UnitWatchRecord(unit_id=unit_id)
        return record

    def _emit(self, kind: str, status: UnitStatus, record: UnitWatchRecord) -> None:
        if self.on_event is not None:
            self.on_event(kind, status, record)

    # -- one pass ----------------------------------------------------------

    async def check_once(self) -> list[str]:
        """Look at every managed unit once. Returns the units restarted.

        Never raises: a watchdog that dies on a backend hiccup is worse
        than no watchdog, because the system then looks supervised and is
        not. Errors are recorded on the unit and reported through
        `on_event`, and the next pass tries again.
        """
        try:
            statuses = await self.supervisor.list()
        except Exception as exc:                     # noqa: BLE001 - see docstring
            logger.warning("watchdog could not list units: %s", exc)
            return []

        restarted: list[str] = []
        now = self.clock()
        for status in statuses:
            reason = self.needs_restart(status)
            record = self._record(status.unit_id)
            if reason is None:
                # A unit that came back and is healthy again has earned a
                # clean slate, or a flaky unit would eventually exhaust its
                # budget over a long run for failures hours apart.
                if record.restarts and status.state == UnitState.RUNNING:
                    record.restarts = 0
                    record.last_error = ""
                continue
            if record.given_up:
                continue
            if record.restarts >= self.policy.max_restarts:
                record.given_up = True
                # Keep the most specific failure already on file. If the
                # restarts themselves were erroring ("no such microVM"),
                # that is the diagnostic an operator needs - overwriting it
                # with the generic "reported FAILED" throws away the only
                # clue as to why this unit could not be brought back.
                record.last_error = record.last_error or reason
                logger.error(
                    "watchdog giving up on %s after %d restarts: %s",
                    status.unit_id, record.restarts, reason,
                )
                self._emit("gave_up", status, record)
                continue
            if record.last_restart_at is not None:
                wait = self.policy.backoff_for(record.restarts)
                if now - record.last_restart_at < wait:
                    continue                          # still backing off
            logger.warning("watchdog restarting %s: %s", status.unit_id, reason)
            self._emit("restarting", status, record)
            try:
                await self.supervisor.restart(status.unit_id)
            except Exception as exc:                  # noqa: BLE001 - see docstring
                record.last_error = f"restart failed: {exc}"
                logger.error("watchdog restart of %s failed: %s", status.unit_id, exc)
                # A failed restart still counts against the budget. It is a
                # failed attempt, and not counting it is how an unrestartable
                # unit becomes an infinite loop.
                record.restarts += 1
                record.last_restart_at = now
                self._emit("restart_failed", status, record)
                continue
            record.restarts += 1
            record.last_restart_at = now
            record.last_error = reason
            restarted.append(status.unit_id)
            self._emit("restarted", status, record)
        return restarted

    # -- the loop ----------------------------------------------------------

    async def run_forever(self) -> None:
        """Poll until stopped. Cancellation-safe."""
        self._stopping.clear()
        while not self._stopping.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval_s)
            except asyncio.TimeoutError:
                continue

    def start(self) -> "asyncio.Task[None]":
        """Start the loop in the background. This is the one line B2 adds to
        the genesis runner."""
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run_forever(), name="supervisor-watchdog")
        return self._task

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=self.poll_interval_s + 5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
