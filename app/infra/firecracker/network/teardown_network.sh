#!/usr/bin/env bash
# Reverses bridge_setup.sh: removes every fctap* device still attached to
# the bridge (a genesis run that ended uncleanly can leave some - VMManager
# normally removes its own via tap_provisioner.py's remove_tap() inside
# stop()/shutdown_all(), this is the backstop for when it didn't get the
# chance to) and the bridge itself.
#
# Usage (as root, on the m5.metal host): network/teardown_network.sh

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
