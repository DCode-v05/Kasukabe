#!/usr/bin/env bash

set -euo pipefail

BRIDGE_NAME="${FC_BRIDGE_NAME:-fcbr0}"
TAP_PREFIX="${FC_TAP_PREFIX:-fctap}"

if [ "$(id -u)" -ne 0 ]; then
  echo "teardown_network.sh must run as root" >&2
  exit 1
fi

if ip link show "$BRIDGE_NAME" >/dev/null 2>&1; then
  echo "== removing leftover ${TAP_PREFIX}* devices =="
  for dev in $(ip -brief link show | awk '{print $1}' | grep "^${TAP_PREFIX}" || true); do
    echo "  deleting $dev"
    ip link delete "$dev" 2>/dev/null || true
  done

  echo "== removing bridge $BRIDGE_NAME =="
  ip link delete "$BRIDGE_NAME"
else
  echo "== bridge $BRIDGE_NAME does not exist, nothing to do =="
fi
