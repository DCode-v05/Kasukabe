from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ConfigError, FirecrackerSettings  # noqa: E402


def test_build_from_env_defaults_when_env_empty() -> None:
    settings = FirecrackerSettings.build_from_env({})
    assert settings.default_vcpu_count == 1
    assert settings.vsock_cid_base == 3
    assert settings.network_cidr == "10.200.0.0/20"


def test_build_from_env_applies_overrides() -> None:
    settings = FirecrackerSettings.build_from_env(
        {
            "FC_DEFAULT_VCPU_COUNT": "2",
            "FC_NETWORK_CIDR": "10.201.0.0/16",
            "FC_S3_BUCKET": "my-bucket",
            "FC_USE_JAILER": "true",
        }
    )
    assert settings.default_vcpu_count == 2
    assert settings.network_cidr == "10.201.0.0/16"
    assert settings.s3_bucket == "my-bucket"
    assert settings.use_jailer is True


def test_build_from_env_aggregates_every_problem_not_just_the_first() -> None:
    with pytest.raises(ConfigError) as excinfo:
        FirecrackerSettings.build_from_env(
            {
                "FC_DEFAULT_VCPU_COUNT": "not-a-number",
                "FC_VSOCK_CID_BASE": "1",
                "FC_BOOT_TIMEOUT_S": "also-not-a-number",
            }
        )
    problems = excinfo.value.problems
    assert len(problems) == 3, problems
    joined = "\n".join(problems)
    assert "FC_DEFAULT_VCPU_COUNT" in joined
    assert "FC_VSOCK_CID_BASE" in joined
    assert "FC_BOOT_TIMEOUT_S" in joined


def test_validate_for_real_boot_reports_missing_artifacts(tmp_path: Path) -> None:
    settings = FirecrackerSettings(
        firecracker_binary=tmp_path / "no-such-firecracker",
        kernel_image=tmp_path / "no-such-kernel",
        rootfs_image=tmp_path / "no-such-rootfs.ext4",
    )
    problems = settings.validate_for_real_boot()
    assert any("firecracker binary" in p for p in problems)
    assert any("kernel image" in p for p in problems)
    assert any("rootfs image" in p for p in problems)


def test_validate_for_real_boot_passes_when_artifacts_exist(tmp_path: Path) -> None:
    fc = tmp_path / "firecracker"
    fc.touch()
    kernel = tmp_path / "vmlinux"
    kernel.touch()
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.touch()
    settings = FirecrackerSettings(firecracker_binary=fc, kernel_image=kernel, rootfs_image=rootfs)
    assert settings.validate_for_real_boot() == []
