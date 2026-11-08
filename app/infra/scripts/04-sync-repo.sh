#!/usr/bin/env bash
# Kasukabe host-bringup, step 4 (RUNBOOK.md section 6).
#
# Run from the OPERATOR'S LOCAL MACHINE. /Vat/kasukabe has no git remote
# as of this writing (`git status` at the repo root fails with "not a
# git repository") - rsync straight from this checkout is the primary
# sync path today, not a fallback. Re-run this any time during the
# bring-up to push a hotfix; it is safe to run repeatedly (rsync only
# transfers diffs).
#
# Usage: PUBLIC_IP=<ip> KEY_NAME=<key> ./04-sync-repo.sh
set -euo pipefail

: "${PUBLIC_IP:?export PUBLIC_IP, see the output of 02-launch-instance.sh}"
: "${KEY_NAME:?export KEY_NAME (the same EC2 key pair name used to launch)}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/${KEY_NAME}.pem}"

echo "syncing ${REPO_ROOT} -> ubuntu@${PUBLIC_IP}:/opt/kasukabe/"

# Whole /Vat/kasukabe tree: app/, engine/ (the September engine each
# microVM runs directly, DESIGN.md section 4), september-sdk/, smallville/
# (the Ville tilemap assets world/map/ loads, and the reference frontend
# the game/ owner is porting from), and DESIGN.md itself. Excludes are
# everything that is either huge-and-regeneratable or host-specific.
rsync -avz --delete \
	--exclude '.venv/' \
	--exclude '__pycache__/' \
	--exclude '.pytest_cache/' \
	--exclude '.ruff_cache/' \
	--exclude '*.egg-info/' \
	--exclude 'node_modules/' \
	--exclude '.git/' \
	--exclude '*.pyc' \
	-e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=accept-new" \
	"${REPO_ROOT}/" "ubuntu@${PUBLIC_IP}:/opt/kasukabe/"

echo "done. Next: ssh in and run app/infra/scripts/05-deploy-services.sh"
