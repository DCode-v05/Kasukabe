#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/kasukabe/app
CADDY_ENV="${APP_DIR}/infra/env/caddy.env"

if [ ! -f "${CADDY_ENV}" ]; then
	echo "missing ${CADDY_ENV} - copy caddy.env.example, fill in every REPLACE_ME, then re-run" >&2
	exit 1
fi
if grep -q REPLACE_ME "${CADDY_ENV}"; then
	echo "${CADDY_ENV} still has an unfilled REPLACE_ME placeholder" >&2
	exit 1
fi

sudo cp "${APP_DIR}/infra/Caddyfile" /etc/caddy/Caddyfile

sudo mkdir -p /etc/systemd/system/caddy.service.d
sudo tee /etc/systemd/system/caddy.service.d/override.conf >/dev/null <<EOF
[Service]
EnvironmentFile=${CADDY_ENV}
EOF

sudo chown root:root "${CADDY_ENV}"
sudo chmod 600 "${CADDY_ENV}"

sudo systemctl daemon-reload
sudo systemctl enable --now caddy
sudo systemctl restart caddy

echo "== status =="
sudo systemctl --no-pager status caddy || true

cat <<'EOF'

Check logs (Caddy logs its own ACME cert issuance attempts here):
  journalctl -u caddy -f

Verify from your OWN machine (not the instance) once DNS has propagated:
  curl -u operator:<your-plaintext-token> -I https://kasukabe.september.wtf/
  curl -u operator:<your-plaintext-token> -I https://console.kasukabe.september.wtf/

A 401 means Caddy is up but the credential is wrong - re-check the
plaintext token you hashed with `caddy hash-password` matches what you
are sending. A TLS handshake failure usually means DNS has not propagated
yet or port 80/443 is not reachable (check the security group).
EOF
