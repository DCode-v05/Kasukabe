"""Two ways to get a Firecracker API socket answering requests: a real
`firecracker` subprocess, or an in-process fake that speaks the same wire
protocol.

vm_manager.py takes a ProcessLauncher and never checks which one it got -
that is what makes test_vm_manager_lifecycle.py's 3-VM boot/snapshot/
restore/verify test a real exercise of the orchestration code (resource
allocation, state machine, error handling) and not a test of a mock's
behavior standing in for the orchestration code, which would be the usual
failure mode of over-mocking.

FakeFirecrackerProcess exists because this track was built on a Mac dev
host with no /dev/kvm (see app/infra/firecracker/README.md) - there is no
way to boot a real microVM here. It is deliberately NOT a stub that always
returns success: it is a real FastAPI app, served over a real Unix socket,
implementing a real (small) state machine that rejects out-of-order calls
the same way Firecracker's actual API does (e.g. create_snapshot before
pause), so a bug in vm_manager.py's call ordering fails the fake exactly
as it would fail against real Firecracker. What it cannot exercise is
anything below the HTTP API: actual KVM, actual kernel boot, actual
guest-side vsock/init behavior. That gap is real and RUNBOOK.md's
real-hardware checklist (also mirrored in
tests/test_real_firecracker_integration.py, skipped here) is how it gets
closed on the m5.metal host before genesis.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request
import uvicorn


class ProcessHandle(Protocol):
    """What vm_manager.py needs from either backend after launch: a way to
    tell whether it is still alive and a way to make it stop."""

    @property
    def is_running(self) -> bool: ...

    async def terminate(self, timeout_s: float = 5.0) -> None: ...


class ProcessLauncher(Protocol):
    async def launch(self, unit_id: str, api_socket_path: str, run_dir: Path) -> ProcessHandle: ...


class RealFirecrackerProcessHandle:
    def __init__(self, proc: asyncio.subprocess.Process, api_socket_path: str) -> None:
        self._proc = proc
        self.api_socket_path = api_socket_path

    @property
    def is_running(self) -> bool:
        return self._proc.returncode is None

    async def terminate(self, timeout_s: float = 5.0) -> None:
        if self._proc.returncode is not None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()
        finally:
            # Firecracker unlinks its own socket on clean exit but not
            # after a kill -9; a stale socket file blocks the next
            # start() for this unit_id with EADDRINUSE, so clear it here
            # unconditionally rather than special-casing the kill path.
            try:
                os.unlink(self.api_socket_path)
            except FileNotFoundError:
                pass


class RealFirecrackerProcessLauncher:
    """Spawns the actual `firecracker` binary. Exercised only on the
    metal host (RUNBOOK.md); requires /dev/kvm, so vm_manager.py never
    selects this launcher unless FirecrackerSettings.validate_for_real_boot()
    passed, which itself only passes where the binary and images exist.
    """

    def __init__(self, firecracker_binary: Path, jailer_binary: Path, use_jailer: bool) -> None:
        self.firecracker_binary = firecracker_binary
        self.jailer_binary = jailer_binary
        self.use_jailer = use_jailer

    async def launch(
        self, unit_id: str, api_socket_path: str, run_dir: Path
    ) -> RealFirecrackerProcessHandle:
        run_dir.mkdir(parents=True, exist_ok=True)
        # Stale socket from a prior crash blocks bind(); Firecracker
        # itself refuses to start against an existing path.
        try:
            os.unlink(api_socket_path)
        except FileNotFoundError:
            pass

        if self.use_jailer:
            # jailer re-execs firecracker inside a chroot under a cgroup
            # it creates from --cgroup-version/--resource-limit args
            # (RUNBOOK.md has the full genesis-run invocation); the
            # bare-firecracker path below is what every non-jailer boot
            # (including every test) actually runs.
            args = [
                str(self.jailer_binary),
                "--id",
                unit_id,
                "--exec-file",
                str(self.firecracker_binary),
                "--uid",
                "0",
                "--gid",
                "0",
                "--chroot-base-dir",
                str(run_dir),
                "--",
                "--api-sock",
                api_socket_path,
            ]
            binary = args[0]
            proc_args = args[1:]
        else:
            binary = str(self.firecracker_binary)
            proc_args = ["--api-sock", api_socket_path]

        proc = await asyncio.create_subprocess_exec(
            binary,
            *proc_args,
            stdout=open(run_dir / "firecracker.stdout.log", "ab"),
            stderr=open(run_dir / "firecracker.stderr.log", "ab"),
        )

        deadline = asyncio.get_event_loop().time() + 5.0
        while not Path(api_socket_path).exists():
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(
                    f"firecracker for {unit_id} did not create {api_socket_path} in time"
                )
            await asyncio.sleep(0.02)

        return RealFirecrackerProcessHandle(proc, api_socket_path)


def build_fake_firecracker_app() -> FastAPI:
    """A minimal but faithful re-implementation of the slice of the
    Firecracker API surface api_client.py calls, plus a /_test/* namespace
    (unmistakably non-Firecracker, so nobody mistakes it for the real API)
    that lets tests inject and read back "guest memory" content, which is
    what makes the snapshot/restore test able to assert on more than call
    sequencing.
    """

    app = FastAPI()
    state: dict[str, Any] = {
        "phase": "uninitialized",  # uninitialized -> configured -> running -> paused
        "boot_source": None,
        "drives": {},
        "network_interfaces": {},
        "vsock": None,
        "machine_config": None,
        "guest_state": {},
    }

    def require_phase(*allowed: str) -> None:
        if state["phase"] not in allowed:
            raise HTTPException(
                status_code=400,
                detail={
                    "fault_message": (
                        f"operation not allowed in phase {state['phase']!r}, "
                        f"expected one of {allowed}"
                    )
                },
            )

    @app.get("/")
    async def get_info() -> dict[str, Any]:
        return {"state": state["phase"], "vmm_version": "fake-0.0.0"}

    @app.put("/boot-source")
    async def put_boot_source(req: Request) -> dict[str, Any]:
        require_phase("uninitialized")
        state["boot_source"] = await req.json()
        return {}

    @app.put("/drives/{drive_id}")
    async def put_drive(drive_id: str, req: Request) -> dict[str, Any]:
        require_phase("uninitialized")
        state["drives"][drive_id] = await req.json()
        return {}

    @app.put("/network-interfaces/{iface_id}")
    async def put_network_interface(iface_id: str, req: Request) -> dict[str, Any]:
        require_phase("uninitialized")
        state["network_interfaces"][iface_id] = await req.json()
        return {}

    @app.put("/vsock")
    async def put_vsock(req: Request) -> dict[str, Any]:
        require_phase("uninitialized")
        state["vsock"] = await req.json()
        return {}

    @app.put("/machine-config")
    async def put_machine_config(req: Request) -> dict[str, Any]:
        require_phase("uninitialized", "configured")
        state["machine_config"] = await req.json()
        state["phase"] = "configured"
        return {}

    @app.put("/actions")
    async def put_action(req: Request) -> dict[str, Any]:
        body = await req.json()
        if body.get("action_type") == "InstanceStart":
            require_phase("configured")
            if state["boot_source"] is None:
                raise HTTPException(
                    status_code=400,
                    detail={"fault_message": "cannot start: no boot-source configured"},
                )
            state["phase"] = "running"
            return {}
        raise HTTPException(
            status_code=400, detail={"fault_message": f"unsupported action {body}"}
        )

    @app.patch("/vm")
    async def patch_vm(req: Request) -> dict[str, Any]:
        body = await req.json()
        target = body.get("state")
        if target == "Paused":
            require_phase("running")
            state["phase"] = "paused"
            return {}
        if target == "Resumed":
            require_phase("paused")
            state["phase"] = "running"
            return {}
        raise HTTPException(status_code=400, detail={"fault_message": f"bad vm state {target}"})

    @app.put("/snapshot/create")
    async def put_snapshot_create(req: Request) -> dict[str, Any]:
        # Real Firecracker requires Paused for snapshot/create; enforcing
        # it here is what catches a vm_manager.py regression that snapshots
        # a still-running unit.
        require_phase("paused")
        body = await req.json()
        snapshot_path = Path(body["snapshot_path"])
        mem_file_path = Path(body["mem_file_path"])
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        mem_file_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            json.dumps(
                {
                    "boot_source": state["boot_source"],
                    "drives": state["drives"],
                    "network_interfaces": state["network_interfaces"],
                    "vsock": state["vsock"],
                    "machine_config": state["machine_config"],
                }
            )
        )
        # The mem_file is where real Firecracker puts the actual guest
        # memory pages; here it carries the fake's stand-in for "what the
        # engine process inside the guest currently holds in memory" so
        # the restore path has real content to prove it moved.
        mem_file_path.write_text(json.dumps(state["guest_state"]))
        return {}

    @app.put("/snapshot/load")
    async def put_snapshot_load(req: Request) -> dict[str, Any]:
        require_phase("uninitialized")
        body = await req.json()
        snapshot_path = Path(body["snapshot_path"])
        mem_file_path = Path(body["mem_file_path"])
        loaded = json.loads(snapshot_path.read_text())
        state["boot_source"] = loaded["boot_source"]
        state["drives"] = loaded["drives"]
        state["network_interfaces"] = loaded["network_interfaces"]
        state["vsock"] = loaded["vsock"]
        state["machine_config"] = loaded["machine_config"]
        state["guest_state"] = json.loads(mem_file_path.read_text())
        state["phase"] = "running" if body.get("resume_vm", True) else "paused"
        return {}

    # --- test-only guest-state injection, not part of the Firecracker API ---

    @app.post("/_test/guest-state")
    async def test_set_guest_state(req: Request) -> dict[str, Any]:
        state["guest_state"] = await req.json()
        return {}

    @app.get("/_test/guest-state")
    async def test_get_guest_state() -> dict[str, Any]:
        return state["guest_state"]

    @app.get("/_test/phase")
    async def test_get_phase() -> dict[str, Any]:
        return {"phase": state["phase"]}

    return app


class FakeFirecrackerProcessHandle:
    def __init__(self, server: uvicorn.Server, serve_task: asyncio.Task[None]) -> None:
        self._server = server
        self._serve_task = serve_task

    @property
    def is_running(self) -> bool:
        return not self._serve_task.done()

    async def terminate(self, timeout_s: float = 5.0) -> None:
        if self._serve_task.done():
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._serve_task, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._serve_task.cancel()


class FakeFirecrackerProcessLauncher:
    """Test-only ProcessLauncher: one uvicorn server per unit, each bound
    to that unit's own UDS path, running the app from
    build_fake_firecracker_app(). Structurally identical to
    RealFirecrackerProcessLauncher from vm_manager.py's point of view -
    both just answer "launch(unit_id, socket_path, run_dir) -> handle
    with is_running and terminate()".
    """

    def __init__(self) -> None:
        self._apps: dict[str, FastAPI] = {}

    async def launch(
        self, unit_id: str, api_socket_path: str, run_dir: Path
    ) -> FakeFirecrackerProcessHandle:
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.unlink(api_socket_path)
        except FileNotFoundError:
            pass

        app = build_fake_firecracker_app()
        self._apps[unit_id] = app
        config = uvicorn.Config(app, uds=api_socket_path, log_level="warning")
        server = uvicorn.Server(config)
        serve_task = asyncio.create_task(server.serve())

        deadline = asyncio.get_event_loop().time() + 5.0
        while not Path(api_socket_path).exists():
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"fake firecracker for {unit_id} never bound its socket")
            await asyncio.sleep(0.01)

        return FakeFirecrackerProcessHandle(server, serve_task)
