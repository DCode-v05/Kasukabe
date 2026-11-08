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

# Bounds on what the dialog may ask for. The upper bound on the cast is the
# genesis file's own population; the lower is "a town", not "an agent".
MIN_AGENTS = 2
MAX_AGENTS = 250
DEFAULT_AGENTS = 50

# $2.00, matching world/genesis.json's own usd_wallet_cents.
DEFAULT_WALLET_CENTS = 200
# The whole cast can hold at most the fixed $1000 between them.
TOTAL_USD_CENTS = 100_000

DEFAULT_BUDGET_CAP_CENTS = 100          # $1.00

# Stop when this many are left alive. 1 = "run until the last one standing".
# 0 turns the watcher off and the run goes until its budget cap.
DEFAULT_UNTIL_SURVIVORS = 1

# The run's container, and the paths inside it. Named so stop() can find
# and remove the run even if this process restarted since starting it -
# a host PID does not survive that, a container name does.
CONTAINER_NAME = "kasukabe-realsim"
CONTAINER_HOME_ROOT = "/tmp/kasukabe-agents"
CONTAINER_CHECKPOINT_DIR = "/tmp/realsim-checkpoints"
# Reachable from inside the compose network. The broker must already be up:
# starting a money-spending service as a side effect of starting a run is
# one surprise too many, so start() checks and refuses with instructions.
#
# UI-15: this said `http://broker-real:8200` and NO SERVICE OF THAT NAME
# EXISTS. `docker-compose.yml` defines test, world, broker, observatory
# and game; `broker-real` is in none of them, so on the compose network
# the hostname does not resolve and every call the run makes fails at DNS
# - a button whose whole purpose is to spend real money on real
# inference, wired to a host that is not there.
#
# `broker` is the service that exists. BROKER_BASE_URL still overrides,
# for a deployment that really does run a separate one.
DEFAULT_BROKER_URL = "http://broker:8200"

# How often the survival watcher asks the world who is still alive. The world
# is in-process with the runner and this is one cheap HTTP GET, but a survival
# run lasts hours, so once every few seconds is plenty.
SURVIVOR_POLL_S = 5.0

# The embedded world every run brings up. Fixed because
# run_genesis.py is spawned with --world-port 8100 below.
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
    # Set when the run lives in a container. A host PID does not survive a
    # viewer restart; a container name does, so this is also what lets stop()
    # clean up a run this process did not start.
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
        # A WorldClient pointed at the run's own embedded world, built when a
        # run starts and dropped when it stops.
        #
        # Without this the button starts 50 engines on port 8100 and the map
        # keeps showing whatever world the game server was launched against -
        # normally the 12-agent mock. Observed exactly that: a healthy 50-agent
        # run with zero tick failures, and a viewer reporting
        # `cognition: mock, reported: 12`. The operator has no way to fix it
        # from the page, because the binding was decided at process start.
        self.world_client = None
        self._world_client_factory = world_client_factory or self._default_world_client
        self._watcher: asyncio.Task | None = None

    @staticmethod
    def _default_world_client(base_url: str):
        from game.config import GameSettings
        from game.world_client import HttpWorldClient

        return HttpWorldClient(GameSettings(mock_mode=False, world_base_url=base_url))

    # ── status ───────────────────────────────────────────────────────
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
            # A run that died on its own is not "not running": the operator
            # needs to see that it started and then stopped, and why.
            "exit_code": None if proc is None else proc.poll(),
            "error": run.error,
            "alive": run.alive,
            "dead": run.dead,
            "until_survivors": run.until_survivors,
            "finished_reason": run.finished_reason,
            "log_path": None if run.log_path is None else str(run.log_path),
        }

    # ── start ────────────────────────────────────────────────────────
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
            # Point the viewer at the world this run just brought up.
            await self._close_world_client()
            self.world_client = self._world_client_factory(WORLD_BASE_URL)
            if handle.until_survivors:
                self._watcher = asyncio.create_task(self._watch_survivors(handle))
            return {"ok": True, "error": None, "status": self.status()}

    def _spawn(self, handle: RunHandle) -> None:
        """Blocking half of start(): write the genesis, clear the state, exec."""
        # UI-14. This used to be
        # `from world.make_genesis_subset import build_subset`, writing
        # the result into `app/world/` - game/ importing world/, and
        # game/ writing into world/'s own directory. app/CONVENTIONS.md:
        # "no package imports another package's internals across this
        # diagram", and game/README.md makes game/ presentation only,
        # possibly a separately deployed process - which this import would
        # make impossible.
        #
        # A subprocess, which is what the rest of this module already does
        # for the runner itself. And into `data/`, which belongs to
        # nobody in particular, rather than into a sibling package.
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

        # A FRESH town. Deleting the homes is what makes it fresh: persona.txt
        # is rewritten each boot, but brain.sqlite is not, so a second run
        # would start with the first run's memories intact.
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
        # Kept open for the life of the child: a pipe nobody drains fills and
        # blocks the engine, which is why this is a file rather than PIPE.
        log_handle = log_path.open("w", encoding="utf-8")
        handle._log_handle = log_handle

        # THE RUNNER RUNS IN DOCKER, NOT ON THIS HOST.
        #
        # The September engine is Linux by architecture - its sandbox uses
        # seccomp, /proc, unshare and prctl, and it dies at import on
        # signal.SIGKILL anywhere else. Spawned as a host subprocess this
        # button started a runner whose engines could never boot, on any
        # machine that is not Linux. ROADMAP.md Law 1 rules out forking the
        # engine to fix that, so the runner goes where the engine works.
        #
        # Paths below are CONTAINER paths. app/world is bind-mounted into
        # the world service, so the genesis file written above at
        # app/world/genesis_realsim_N.json is visible at world/... inside.
        env = {**os.environ}
        genesis_arg = f"world/{genesis_path.name}"
        cmd = [
            "docker", "compose", "run", "-d",
            "--name", CONTAINER_NAME,
            "--no-deps",
            # The map polls 127.0.0.1:8100 on the host, so the run's embedded
            # world has to be published AND bound to 0.0.0.0 - the default
            # 127.0.0.1 is only reachable from inside the container.
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
        # Remove any container left by a previous run before reusing the
        # name - docker refuses a duplicate, and the failure would look
        # like "the button does nothing".
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
            # Its own process group, so stop() can take the engines down with
            # it. The runner spawns one subprocess per agent; killing only the
            # parent leaves 50 engines holding their ports.
            start_new_session=True,
        )

    # ── stop ─────────────────────────────────────────────────────────
    async def stop(self) -> dict[str, object]:
        async with self._lock:
            run = self.run
            if run is None or run.process is None:
                return {"ok": True, "error": None, "status": self.status()}
            if self._watcher is not None:
                self._watcher.cancel()
                self._watcher = None
            await asyncio.to_thread(self._terminate, run)
            # And release the binding, so the map falls back to whatever world
            # the server was started against rather than polling a dead port.
            await self._close_world_client()
            return {"ok": True, "error": None, "status": self.status()}

    def _terminate(self, run: RunHandle) -> None:
        if run.container_name is not None:
            # Removes the runner AND every engine it spawned: they are
            # children inside that container, so there is nothing left to
            # leak holding port 8100.
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
            # The whole group: the runner plus every engine it spawned.
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (AttributeError, OSError):
            # UI-15: `os.killpg`/`os.getpgid` DO NOT EXIST on Windows, and
            # this project is developed on Windows. The old clause caught
            # ProcessLookupError and PermissionError, neither of which is
            # what a missing attribute raises, so stopping a non-container
            # run raised AttributeError out of `stop()` and the run was
            # left alive with its engines still spending.
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
