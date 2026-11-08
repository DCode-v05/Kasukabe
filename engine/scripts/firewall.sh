#!/usr/bin/env bash

set -euo pipefail

echo "[$(date)] Configuring firewall..."

ufw default deny incoming
ufw default allow outgoing

ufw allow 22/tcp comment "SSH"

ufw allow 80/tcp comment "HTTP (redirect to HTTPS)"
ufw allow 443/tcp comment "HTTPS (nginx)"

ufw deny from any to any port 5432 comment "PostgreSQL — internal only"
ufw deny from any to any port 6379 comment "Redis — internal only"
ufw deny from any to any port 8000 comment "Orchestrator — internal only"
ufw deny from any to any port 9001:9999/tcp comment "Engine ports — internal only"

ufw --force enable

echo ""
ufw status verbose
echo ""
echo "[$(date)] Firewall configured."
