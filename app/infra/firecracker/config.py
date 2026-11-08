"""Firecracker track settings.

Mirrors engine/src/config.py's idiom (app/CONVENTIONS.md: "aggregate
errors instead of failing on the first one where the caller benefits from
seeing everything wrong at once"): build_from_env() collects every problem
in one ConfigError instead of stopping at the first missing path, because
a genesis-day operator staring at a boot failure needs the full list of
what is wrong with the host, not one fact per retry.

Every field defaults to values that make sense on the m5.metal genesis
host (DESIGN.md section 9); local dev never boots a real microVM (no KVM
on this Mac, see app/infra/firecracker/README.md), so these defaults are
exercised for real only on that host, and by the fake backend's tests
everywhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised by build_from_env() with every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("firecracker config invalid:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class FirecrackerSettings:
    firecracker_binary: Path = Path("/usr/local/bin/firecracker")

    use_jailer: bool = False
    jailer_binary: Path = Path("/usr/local/bin/jailer")

    kernel_image: Path = Path("/var/lib/kasukabe/kernel/vmlinux")
    rootfs_image: Path = Path("/var/lib/kasukabe/rootfs/engine-rootfs.ext4")

    run_dir: Path = Path("/var/run/kasukabe/firecracker")

    network_cidr: str = "10.200.0.0/20"
    bridge_name: str = "fcbr0"
    bridge_host_ip: str = "10.200.0.1"
    tap_prefix: str = "fctap"

    vsock_cid_base: int = 3
    vsock_guest_port: int = 52000

    s3_bucket: str = "kasukabe-microvm-snapshots"
    s3_prefix: str = "kasukabe/vm-snapshots"
    aws_region: str = "ap-south-1"

    default_vcpu_count: int = 1
    boot_timeout_s: float = 8.0
    api_request_timeout_s: float = 5.0

    extra_env: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def build_from_env(env: dict[str, str] | None = None) -> FirecrackerSettings:
        e = env if env is not None else dict(os.environ)
        problems: list[str] = []

        def get_path(key: str, default: Path) -> Path:
            raw = e.get(key)
            return Path(raw) if raw else default

        def get_int(key: str, default: int) -> int:
            raw = e.get(key)
            if raw is None or raw == "":
                return default
            try:
                return int(raw)
            except ValueError:
                problems.append(f"{key}={raw!r} is not an integer")
                return default

        def get_float(key: str, default: float) -> float:
            raw = e.get(key)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                problems.append(f"{key}={raw!r} is not a number")
                return default

        def get_bool(key: str, default: bool) -> bool:
            raw = e.get(key)
            if raw is None or raw == "":
                return default
            if raw.lower() in ("1", "true", "yes", "on"):
                return True
            if raw.lower() in ("0", "false", "no", "off"):
                return False
            problems.append(f"{key}={raw!r} is not a recognizable boolean")
            return default

        vcpu = get_int("FC_DEFAULT_VCPU_COUNT", 1)
        if vcpu < 1:
            problems.append(f"FC_DEFAULT_VCPU_COUNT={vcpu} must be >= 1")
            vcpu = 1

        vsock_base = get_int("FC_VSOCK_CID_BASE", 3)
        if vsock_base < 3:
            problems.append(f"FC_VSOCK_CID_BASE={vsock_base} must be >= 3 (0-2 are reserved)")
            vsock_base = 3

        settings = FirecrackerSettings(
            firecracker_binary=get_path("FC_BINARY", Path("/usr/local/bin/firecracker")),
            use_jailer=get_bool("FC_USE_JAILER", False),
            jailer_binary=get_path("FC_JAILER_BINARY", Path("/usr/local/bin/jailer")),
            kernel_image=get_path("FC_KERNEL_IMAGE", Path("/var/lib/kasukabe/kernel/vmlinux")),
            rootfs_image=get_path(
                "FC_ROOTFS_IMAGE", Path("/var/lib/kasukabe/rootfs/engine-rootfs.ext4")
            ),
            run_dir=get_path("FC_RUN_DIR", Path("/var/run/kasukabe/firecracker")),
            network_cidr=e.get("FC_NETWORK_CIDR", "10.200.0.0/20"),
            bridge_name=e.get("FC_BRIDGE_NAME", "fcbr0"),
            bridge_host_ip=e.get("FC_BRIDGE_HOST_IP", "10.200.0.1"),
            tap_prefix=e.get("FC_TAP_PREFIX", "fctap"),
            vsock_cid_base=vsock_base,
            vsock_guest_port=get_int("FC_VSOCK_GUEST_PORT", 52000),
            s3_bucket=e.get("FC_S3_BUCKET", "kasukabe-microvm-snapshots"),
            s3_prefix=e.get("FC_S3_PREFIX", "kasukabe/vm-snapshots"),
            aws_region=e.get("AWS_REGION", "ap-south-1"),
            default_vcpu_count=vcpu,
            boot_timeout_s=get_float("FC_BOOT_TIMEOUT_S", 8.0),
            api_request_timeout_s=get_float("FC_API_REQUEST_TIMEOUT_S", 5.0),
        )

        if problems:
            raise ConfigError(problems)
        return settings

    def validate_for_real_boot(self) -> list[str]:
        """Checks only relevant when actually booting a real microVM (not
        the fake backend). Kept separate from build_from_env() so unit
        tests can build a FirecrackerSettings without a kernel/rootfs
        present on disk, which is every environment except the metal
        host until RUNBOOK.md's build steps have run there.
        """
        problems: list[str] = []
        if self.use_jailer and not self.jailer_binary.exists():
            problems.append(f"FC_USE_JAILER is set but {self.jailer_binary} does not exist")
        if not self.firecracker_binary.exists():
            problems.append(f"firecracker binary not found at {self.firecracker_binary}")
        if not self.kernel_image.exists():
            problems.append(
                f"kernel image not found at {self.kernel_image} "
                "(run app/infra/firecracker/kernel/build_kernel.sh on a Linux builder first)"
            )
        if not self.rootfs_image.exists():
            problems.append(
                f"rootfs image not found at {self.rootfs_image} "
                "(run app/infra/firecracker/rootfs/build_rootfs.sh first)"
            )
        return problems
