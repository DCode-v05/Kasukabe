#!/usr/bin/env bash
# Kasukabe host-bringup, step 5 (RUNBOOK.md section 7).
#
# Run ON THE m5.metal INSTANCE, after 03-bootstrap-host.sh and after the
# repo has been synced to /opt/kasukabe (04-sync-repo.sh) AND after you
# have copied app/infra/env/*.env.example -> *.env and filled in every
# REPLACE_ME (RUNBOOK.md section 7 walks through each one). This script
# refuses to proceed if any *.env file is still missing - it will NOT
# silently start services with example placeholders.
#
# Installs the venv, installs the four systemd units (world/broker/game/
# console - NOT litestream, see RUNBOOK.md section 10 for why that one is
# separate), and starts them.
set -euo pipefail

APP_DIR=/opt/kasukabe/app
ENV_DIR="${APP_DIR}/infra/env"

for svc in world broker game console; do
	if [ ! -f "${ENV_DIR}/${svc}.env" ]; then
		echo "missing ${ENV_DIR}/${svc}.env - copy ${svc}.env.example, fill in every REPLACE_ME, then re-run this script" >&2
		exit 1
	fi
	if grep -q REPLACE_ME "${ENV_DIR}/${svc}.env"; then
		echo "${ENV_DIR}/${svc}.env still has an unfilled REPLACE_ME placeholder - fill it in before starting services" >&2
		exit 1
	fi
done

echo "== venv (as kasukabe user) =="
sudo chown -R kasukabe:kasukabe /opt/kasukabe/app
sudo -u kasukabe bash -c "cd ${APP_DIR} && uv sync --python 3.12"

echo "== env file permissions =="
sudo chown kasukabe:kasukabe "${ENV_DIR}"/*.env
sudo chmod 600 "${ENV_DIR}"/*.env

echo "== systemd units =="
sudo cp "${APP_DIR}/infra/systemd/kasukabe-world.service" /etc/systemd/system/
sudo cp "${APP_DIR}/infra/systemd/kasukabe-broker.service" /etc/systemd/system/
sudo cp "${APP_DIR}/infra/systemd/kasukabe-game.service" /etc/systemd/system/
sudo cp "${APP_DIR}/infra/systemd/kasukabe-console.service" /etc/systemd/system/
sudo systemctl daemon-reload

echo "== enable + start (world first - broker/game/console all depend on it) =="
sudo systemctl enable --now kasukabe-world
sleep 2
sudo systemctl enable --now kasukabe-broker kasukabe-game kasukabe-console

echo "== status =="
sudo systemctl --no-pager status kasukabe-world kasukabe-broker kasukabe-game kasukabe-console || true

cat <<'EOF'

Check logs with:
  journalctl -u kasukabe-world -f
  journalctl -u kasukabe-broker -f
  journalctl -u kasukabe-game -f
  journalctl -u kasukabe-console -f

Verify health (from the instance):
  curl -s localhost:8100/health
  curl -s localhost:8200/health
  curl -s localhost:8400/health
  curl -s localhost:8300/health

Next: 06-configure-caddy.sh (RUNBOOK.md section 6) to put the two
services behind TLS + token gating on the public subdomains.
EOF
