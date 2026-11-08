#!/usr/local/bin/python3
"""PID 1 for the Kasukabe engine microVM guest.

Firecracker's boot-source.boot_args (vm_manager.py's build_boot_args())
sets init=/usr/local/sbin/engine-init, so the kernel execve()s this file
directly as PID 1 - no systemd, no OpenRC, no shell wrapper. Being PID 1
carries two real obligations this script exists to satisfy, not just
"start uvicorn":

1. Mount the filesystems the kernel does not mount for you (proc, sysfs,
   a writable /data and /tmp on top of the read-only rootfs drive - see
   engine/Dockerfile's own SEPTEMBER_HOME comment for why the engine
   needs a writable path). devtmpfs is the one exception: CONFIG_DEVTMPFS_MOUNT=y
   in kernel/microvm-kernel.config already mounts it before userspace
   starts, so mounting it again here is a no-op safety net, not a
   requirement.
2. Reap zombies. Every process that dies with no parent still waiting on
   it reparents to PID 1, and PID 1 is the only process obligated to
   call wait() on it or it stays a zombie forever. The engine spawns
   bubblewrap children for tool sandboxing (engine/Dockerfile's own
   comment on this); a minimal init that forks the engine once and then
   never calls wait() again would leak every one of those.

Networking is deliberately NOT configured here: kernel/microvm-kernel.config
has CONFIG_IP_PNP=y, so the kernel itself parses boot_args' ip=<...>
token and brings up eth0 with a static address before this script ever
runs - see vm_manager.py's build_boot_args() for exactly what it passes.
No iproute2, no dhclient, nothing to install in the rootfs for this.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.parse

ENGINE_UID = 1000
ENGINE_GID = 1000
ENGINE_WORKDIR = "/app"


def mount(fstype: str, source: str, target: str) -> None:
    os.makedirs(target, exist_ok=True)
    result = subprocess.run(
        ["mount", "-t", fstype, source, target], capture_output=True, text=True
    )
    if result.returncode != 0 and "already mounted" not in result.stderr:
        sys.stderr.write(f"engine-init: mount -t {fstype} {source} {target}: {result.stderr}\n")


def parse_cmdline() -> dict[str, str]:
    with open("/proc/cmdline", encoding="utf-8") as f:
        raw = f.read().strip()
    values: dict[str, str] = {}
    for token in raw.split():
        if "=" in token:
            key, _, value = token.partition("=")
            values[key] = value
    return values


def build_engine_env(cmdline: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env["ENGINE_ID"] = cmdline.get("engine_id", "")
    env["CDT_URL"] = urllib.parse.unquote(cmdline.get("cdt_url", ""))
    env["CDT_KEY"] = urllib.parse.unquote(cmdline.get("cdt_key", ""))
    env["SEPTEMBER_HOME"] = "/data"
    env["KASUKABE_UNIT_ID"] = cmdline.get("unit_id", "")
    # See vm_manager.py's build_boot_args() docstring: extra__<KEY> tokens
    # are the escape hatch for anything beyond the four identity fields
    # above, url-quoted the same way and decoded the same way here.
    prefix = "extra__"
    for key, value in cmdline.items():
        if key.startswith(prefix):
            env[key[len(prefix) :]] = urllib.parse.unquote(value)
    return env


def main() -> None:
    mount("proc", "proc", "/proc")
    mount("sysfs", "sysfs", "/sys")
    mount("devtmpfs", "devtmpfs", "/dev")
    mount("tmpfs", "tmpfs", "/data")
    mount("tmpfs", "tmpfs", "/tmp")
    os.chown("/data", ENGINE_UID, ENGINE_GID)
    os.chmod("/data", 0o755)

    cmdline = parse_cmdline()
    env = build_engine_env(cmdline)

    child_pid = os.fork()
    if child_pid == 0:
        os.chdir(ENGINE_WORKDIR)
        os.setgid(ENGINE_GID)
        os.setuid(ENGINE_UID)
        os.execvpe(
            "uvicorn",
            ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"],
            env,
        )
        os._exit(127)  # only reached if execvpe itself failed

    sys.stderr.write(f"engine-init: engine started as pid {child_pid}\n")

    while True:
        try:
            reaped_pid, status = os.wait()
        except ChildProcessError:
            break
        if reaped_pid == child_pid:
            code = os.waitstatus_to_exitcode(status)
            sys.stderr.write(f"engine-init: engine process exited ({code}), powering off\n")
            subprocess.run(["poweroff", "-f"])
            os._exit(code if code >= 0 else 1)


if __name__ == "__main__":
    main()
