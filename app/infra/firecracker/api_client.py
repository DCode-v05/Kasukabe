"""Async client for the Firecracker HTTP API, spoken over a Unix domain socket.

Every Firecracker microVM process exposes a REST API on a UDS it creates at
startup (`firecracker --api-sock <path>`); there is no TCP endpoint and no
port to collide across 250 co-resident microVMs, which is why the socket
path (not a port number) is the addressing unit throughout this file and
resources.py.

This wraps exactly the endpoints vm_manager.py needs to drive one unit
through its full lifecycle: configure -> InstanceStart -> pause/resume ->
snapshot create -> snapshot load. It is deliberately not a general
Firecracker API SDK - DESIGN.md's genesis run needs this much and no more,
and a bigger surface would be more to keep in sync with Firecracker
releases for no benefit here.

httpx's AsyncHTTPTransport(uds=...) is what makes talking HTTP over a UDS
possible; the base_url is a dummy ("http://fc") purely because httpx
requires *some* scheme+host to build request lines against, not because
any DNS lookup or TCP connect happens - the transport routes every
request down the UDS regardless of what host appears in the URL.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx


class FirecrackerApiError(Exception):
    """Raised for any non-2xx response. Carries the endpoint and
    Firecracker's own fault_message (its documented error body shape:
    {"fault_message": "..."}) so a boot failure during a 250-VM genesis
    run points straight at which unit and which call, not just "500".
    """

    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body
        super().__init__(f"{method} {path} -> {status_code}: {body}")


class FirecrackerApiClient:
    """One instance per microVM, bound to that microVM's own API socket."""

    def __init__(self, api_socket_path: str, request_timeout_s: float = 5.0) -> None:
        self.api_socket_path = api_socket_path
        transport = httpx.AsyncHTTPTransport(uds=api_socket_path)
        self._client = httpx.AsyncClient(
            transport=transport, base_url="http://fc", timeout=request_timeout_s
        )

    async def __aenter__(self) -> FirecrackerApiClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._client.request(method, path, json=json)
        if resp.status_code // 100 != 2:
            raise FirecrackerApiError(method, path, resp.status_code, resp.text)
        if resp.content:
            return resp.json()
        return None

    async def get_instance_info(self) -> dict[str, Any]:
        return await self._request("GET", "/")

    async def configure_boot_source(self, kernel_image_path: str, boot_args: str) -> None:
        await self._request(
            "PUT",
            "/boot-source",
            json={"kernel_image_path": kernel_image_path, "boot_args": boot_args},
        )

    async def configure_drive(
        self,
        drive_id: str,
        path_on_host: str,
        is_root_device: bool,
        is_read_only: bool = False,
    ) -> None:
        await self._request(
            "PUT",
            f"/drives/{drive_id}",
            json={
                "drive_id": drive_id,
                "path_on_host": path_on_host,
                "is_root_device": is_root_device,
                "is_read_only": is_read_only,
            },
        )

    async def configure_network_interface(self, iface_id: str, host_dev_name: str) -> None:
        await self._request(
            "PUT",
            f"/network-interfaces/{iface_id}",
            json={"iface_id": iface_id, "host_dev_name": host_dev_name},
        )

    async def configure_vsock(self, vsock_id: str, guest_cid: int, uds_path: str) -> None:
        await self._request(
            "PUT",
            "/vsock",
            json={"vsock_id": vsock_id, "guest_cid": guest_cid, "uds_path": uds_path},
        )

    async def configure_machine(self, vcpu_count: int, mem_size_mib: int) -> None:
        await self._request(
            "PUT",
            "/machine-config",
            json={"vcpu_count": vcpu_count, "mem_size_mib": mem_size_mib},
        )

    async def start_instance(self) -> None:
        await self._request("PUT", "/actions", json={"action_type": "InstanceStart"})

    async def pause(self) -> None:
        await self._request("PATCH", "/vm", json={"state": "Paused"})

    async def resume(self) -> None:
        await self._request("PATCH", "/vm", json={"state": "Resumed"})

    async def create_snapshot(
        self, snapshot_path: str, mem_file_path: str, snapshot_type: str = "Full"
    ) -> None:
        await self._request(
            "PUT",
            "/snapshot/create",
            json={
                "snapshot_type": snapshot_type,
                "snapshot_path": snapshot_path,
                "mem_file_path": mem_file_path,
            },
        )

    async def load_snapshot(
        self, snapshot_path: str, mem_file_path: str, resume_vm: bool = True
    ) -> None:
        await self._request(
            "PUT",
            "/snapshot/load",
            json={
                "snapshot_path": snapshot_path,
                "mem_file_path": mem_file_path,
                "resume_vm": resume_vm,
            },
        )
