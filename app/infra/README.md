# infra/

Metal-host bring-up and deploy tooling for the `ap-south-1` genesis run
(DESIGN.md section 9: the m5.metal host running 250 Firecracker microVMs
plus broker and world server). Two tracks share this directory:

- **host-bringup** (this README, `RUNBOOK.md`, `scripts/`, `systemd/`,
  `Caddyfile`, `env/`): launches and configures the m5.metal host itself
  - EC2, S3, IAM, systemd services for world/broker/game/console, Caddy
    TLS + token gating, Litestream wiring, teardown.
- **Firecracker microVMs** (`firecracker/`, `litestream.yml`): a
  separate track, owns the rootfs build, kernel, per-microVM networking,
  and the exact Litestream replication config for the SQLite trace DB.
  Not documented here - see that directory's own README once it lands.

Not a Python package - shell scripts, systemd units, and deploy tooling
live here. Never imported by `world/`, `broker/`, `game/`, or
`research/`; it drives them from the outside (build images, start
services, ship config).

## Start here

**`RUNBOOK.md`** - the full step-by-step bring-up, written so a human can
execute every command without reading any code. Read it top to bottom
once before running anything; it explains the dependency order (S3/IAM
before the instance exists, DNS before Caddy requests a TLS cert, env
files before services start) and calls out the current cross-track
blockers under "Known gaps."

## Layout

```
RUNBOOK.md              the human-facing runbook - start here
Caddyfile                reverse proxy: TLS (automatic HTTPS) + basic-auth
                          token gating in front of the two public hostnames
scripts/
  01-setup-s3-and-iam.sh   S3 bucket + IAM role/instance profile (local)
  02-launch-instance.sh    m5.metal on-demand launch + EIP (local)
  03-bootstrap-host.sh     system packages, kasukabe user, uv, caddy,
                            litestream (runs ON the instance)
  04-sync-repo.sh          rsync the repo to the instance (local; no git
                            remote exists yet, this is the primary sync
                            path, not a fallback)
  05-deploy-services.sh    venv + systemd units for world/broker/game/
                            console (runs ON the instance)
  06-configure-caddy.sh    Caddyfile + TLS + token gating (ON the instance)
  07-mint-agent-tokens.py  signs one broker bearer token per agent in
                            world/genesis.json, for the Firecracker
                            track's per-microVM CDT_KEY provisioning
  teardown.sh              one-command teardown (default: safe, data-
                            preserving; --nuke-all: full and irreversible)
systemd/                 unit files scripts/05 and scripts/06 install
env/                     *.env.example templates - copy to *.env, fill
                          in every REPLACE_ME, never commit the real ones
```
