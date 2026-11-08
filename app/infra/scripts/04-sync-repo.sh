#!/usr/bin/env bash
set -euo pipefail

: "${PUBLIC_IP:?export PUBLIC_IP, see the output of 02-launch-instance.sh}"
: "${KEY_NAME:?export KEY_NAME (the same EC2 key pair name used to launch)}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/${KEY_NAME}.pem}"

echo "syncing ${REPO_ROOT} -> ubuntu@${PUBLIC_IP}:/opt/kasukabe/"

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
