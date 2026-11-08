#!/usr/bin/env bash

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

sysctl -w net.ipv4.ip_forward=1 >/dev/null

echo "== $BRIDGE_NAME up at $BRIDGE_ADDR/$BRIDGE_PREFIX, ip_forward=1 =="
ip -brief link show "$BRIDGE_NAME"
ip -brief addr show "$BRIDGE_NAME"
