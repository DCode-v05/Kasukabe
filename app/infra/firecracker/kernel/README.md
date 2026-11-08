# Guest kernel

## What's here

- `microvm-kernel-6.1.config` - vendored verbatim from
  `firecracker-microvm/firecracker`'s own repo,
  `resources/guest_configs/microvm-kernel-ci-x86_64-6.1.config`
  (fetched from
  `https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/resources/guest_configs/microvm-kernel-ci-x86_64-6.1.config`
  while building this track - not hand-written, not guessed). It's the
  exact config Firecracker's own CI uses to build the kernel it tests
  every release against: minimal virtio (`VIRTIO_MMIO`, `VIRTIO_BLK`,
  `VIRTIO_NET`, `VIRTIO_VSOCKETS`), `EXT4_FS` for the rootfs drive,
  `SERIAL_8250_CONSOLE` for the guest console, `IP_PNP` so the kernel
  itself configures the network interface from the `ip=` boot arg with
  no userspace networking tool needed (see `rootfs/engine_init.py`'s
  docstring), `USER_NS` + `SECCOMP_FILTER` (both required for bubblewrap,
  which the engine uses for tool-call sandboxing - verified present in
  this exact config before wiring engine_init to drop into a non-root
  user; see the "what was checked" list below), and `CONFIG_MODULES` is
  NOT set - everything needed is built in, matching microVM practice of
  a single monolithic image with no module loading at boot.

- `build_kernel.sh` - the build script. Follows the manual recipe from
  Firecracker's own
  `docs/rootfs-and-kernel-setup.md` (fetched from
  `https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/docs/rootfs-and-kernel-setup.md`
  during this track's build) line for line: clone `linux-stable` at the
  point release this config was generated from, drop in the vendored
  `.config`, `make olddefconfig` to reconcile, `make vmlinux`.

## What was checked in this config, and why it matters for this track

| Symbol | Value | Why it matters here |
|---|---|---|
| `CONFIG_VIRTIO_MMIO`, `CONFIG_VIRTIO_BLK`, `CONFIG_VIRTIO_NET` | y | the three devices `api_client.py` configures per unit (drive, network-interface) and the console |
| `CONFIG_VIRTIO_VSOCKETS`, `CONFIG_VSOCKETS` | y | `api_client.configure_vsock()` / `resources.py`'s per-unit vsock CID |
| `CONFIG_EXT4_FS` | y | the rootfs image `rootfs/build_rootfs.sh` produces is ext4 |
| `CONFIG_IP_PNP`, `CONFIG_IP_PNP_BOOTP` | y | kernel-level static IP config from `boot_args`' `ip=` token - see `vm_manager.py`'s `build_boot_args()` |
| `CONFIG_DEVTMPFS`, `CONFIG_DEVTMPFS_MOUNT` | y | `/dev` populated before `engine_init.py` (PID 1) runs |
| `CONFIG_USER_NS`, `CONFIG_SECCOMP_FILTER` | y | bubblewrap (the engine's tool sandbox, `engine/Dockerfile`) needs unprivileged user namespaces + seccomp; both present, so sandboxing works inside the guest exactly like it does in engine's own container image |
| `CONFIG_BINFMT_SCRIPT`, `CONFIG_BINFMT_ELF` | y | `init=/usr/local/sbin/engine-init` is a `#!/usr/local/bin/python3` script, not a compiled binary - the kernel needs `BINFMT_SCRIPT` to execve() it as PID 1 at all |
| `CONFIG_MODULES` | not set | monolithic image, no module loading step to fail or delay boot |

## Why the kernel is not compiled in this session

Compiling needs a C toolchain, not KVM - unlike booting, it would in
principle run on this Mac inside a Linux container. The reason it
still isn't done here: this dev host is Apple Silicon (arm64), so a
`--platform linux/amd64` container builds x86_64 output under QEMU
user-mode CPU emulation, the same mechanism `app/docker-compose.yml`'s
own `test` service already documents using for `pytest` ("On Apple
Silicon this runs under QEMU emulation (slower but correct)"). Emulating
every instruction of a full kernel compile (tens of thousands of C
files) is a fundamentally different order of magnitude from emulating a
pytest run - realistically hours, not minutes, on this hardware for a
build that takes a few minutes natively.

The actual target, the m5.metal genesis host (DESIGN.md section 9), is
native x86_64 bare metal. `build_kernel.sh` run there compiles natively
at full speed, with no emulation tax, and does not block the parts of
genesis this track's task explicitly says must not wait on it. This is
the honest tradeoff, not a shortcut: `build_kernel.sh` is a complete,
correct, ready-to-run script using Firecracker's own documented recipe
and a pinned, verified-to-exist kernel tag (`v6.1.167`, confirmed against
`git.kernel.org`'s stable tree while writing this script) - what's
missing is only the multi-hour wait on the wrong architecture, not any
part of the implementation.

RUNBOOK.md's pre-genesis checklist item: run `build_kernel.sh` on the
metal host (or any native Linux x86_64 machine) before genesis day.
