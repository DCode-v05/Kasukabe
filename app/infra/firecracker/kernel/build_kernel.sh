#!/usr/bin/env bash

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
make olddefconfig

echo "== building vmlinux ($JOBS jobs) =="
make vmlinux -j"$JOBS"

cp vmlinux "$OUT_DIR/vmlinux"
echo "== done: $OUT_DIR/vmlinux =="
ls -lh "$OUT_DIR/vmlinux"
echo
echo "Production placement (RUNBOOK.md): copy to"
echo "  FirecrackerSettings.kernel_image default: /var/lib/kasukabe/kernel/vmlinux"
