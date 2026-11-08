#!/usr/bin/env python3
"""Mint per-agent broker bearer tokens (RUNBOOK.md section 9).

broker/auth.py's own module docstring says this in so many words: "infra/'s
job to mint these at agent-provisioning time." This script is that job -
deliberately a standalone stdlib-only script (no `import broker`, matching
infra/README.md: "Not a Python package... never imported by world/, broker/,
game/, or research/; it drives them from the outside") so it never needs
the app/ venv, just a bare `python3`. It reproduces
broker.auth.sign_agent_token()'s exact algorithm (HMAC-SHA256 of the agent
id, hex digest, "<agent_id>.<hex-hmac>") byte for byte - if that function
ever changes, this script's SIGN_AGENT_TOKEN block below must change with
it, since the two are not shared code by design.

Reads agent ids from world/genesis.json (world/'s genesis generator has
already produced 250 real agent records there, each with an "agent_id"
field like "a0039" - see that file). Writes one CSV row per agent:
agent_id,token - this is the exact interface app/infra/firecracker/
(the microVM provisioning track) should consume to set each guest's
CDT_KEY at boot (DESIGN.md section 9: "herald base_url -> broker",
broker/auth.py's module docstring: "each agent's microVM is provisioned
with CDT_URL pointed at broker and CDT_KEY set to that agent's broker
credential").

Usage:
    python3 07-mint-agent-tokens.py \
        --secret "$(grep AUTH_SHARED_SECRET env/broker.env | cut -d= -f2)" \
        --genesis ../world/genesis.json \
        --out /opt/kasukabe/data/agent-tokens.csv

The output file contains live credentials - chmod 600 it (this script
does so itself) and never commit it, log it, or paste it into a chat.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path


def sign_agent_token(agent_id: str, secret: str) -> str:
    """Byte-for-byte port of broker.auth.sign_agent_token - see module
    docstring for why this is duplicated rather than imported."""
    mac = hmac.new(secret.encode("utf-8"), agent_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{agent_id}.{mac}"


def load_agent_ids(genesis_path: Path) -> list[str]:
    data = json.loads(genesis_path.read_text(encoding="utf-8"))
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise SystemExit(f"{genesis_path}: no 'agents' array found - is this world/genesis.json?")
    ids = [a["agent_id"] for a in agents if "agent_id" in a]
    if len(ids) != len(agents):
        raise SystemExit(f"{genesis_path}: some agent records are missing 'agent_id'")
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{genesis_path}: duplicate agent_id values found")
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secret",
        default=os.environ.get("AUTH_SHARED_SECRET", ""),
        help="must match broker.env's AUTH_SHARED_SECRET exactly (or read from that env var)",
    )
    parser.add_argument(
        "--genesis",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "world" / "genesis.json",
        help="path to world/genesis.json (default: ../../world/genesis.json relative to this script)",
    )
    parser.add_argument("--out", type=Path, required=True, help="output CSV path (agent_id,token)")
    args = parser.parse_args()

    if not args.secret:
        print(
            "no --secret given and AUTH_SHARED_SECRET is unset - refusing to mint unsigned "
            "tokens for a production run (broker.env should have a real secret; see "
            "env/broker.env.example)",
            file=sys.stderr,
        )
        return 1

    agent_ids = load_agent_ids(args.genesis)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["agent_id", "token"])
        for agent_id in agent_ids:
            writer.writerow([agent_id, sign_agent_token(agent_id, args.secret)])
    args.out.chmod(0o600)

    print(f"wrote {len(agent_ids)} agent tokens to {args.out} (chmod 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
