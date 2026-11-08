#!/usr/bin/env bash
# Builds the Firecracker guest kernel (vmlinux) from
# microvm-kernel-6.1.config, following the exact manual recipe
# Firecracker's own docs/rootfs-and-kernel-setup.md documents (see
# kernel/README.md for the fetched excerpt and source URL).
#
# NOT run as part of this track's build/test pass - see kernel/README.md
# for exactly why (short version: compiling needs no KVM, but this dev
# host is Apple Silicon and would cross-compile x86_64 under QEMU
# emulation, which is a multi-hour job for a kernel; the real target,
# the m5.metal genesis host, is native x86_64 and compiles this in
# minutes). This script is what RUNBOOK.md's pre-genesis checklist runs
# there, not a placeholder - every step below is the same recipe
# Firecracker's docs prescribe, pinned to the same kernel point release
# (v6.1.167) the vendored config file was generated from.
#
# Usage (on a native Linux x86_64 builder, e.g. the m5.metal host itself,
# or any Linux x86_64 CI runner):
#   app/infra/firecracker/kernel/build_kernel.sh [output_dir]
# Default output_dir: app/infra/firecracker/kernel/build/
#
# Usage via Docker (portable, but slow under QEMU emulation on a
# non-x86_64 host - matches the platform: linux/amd64 pin the rest of
# this repo already uses for the same production-parity reason):
#   docker run --rm --platform linux/amd64 \
#     -v "$(pwd)/app/infra/firecracker/kernel:/kernel" \
#     -w /kernel debian:bookworm-slim bash build_kernel.sh /kernel/build

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${1:-$SCRIPT_DIR/build}"
KERNEL_TAG="v6.1.167"
KERNEL_REPO="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"
SRC_DIR="$SCRIPT_DIR/build/linux-src"
JOBS="${JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

mkdir -p "$OUT_DIR"

if ! command -v gcc >/dev/null || ! command -v flex >/dev/null || ! command -v bison >/dev/null; then
  echo "missing build tools. On Debian/Ubuntu:" >&2
  echo "  apt-get update && apt-get install -y build-essential flex bison libssl-dev libelf-dev bc git" >&2
  exit 1
fi

if [ ! -d "$SRC_DIR" ]; then
  echo "== cloning linux $KERNEL_TAG (shallow) =="
  git clone --depth 1 --branch "$KERNEL_TAG" "$KERNEL_REPO" "$SRC_DIR"
fi

echo "== applying microvm-kernel-6.1.config =="
cp "$SCRIPT_DIR/microvm-kernel-6.1.config" "$SRC_DIR/.config"

cd "$SRC_DIR"
# olddefconfig reconciles the vendored config against this exact checkout
# (new config symbols introduced since the config was generated get their
# default; nothing in the vendored file is dropped silently - `make
# olddefconfig` warns on stdout for anything it had to decide, which is
# why this script does not redirect that output).
make olddefconfig

echo "== building vmlinux ($JOBS jobs) =="
make vmlinux -j"$JOBS"

cp vmlinux "$OUT_DIR/vmlinux"
echo "== done: $OUT_DIR/vmlinux =="
ls -lh "$OUT_DIR/vmlinux"
echo
echo "Production placement (RUNBOOK.md): copy to"
echo "  FirecrackerSettings.kernel_image default: /var/lib/kasukabe/kernel/vmlinux"
