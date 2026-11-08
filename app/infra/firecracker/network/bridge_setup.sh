#!/usr/bin/env bash
# Host-level, run-once network setup for the genesis run: the bridge every
# per-unit tap device (tap_provisioner.py, created individually by
# VMManager.start()/restore() as each unit boots - see that module's
# docstring for why per-unit creation is NOT scripted here) attaches to.
#
# Deliberately NOT per-unit and NOT Python: unlike tap device names
# (derived from a hash of unit_id, so only VMManager - the thing that
# already computed that hash - can know them), the bridge itself is one
# static resource for the whole host, config known up front, exactly the
# kind of thing app/infra/README.md already describes this directory as
# ("shell scripts and deploy tooling"). Idempotent: safe to re-run after
# a host reboot or a genesis restart.
#
# The values below (name, address, prefix) are FirecrackerSettings'
# defaults (config.py: bridge_name, bridge_host_ip, network_cidr) spelled
# out in bash since this script runs before any Python process exists to
# read them from. If you change one in config.py's FirecrackerSettings
# defaults or via FC_BRIDGE_NAME/FC_BRIDGE_HOST_IP/FC_NETWORK_CIDR, change
# it here too - RUNBOOK.md's pre-genesis checklist calls this out
# explicitly as the one place that can drift.
#
# Usage (as root, on the m5.metal host): network/bridge_setup.sh

set -euo pipefail

BRIDGE_NAME="${FC_BRIDGE_NAME:-fcbr0}"
BRIDGE_ADDR="${FC_BRIDGE_HOST_IP:-10.200.0.1}"
BRIDGE_PREFIX="${FC_BRIDGE_PREFIX:-20}"  # matches network_cidr's default 10.200.0.0/20

if [ "$(id -u)" -ne 0 ]; then
  echo "bridge_setup.sh must run as root (creates a network device)" >&2
  exit 1
fi

if ! ip link show "$BRIDGE_NAME" >/dev/null 2>&1; then
  echo "== creating bridge $BRIDGE_NAME =="
  ip link add name "$BRIDGE_NAME" type bridge
else
  echo "== bridge $BRIDGE_NAME already exists, reusing =="
fi

if ! ip addr show dev "$BRIDGE_NAME" | grep -q "$BRIDGE_ADDR/"; then
  ip addr add "$BRIDGE_ADDR/$BRIDGE_PREFIX" dev "$BRIDGE_NAME"
fi
ip link set "$BRIDGE_NAME" up

# Guests reach the broker (CDT_URL) and world server by IP address
# directly on this bridge - the host's own network stack answers
# connections to $BRIDGE_ADDR:8100/8200 the moment those services bind
# to it, no NAT or forwarding needed for that path (DESIGN.md section 9:
# broker and world run ON this same metal host). ip_forward is still
# enabled here, off by the kernel default, in case a future need arises
# for guest-to-guest or guest-to-internet traffic; it costs nothing to
# have on and DESIGN.md does not rule either out.
sysctl -w net.ipv4.ip_forward=1 >/dev/null

echo "== $BRIDGE_NAME up at $BRIDGE_ADDR/$BRIDGE_PREFIX, ip_forward=1 =="
ip -brief link show "$BRIDGE_NAME"
ip -brief addr show "$BRIDGE_NAME"
