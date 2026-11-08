"""Start and stop a real Bedrock run from the map's Real sim button.

WHY THIS LIVES IN game/. app/CONVENTIONS.md says "game/ is presentation
only ... it never computes game logic locally". Nothing here computes game
logic: it writes a genesis file, spawns world/run_genesis.py, and kills it
again. That is operator process control - the same category as the
President/God console actions game/console/ already owns - and every rule
about how the town behaves still lives in world/. The alternative was a
fourth service whose only job is `subprocess.Popen`.

WHAT "FRESH" MEANS. Every start is a new town, not a resumed one:
  - a genesis file is regenerated for the requested cast size and purse;
  - --no-resume, so the runner ignores any checkpoint;
  - the checkpoint directory and every agent's home (persona.txt and
    brain.sqlite) are deleted first.
Without that last step a second run inherits the first run's memories, and
"who survives" is measured against a cast that has already lived a life.

SAFETY. Starting spends real money, so:
  - only one run at a time; a second start is refused, not queued;
  - the per-agent purse is capped by world/'s own $1000 invariant, which
    make_genesis_subset.repair() enforces and reports in cents;
  - a budget cap in cents is passed to the runner on every start.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("game.realsim")

APP_ROOT = Path(__file__).resolve().parent.parent

MIN_AGENTS = 2
MAX_AGENTS = 250
DEFAULT_AGENTS = 50

DEFAULT_WALLET_CENTS = 200
TOTAL_USD_CENTS = 100_000

DEFAULT_BUDGET_CAP_CENTS = 100

DEFAULT_UNTIL_SURVIVORS = 1

CONTAINER_NAME = "kasukabe-realsim"
CONTAINER_HOME_ROOT = "/tmp/kasukabe-agents"
CONTAINER_CHECKPOINT_DIR = "/tmp/realsim-checkpoints"
DEFAULT_BROKER_URL = "http://broker:8200"

SURVIVOR_POLL_S = 5.0

WORLD_BASE_URL = "http://127.0.0.1:8100"


@dataclass
class RunHandle:
    """One live run. `process` is None once it has been reaped."""

    agents: int
    wallet_cents: int
    budget_cap_cents: int
    until_survivors: int = DEFAULT_UNTIL_SURVIVORS
    alive: int | None = None
    dead: int = 0
    finished_reason: str | None = None
    process: subprocess.Popen | None = None
    genesis_path: Path | None = None
    home_root: Path | None = None
    checkpoint_dir: Path | None = None
    log_path: Path | None = None
    error: str | None = None
    started_monotonic: float = 0.0
    container_name: str | None = None
    _log_handle: object | None = field(default=None, repr=False)

    def alive_process(self) -> bool:
        """Is the RUNNER still up? Named apart from `alive`, which counts
        living agents - two very different questions to confuse.

        Asks DOCKER, not the local process. `docker compose run -d`
        detaches: the command we spawned exits ~immediately with status 0
        while the run it started keeps going, so polling the local PID
        reports every healthy run as finished the moment it starts.

        Falls back to the local process when docker cannot be reached, so a
        test double that sets `process` directly still behaves.
        """
        if self.process is not None and self.container_name is None:
            return self.process.poll() is None
        if self.container_name is None:
            return False
        try:
            out = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return out.returncode == 0 and out.stdout.strip() == "true"


def max_wallet_cents(agents: int) -> int:
    """The biggest equal purse `agents` people can be given."""
    return TOTAL_USD_CENTS // max(1, agents)


def validate(agents: int, wallet_cents: int) -> str | None:
    """The message to show the operator, or None if the request is sound."""
    if not MIN_AGENTS <= agents <= MAX_AGENTS:
        return f"Cast size must be between {MIN_AGENTS} and {MAX_AGENTS}."
    if wallet_cents < 0:
        return "Starting money cannot be negative."
    cap = max_wallet_cents(agents)
    if wallet_cents > cap:
        return (
            f"{agents} agents at {wallet_cents} cents each is "
            f"{agents * wallet_cents} cents, and the town only holds "
            f"{TOTAL_USD_CENTS} (the fixed $1000). "
            f"The most each can start with is {cap} cents (${cap / 100:.2f})."
        )
    return None


class RealSimController:
    """Owns at most one run. Not thread-safe by design - it is driven from
    one FastAPI event loop, and the lock makes concurrent starts a refusal
    rather than two runners fighting over port 8100."""

    def __init__(self, app_root: Path | None = None,
                 world_client_factory=None) -> None:
        self.app_root = app_root or APP_ROOT
        self.run: RunHandle | None = None
        self._lock = asyncio.Lock()
        self.world_client = None
        self._world_client_factory = world_client_factory or self._default_world_client
        self._watcher: asyncio.Task | None = None

    @staticmethod
    def _default_world_client(base_url: str):
        from game.config import GameSettings
        from game.world_client import HttpWorldClient

        return HttpWorldClient(GameSettings(mock_mode=False, world_base_url=base_url))

    def status(self) -> dict[str, object]:
        run = self.run
        if run is None:
            return {"running": False, "agents": None, "wallet_cents": None,
                    "exit_code": None, "error": None, "alive": None,
                    "dead": 0, "until_survivors": None, "finished_reason": None}
        proc = run.process
        return {
            "running": run.alive_process(),
            "agents": run.agents,
            "wallet_cents": run.wallet_cents,
            "budget_cap_cents": run.budget_cap_cents,
            "exit_code": None if proc is None else proc.poll(),
            "error": run.error,
            "alive": run.alive,
            "dead": run.dead,
            "until_survivors": run.until_survivors,
            "finished_reason": run.finished_reason,
            "log_path": None if run.log_path is None else str(run.log_path),
        }

    async def start(self, agents: int, wallet_cents: int,
                    budget_cap_cents: int = DEFAULT_BUDGET_CAP_CENTS,
                    until_survivors: int = DEFAULT_UNTIL_SURVIVORS) -> dict[str, object]:
        async with self._lock:
            if self.run is not None and self.run.alive_process():
                return {"ok": False, "error": "A run is already going. Stop it first.",
                        "status": self.status()}

            if (problem := validate(agents, wallet_cents)) is not None:
                return {"ok": False, "error": problem, "status": self.status()}

            handle = RunHandle(agents=agents, wallet_cents=wallet_cents,
                               budget_cap_cents=budget_cap_cents,
                               until_survivors=max(0, until_survivors))
            try:
                await asyncio.to_thread(self._spawn, handle)
            except Exception as exc:  # noqa: BLE001 - reported, never raised at the browser
                logger.exception("game.realsim: failed to start")
                handle.error = f"{type(exc).__name__}: {exc}"
                self.run = handle
                return {"ok": False, "error": handle.error, "status": self.status()}

            self.run = handle
            await self._close_world_client()
            self.world_client = self._world_client_factory(WORLD_BASE_URL)
            if handle.until_survivors:
                self._watcher = asyncio.create_task(self._watch_survivors(handle))
            return {"ok": True, "error": None, "status": self.status()}

    def _spawn(self, handle: RunHandle) -> None:
        """Blocking half of start(): write the genesis, clear the state, exec."""
        genesis_path = self.app_root / "data" / f"genesis_realsim_{handle.agents}.json"
        genesis_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                sys.executable, "-m", "world.make_genesis_subset",
                str(handle.agents),
                str(handle.wallet_cents if handle.wallet_cents is not None else ""),
                str(genesis_path),
            ],
            cwd=self.app_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not genesis_path.exists():
            raise RuntimeError(
                "could not build the real-sim genesis: "
                f"{(result.stderr or result.stdout or '').strip()[:400]}"
            )
        handle.genesis_path = genesis_path

        home_root = Path("/tmp/kasukabe-realsim")
        checkpoint_dir = self.app_root / "data" / "realsim-checkpoints"
        for path in (home_root, checkpoint_dir):
            shutil.rmtree(path, ignore_errors=True)
        home_root.mkdir(parents=True, exist_ok=True)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        handle.home_root = home_root
        handle.checkpoint_dir = checkpoint_dir

        log_path = self.app_root / "data" / "realsim.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle.log_path = log_path
        log_handle = log_path.open("w", encoding="utf-8")
        handle._log_handle = log_handle

        env = {**os.environ}
        genesis_arg = f"world/{genesis_path.name}"
        cmd = [
            "docker", "compose", "run", "-d",
            "--name", CONTAINER_NAME,
            "--no-deps",
            "-p", "8100:8100",
            "-v", "../engine:/engine:ro",
            "-e", f"KASUKABE_HOME_ROOT={CONTAINER_HOME_ROOT}",
            "world",
            "python", "-m", "world.run_genesis", "--live",
            "--genesis-path", genesis_arg,
            "--world-mode", "embedded",
            "--world-host", "0.0.0.0", "--world-port", "8100",
            "--broker-base-url", os.environ.get("BROKER_BASE_URL", DEFAULT_BROKER_URL),
            "--budget-cap-cents", str(handle.budget_cap_cents),
            "--supervisor-backend", "process",
            "--engine-repo", "/engine",
            "--no-resume",
            "--checkpoint-dir", CONTAINER_CHECKPOINT_DIR,
        ]
        try:
            subprocess.run(["docker", "rm", "-f", CONTAINER_NAME],
                           capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        handle.container_name = CONTAINER_NAME
        logger.info("game.realsim: starting %d agents at %d cents each in %s",
                    handle.agents, handle.wallet_cents, CONTAINER_NAME)
        handle.process = subprocess.Popen(
            cmd, cwd=str(self.app_root), env=env,
            stdout=log_handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    async def stop(self) -> dict[str, object]:
        async with self._lock:
            run = self.run
            if run is None or run.process is None:
                return {"ok": True, "error": None, "status": self.status()}
            if self._watcher is not None:
                self._watcher.cancel()
                self._watcher = None
            await asyncio.to_thread(self._terminate, run)
            await self._close_world_client()
            return {"ok": True, "error": None, "status": self.status()}

    def _terminate(self, run: RunHandle) -> None:
        if run.container_name is not None:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", run.container_name],
                    capture_output=True, text=True, timeout=60,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("game.realsim: could not remove %s: %s",
                               run.container_name, exc)
            self._close_log(run)
            return

        proc = run.process
        if proc is None or proc.poll() is not None:
            self._close_log(run)
            return
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (AttributeError, OSError):
            proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (AttributeError, OSError):
                proc.kill()
            proc.wait(timeout=10)
        self._close_log(run)

    async def _watch_survivors(self, handle: RunHandle) -> None:
        """Stop the run when the town is down to `until_survivors` alive.

        A survival experiment has to END, and nothing else here ends it: the
        runner has no opinion about population, and a budget cap stops the
        spending without answering the question. This polls the world the run
        brought up and shuts it down the moment the answer is in - which is
        also the moment it stops costing money.
        """
        try:
            while True:
                await asyncio.sleep(SURVIVOR_POLL_S)
                if not handle.alive_process():
                    handle.finished_reason = "the runner exited"
                    return
                client = self.world_client
                if client is None:
                    return
                try:
                    snap = await client.get_snapshot()
                except Exception:  # noqa: BLE001 - a blip is not the end of a run
                    continue
                alive = sum(1 for a in snap.agents if a.is_alive)
                handle.alive = alive
                handle.dead = max(0, handle.agents - alive)
                if alive <= handle.until_survivors:
                    handle.finished_reason = (
                        f"{alive} of {handle.agents} left alive"
                        if alive else "nobody survived"
                    )
                    logger.info("game.realsim: survival run finished - %s",
                                handle.finished_reason)
                    await asyncio.to_thread(self._terminate, handle)
                    await self._close_world_client()
                    return
        except asyncio.CancelledError:
            raise

    async def _close_world_client(self) -> None:
        client = self.world_client
        self.world_client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is None:
            return
        try:
            await close()
        except Exception:  # noqa: BLE001 - a failed close must not block a stop
            logger.debug("game.realsim: world client close failed", exc_info=True)

    @staticmethod
    def _close_log(run: RunHandle) -> None:
        handle = run._log_handle
        if handle is not None:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - closing a log must never raise
                pass
            run._log_handle = None
