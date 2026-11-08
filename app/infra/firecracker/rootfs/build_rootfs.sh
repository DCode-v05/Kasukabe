#!/usr/bin/env bash

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
