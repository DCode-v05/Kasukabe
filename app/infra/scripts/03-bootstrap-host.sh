#!/usr/bin/env bash
# Kasukabe host-bringup, step 3 (RUNBOOK.md section 6).
#
# Run ON THE m5.metal INSTANCE itself (scp this file up, or copy/paste
# once the repo is synced - see RUNBOOK.md section 6 for both paths):
#   ssh -i ~/.ssh/<key>.pem ubuntu@<public-ip> 'bash -s' < app/infra/scripts/03-bootstrap-host.sh
#
# Installs everything the host needs BEFORE the repo is synced: system
# packages, the `kasukabe` service user, uv (installed system-wide so
# every user including `kasukabe` can see it), Caddy, and Litestream.
# Idempotent - safe to re-run if a step fails partway.
set -euo pipefail

# Pinned to a known-good release rather than "latest" so a live run never
# depends on GitHub's API at the wrong moment. This was the newest stable
# tag as of this script's authoring - check
# https://github.com/benbjohnson/litestream/releases before the real run
# and bump if a newer one exists; do not blindly trust this pin is
# current months later.
LITESTREAM_VERSION="0.3.13"

echo "== apt update/upgrade =="
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

echo "== base packages =="
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
	build-essential git rsync curl ca-certificates gnupg unzip jq sqlite3 \
	debian-keyring debian-archive-keyring apt-transport-https \
	qemu-kvm bridge-utils iproute2

# m5.metal is bare metal specifically so /dev/kvm is real hardware
# virtualization, not nested (Firecracker's requirement) - this only
# checks it is present, it does not configure any microVM networking or
# rootfs (app/infra/firecracker/ owns that).
if [ -e /dev/kvm ]; then
	echo "/dev/kvm present - good, Firecracker track can proceed"
else
	echo "WARNING: /dev/kvm not found - confirm this instance actually launched as m5.metal, not m5.xlarge or similar" >&2
fi

echo "== kasukabe service user =="
sudo groupadd -f kasukabe
id -u kasukabe >/dev/null 2>&1 || sudo useradd -g kasukabe -m -s /usr/sbin/nologin kasukabe
sudo usermod -aG kvm kasukabe || true

echo "== /opt/kasukabe layout =="
sudo mkdir -p /opt/kasukabe/data
# Owned by the SSH admin user for now so rsync (RUNBOOK.md section 6) can
# write without sudo; 05-deploy-services.sh chowns app/ to kasukabe:kasukabe
# right before installing the venv and starting services.
sudo chown -R "$(whoami)":kasukabe /opt/kasukabe
sudo chmod 750 /opt/kasukabe
sudo chown kasukabe:kasukabe /opt/kasukabe/data
sudo chmod 750 /opt/kasukabe/data

echo "== uv (system-wide, so the kasukabe user sees it too) =="
if ! command -v uv >/dev/null 2>&1; then
	curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
uv --version

echo "== python 3.12 via uv =="
sudo -u kasukabe env HOME=/home/kasukabe uv python install 3.12 || uv python install 3.12

echo "== Caddy (official apt repo) =="
if ! command -v caddy >/dev/null 2>&1; then
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		| sudo tee /etc/apt/sources.list.d/caddy-stable.list
	sudo apt-get update -y
	sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi
caddy version

echo "== Litestream v${LITESTREAM_VERSION} =="
if ! command -v litestream >/dev/null 2>&1; then
	curl -LsSf "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.tar.gz" \
		-o /tmp/litestream.tar.gz
	sudo tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream
	rm /tmp/litestream.tar.gz
fi
litestream version

echo "== done: apt packages, kasukabe user, uv, python 3.12, caddy, litestream all installed =="
echo "next: sync the repo (RUNBOOK.md section 6), then run 05-deploy-services.sh"
