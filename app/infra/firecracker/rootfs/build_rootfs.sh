#!/usr/bin/env bash
# Builds the ext4 rootfs image Firecracker boots: the September engine and
# every one of its Python dependencies baked in, no Docker inside the
# microVM (DESIGN.md, this track's own task).
#
# Three stages, each for a concrete reason:
#
# 1. `docker build` (Dockerfile.rootfs) assembles the actual filesystem
#    tree - engine deps installed, engineuser/enginegroup created,
#    engine-init put in place - using engine/Dockerfile's own base image
#    and bubblewrap install so this never drifts from what engine/ already
#    ships and tests against.
#
# 2. `docker export` turns that container's filesystem into a plain tar
#    stream. This is the ONLY way to get a real Linux filesystem tree out
#    of a Docker Desktop for Mac build without volume-mounting the host
#    (which would just re-create the "no Linux filesystem here" problem
#    one level up).
#
# 3. A second, throwaway `debian:bookworm-slim` container extracts that
#    tar AS ROOT and runs `mkfs.ext4 -d <dir> ...` in the SAME container,
#    in the same step, with no intermediate extraction onto this Mac's
#    host filesystem. That ordering is deliberate, not incidental: `tar
#    -x` run as a non-root user (which extracting straight onto this Mac
#    would be) silently drops any uid/gid it doesn't own, which would
#    silently turn every engineuser-owned (1000:1000) file into
#    something else before mkfs.ext4 -d ever reads it. mkfs.ext4 -d
#    itself needs no loop device, no `mount`, no --privileged: it is a
#    plain userspace tool that reads a directory tree and writes ext4
#    metadata straight into the target file, which is exactly what makes
#    this whole pipeline possible without a Linux host or root on this Mac.
#
# Usage: app/infra/firecracker/rootfs/build_rootfs.sh [output_path]
# Default output: app/infra/firecracker/rootfs/build/engine-rootfs.ext4
# Production output belongs at FirecrackerSettings.rootfs_image's default,
# /var/lib/kasukabe/rootfs/engine-rootfs.ext4 on the metal host
# (RUNBOOK.md has the exact genesis-day invocation).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KASUKABE_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
OUT_IMAGE="${1:-$SCRIPT_DIR/build/engine-rootfs.ext4}"
IMAGE_TAG="kasukabe-fc-rootfs-fs:local"

mkdir -p "$(dirname "$OUT_IMAGE")"
OUT_DIR="$(cd "$(dirname "$OUT_IMAGE")" && pwd)"
OUT_NAME="$(basename "$OUT_IMAGE")"

echo "== building rootfs filesystem image (engine + deps baked in) =="
docker build \
  --platform linux/amd64 \
  -f "$SCRIPT_DIR/Dockerfile.rootfs" \
  -t "$IMAGE_TAG" \
  "$KASUKABE_ROOT"

echo "== exporting container filesystem to a tar stream =="
CONTAINER_ID="$(docker create --platform linux/amd64 "$IMAGE_TAG")"
TAR_PATH="$(mktemp -t kasukabe-rootfs-XXXXXX.tar)"
trap 'rm -f "$TAR_PATH"; docker rm "$CONTAINER_ID" >/dev/null 2>&1 || true' EXIT

docker export "$CONTAINER_ID" -o "$TAR_PATH"
docker rm "$CONTAINER_ID" >/dev/null
CONTAINER_ID=""

echo "== extracting as root and building ext4 image (mkfs.ext4 -d, no loop device needed) =="
docker run --rm --platform linux/amd64 \
  -v "$TAR_PATH:/rootfs-content.tar:ro" \
  -v "$OUT_DIR:/out" \
  debian:bookworm-slim \
  bash -eu -c '
    apt-get update -qq && apt-get install -y -qq --no-install-recommends e2fsprogs >/dev/null
    mkdir -p /rootfs-extracted
    tar -xf /rootfs-content.tar -C /rootfs-extracted
    # Mount points the kernel/init need but nothing populates at build
    # time (engine_init.py os.makedirs()s these too, at boot, as a
    # second safety net - both exist because a rootfs image is easier to
    # debug when the mount targets are visibly present, not because init
    # actually depends on it).
    mkdir -p /rootfs-extracted/proc /rootfs-extracted/sys /rootfs-extracted/dev \
             /rootfs-extracted/data /rootfs-extracted/tmp
    size_mb=$(( $(du -sm /rootfs-extracted | cut -f1) + 150 ))
    echo "rootfs content: $(du -sh /rootfs-extracted | cut -f1), image size: ${size_mb}M"
    mkfs.ext4 -q -d /rootfs-extracted -r 1 -N 0 -m 0 -L kasukaberoot -F "/out/'"$OUT_NAME"'" "${size_mb}M"
  '

echo "== verifying the image without mounting it (debugfs, read-only) =="
docker run --rm --platform linux/amd64 \
  -v "$OUT_DIR:/out:ro" \
  debian:bookworm-slim \
  bash -eu -c '
    apt-get update -qq && apt-get install -y -qq --no-install-recommends e2fsprogs >/dev/null
    echo "--- root listing ---"
    debugfs -R "ls -l /" "/out/'"$OUT_NAME"'"
    echo "--- engine-init present and executable ---"
    debugfs -R "stat /usr/local/sbin/engine-init" "/out/'"$OUT_NAME"'" | grep -E "Inode|Mode"
    echo "--- src/server.py present (the engine app module) ---"
    debugfs -R "stat /app/src/server.py" "/out/'"$OUT_NAME"'" | grep Inode
  '

echo "== done: $OUT_IMAGE =="
ls -lh "$OUT_IMAGE"
