#!/usr/bin/env bash
# UFW Firewall Setup — Engine Production VPS
#
# Only ports 80 and 443 are open externally (nginx).
# Everything else (engine port) is internal only.
#
# Usage: sudo ./scripts/firewall.sh

set -euo pipefail

echo "[$(date)] Configuring firewall..."

# Reset to defaults
ufw default deny incoming
ufw default allow outgoing

# SSH (adjust port if you use a non-standard SSH port)
ufw allow 22/tcp comment "SSH"

# HTTP and HTTPS (nginx)
ufw allow 80/tcp comment "HTTP (redirect to HTTPS)"
ufw allow 443/tcp comment "HTTPS (nginx)"

# Explicitly deny internal service ports from external access.
# These are already denied by the default policy, but explicit rules
# make the intent clear and survive default policy changes.
ufw deny from any to any port 5432 comment "PostgreSQL — internal only"
ufw deny from any to any port 6379 comment "Redis — internal only"
ufw deny from any to any port 8000 comment "Orchestrator — internal only"
ufw deny from any to any port 9001:9999/tcp comment "Engine ports — internal only"

# Enable firewall
ufw --force enable

echo ""
ufw status verbose
echo ""
echo "[$(date)] Firewall configured."
