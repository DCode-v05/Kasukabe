# RUNBOOK: microVM track, m5.metal host

Everything below runs on the genesis host itself (DESIGN.md section 9:
m5.metal spot, ap-south-1) - native Linux x86_64, real `/dev/kvm`. Nothing
in this track needed to block genesis (its own task description); this
is the checklist for when it's time to actually switch process-mode over
to microVM-mode.

## 0. Prerequisites on the host

```
sudo apt-get update
sudo apt-get install -y build-essential flex bison libssl-dev libelf-dev bc git \
    iproute2 e2fsprogs awscli
```

Get a `firecracker` binary (and `jailer` if `FC_USE_JAILER=true`) onto
the host - the upstream release binaries at
`https://github.com/firecracker-microvm/firecracker/releases` match the
kernel/API version this track was built against (Firecracker 1.x REST
API, `snapshot/create` + `snapshot/load` endpoints as used in
`api_client.py`). Place at `/usr/local/bin/firecracker` (matches
`FirecrackerSettings.firecracker_binary`'s default) or set
`FC_BINARY=/path/to/firecracker`.

Confirm KVM is actually usable before going further:

```
ls -l /dev/kvm
```

## 1. Build the kernel (once; minutes on this hardware)

```
app/infra/firecracker/kernel/build_kernel.sh /var/lib/kasukabe/kernel
```

Produces `/var/lib/kasukabe/kernel/vmlinux` - matches
`FirecrackerSettings.kernel_image`'s default exactly, no `FC_KERNEL_IMAGE`
override needed if you used this path.

## 2. Build the rootfs (once, or whenever engine/ source changes)

Run from a machine with Docker (can be this host, or built elsewhere and
copied over - the image is a plain file):

```
app/infra/firecracker/rootfs/build_rootfs.sh /var/lib/kasukabe/rootfs/engine-rootfs.ext4
```

Matches `FirecrackerSettings.rootfs_image`'s default. Re-run this any
time `engine/` changes and the running genesis needs the new code - a
fresh rootfs, not a patch, since the image ships read-only
(`is_read_only=True` in `vm_manager.py`'s `configure_drive` call; the
engine's own `/data` is where it writes anything, mounted as tmpfs at
boot by `rootfs/engine_init.py`, never persisted in the image itself).

## 3. Bring up host networking (once per host boot)

```
sudo app/infra/firecracker/network/bridge_setup.sh
```

Idempotent - safe to re-run. Confirm broker and world server (DESIGN.md
section 9) are listening on the bridge address
(`FirecrackerSettings.bridge_host_ip`, default `10.200.0.1`) and not just
`127.0.0.1`, or guests will reach the bridge but find nothing answering.

## 4. Point the genesis runner at this backend

Whatever config knob the genesis-runner track (`app/world/run_genesis.py`,
not owned here) exposes for `SUPERVISOR_BACKEND` - set it to
`firecracker`. Concretely, that means constructing the supervisor via:

```python
from config import FirecrackerSettings
from vm_manager import build_real_supervisor

settings = FirecrackerSettings.build_from_env()
supervisor = build_real_supervisor(settings)  # raises with every missing
                                               # artifact listed if steps
                                               # 1-2 were skipped
```

and passing `supervisor` wherever the genesis runner currently holds its
process-mode one - same `Supervisor` protocol either way
(`supervisor.py`), so nothing else about the genesis runner's own code
changes.

## 5. Verify on real hardware before trusting it with the live run

```
FIRECRACKER_REAL_HW=1 BROKER_TEST_KEY=<a real or test broker key> \
  pytest app/infra/firecracker/tests/test_real_firecracker_integration.py -v
```

This is the same boot-3/snapshot/restore/verify-state scenario
`tests/test_vm_manager_lifecycle.py` already runs against the fake
backend (32/32 passing as of this track's build - see README.md), now
against real `firecracker` + real KVM. Do not skip this step and assume
the fake-backed suite passing is sufficient: the fake proves the
orchestration logic is correct; this proves the actual boot works on
actual hardware, which nothing else in this track can prove from a Mac
dev host.

## 6. Genesis-day scale-up

`ResourcePool` (resources.py) already supports 250+ units without any
change - `cid_pool_size` defaults to 100,000 and `network_cidr`'s default
`10.200.0.0/20` gives over 4000 usable guest addresses. Before the real
250-agent run:

- Confirm `ulimit -n` (open files) on the host is high enough for 250 x
  (API socket + vsock UDS + several drive/log file descriptors each) -
  raise via `/etc/security/limits.conf` if needed.
- Confirm the host has 250 x `memory_mb` (per `UnitSpec`, default 256 MiB)
  of RAM headroom plus broker/world/game overhead - m5.metal's 384 GB
  covers this with wide margin at the DESIGN.md-documented population.
- Run `network/bridge_setup.sh` once, then let `VMManager.start()` create
  each unit's tap device itself as agents come up - do not pre-create tap
  devices with a separate script (see `tap_provisioner.py`'s module
  docstring for exactly why that would drift from the real allocation).

## Teardown

```
sudo app/infra/firecracker/network/teardown_network.sh
```

`VMManager.shutdown_all()` (called by the genesis runner on a clean stop)
already removes each unit's own tap device and stops each firecracker
process; this script is the backstop for whatever an unclean stop left
behind.
