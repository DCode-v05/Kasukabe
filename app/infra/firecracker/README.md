# app/infra/firecracker/

The microVM track (DESIGN.md section 9): a rootfs with the September
engine baked in, a minimal kernel, a VM manager that boots N microVMs and
snapshots/restores them to S3, and a `Supervisor` interface identical in
shape to whatever the process-mode supervisor the genesis runner uses
today exposes - so switching `SUPERVISOR_BACKEND` from `process` to
`firecracker` is a config change, not a rewrite. Per this track's own
task: **does not block genesis**, lands independently.

## What's real here, and what needs a Linux host to finish

This was built on a Mac (Apple Silicon, no `/dev/kvm`, confirmed absent;
no `qemu-system-*`, no `firecracker`, no `multipass`/`lima` on this
machine either). Firecracker needs KVM to boot anything - that is a
hardware fact, not a gap in this track. So, honestly, by piece:

| Piece | Status |
|---|---|
| `supervisor.py` - the `Supervisor` protocol, `UnitSpec`/`UnitStatus`/`SnapshotHandle` | Real, complete |
| `api_client.py` - async Firecracker REST client over a unix socket | Real, complete |
| `vm_manager.py` - `VMManager` (start/stop/restart/status/list/health/snapshot/restore/shutdown_all) | Real, complete |
| `resources.py` - deterministic per-unit vsock CID / guest IP / tap name / socket paths | Real, complete |
| `tap_provisioner.py` - per-unit tap device create/remove | Real, complete (`LinuxTapProvisioner`); needs CAP_NET_ADMIN on a real Linux host to actually run |
| `snapshot_store.py` - S3 (via `aws` CLI, not boto3 - see its own docstring) + local filesystem stores | Real, complete |
| `network/bridge_setup.sh`, `network/teardown_network.sh` | Real, complete; needs root on a real Linux host to run |
| `rootfs/` - engine + deps baked into a real ext4 image | **Built and verified for real** in this session (see below) - a 353 MB `engine-rootfs.ext4` with `/usr/local/sbin/engine-init` and the full engine source tree, confirmed via `debugfs` without mounting |
| `kernel/` - guest kernel config + build script | Config is the real, vendored upstream Firecracker CI config; **not compiled in this session** - see `kernel/README.md` for the specific reason (compiling under QEMU emulation on this arm64 Mac is a multi-hour job; the m5.metal host is native x86_64 and compiles it in minutes) |
| `process.py`'s `FakeFirecrackerProcessLauncher` | Real FastAPI app on a real unix socket, enforcing Firecracker's actual call-ordering - see its own docstring for exactly what it does and does not prove |
| Booting an actual microVM under KVM | **Not exercised anywhere in this track.** `tests/test_real_firecracker_integration.py` is the scenario, gated on `FIRECRACKER_REAL_HW=1` + `/dev/kvm`, skipped everywhere this was built. RUNBOOK.md's checklist is what closes this gap on the metal host before genesis. |

Everything in this repo's own test suite (32 tests, 1 real-hardware test
correctly skipped) runs against `FakeFirecrackerProcessLauncher`, not a
plain mock that always returns success: it is a real FastAPI server on a
real Unix socket implementing Firecracker's actual phase state machine
(reject `InstanceStart` before `machine-config`, reject
`snapshot/create` before `Paused`, etc.), so a call-ordering bug in
`vm_manager.py` fails the same way it would against real Firecracker.
What it structurally cannot exercise is anything below the HTTP API:
KVM, actual kernel boot, actual guest-side init behavior. See
`process.py`'s module docstring for the same point in more detail.

## Running the tests

Self-contained, not wired into `app/docker-compose.yml`'s shared `test`
service - see `Dockerfile.test`'s header comment for why (that file is
out of this track's lane while the sibling process-supervisor track
edits it concurrently).

```
cd app/infra/firecracker
docker compose build test
docker compose run --rm test
```

32 passed, 1 skipped, ~4s, last verified while writing this file.

## Building the rootfs for real

```
app/infra/firecracker/rootfs/build_rootfs.sh
```

Produces `rootfs/build/engine-rootfs.ext4`. No loop device, no `mount`,
no `--privileged` - `mkfs.ext4 -d <dir>` builds the image directly from a
directory tree in userspace; see `build_rootfs.sh`'s own header comment
for exactly why the extraction step also has to run as root inside a
throwaway container rather than on this Mac's host filesystem (uid/gid
preservation for `engineuser`).

## Building the kernel

Needs a native Linux x86_64 machine (or accept a multi-hour QEMU-emulated
build) - see `kernel/README.md`. On the m5.metal host:

```
app/infra/firecracker/kernel/build_kernel.sh
```

## The Supervisor contract

`supervisor.Supervisor` is what `app/world/run_genesis.py` (the sibling
genesis-runner track, not owned here) is expected to program against:
`start`, `stop`, `restart`, `status`, `list`, `health`, `snapshot`,
`restore`, `shutdown_all`, all async, all keyed by a stable `unit_id`.
`vm_manager.VMManager` is this track's implementation of it.
`tests/test_supervisor_protocol.py` checks `VMManager` satisfies the
protocol structurally (`isinstance(manager, Supervisor)`, every method a
coroutine function) so a signature drifting out of sync between this
track and the process-mode supervisor fails at test time, not at genesis
boot.

Build a real supervisor with `vm_manager.build_real_supervisor(settings)`
- the one call site that ever selects `RealFirecrackerProcessLauncher`
and `LinuxTapProvisioner`; every test in this track instead constructs
`VMManager` directly against the fakes, so a test can never accidentally
boot a real microVM and this function can never accidentally be
exercised by a fake.

## Config

`config.FirecrackerSettings.build_from_env()` reads `FC_*` env vars
(defaults documented inline in `config.py`), aggregating every problem
into one `ConfigError` instead of failing on the first
(`app/CONVENTIONS.md`'s idiom, matching `engine/src/config.py`).
`settings.validate_for_real_boot()` is the separate check for "does the
firecracker binary / kernel image / rootfs image actually exist on disk"
- kept apart from `build_from_env()` so unit tests can build a
`FirecrackerSettings` with none of those present, which is every
environment except the metal host until the build steps above have run
there.

See RUNBOOK.md for the exact genesis-day sequence.
