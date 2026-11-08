# Kasukabe genesis host - RUNBOOK

Track: **host-bringup**. Owns everything in `app/infra/` except
`litestream.yml` and `firecracker/` (owned by the trace-store and
Firecracker microVM tracks respectively - see "Known gaps" at the
bottom). Every command below is literal and copy-pasteable; you should
not need to open a single `.py` file to execute this runbook.

Read this whole document once before running anything - sections are
ordered as a real dependency chain (S3/IAM before the instance exists,
DNS before Caddy requests a TLS cert, env files before services start),
and skipping ahead will fail loudly rather than silently.

## 0. What you're building

One `m5.metal` instance in `ap-south-1` runs, directly on the host (no
Docker in production - see the top of this task's brief):

| Service | Port | Bind | Reached by |
|---|---|---|---|
| `world` (map, ticks, tools, ledger, charter) | 8100 | `127.0.0.1` | broker, game, console (same host only) |
| `broker` (Bedrock allowlist, RPM buckets, metering) | 8200 | `0.0.0.0` | world, game, console, and the 250 Firecracker microVMs over the bridge network |
| `game` (live town view, Phaser + WS relay) | 8400 | `127.0.0.1` | Caddy only |
| `console` (President/God operator UI) | 8300 | `127.0.0.1` | Caddy only |
| `caddy` (TLS + basic-auth gate) | 80, 443 | `0.0.0.0` | the public internet |
| `litestream` | n/a (no listener) | - | streams SQLite's WAL to S3 continuously |

The 250 Firecracker microVMs (`app/infra/firecracker/`, a different
track) run alongside this on the same bare-metal box, each pointed at
broker's `0.0.0.0:8200` over whatever bridge network that track sets up.
This runbook does not configure that network - see "Known gaps."

## 1. Prerequisites

On your local machine:

- AWS CLI v2 (`aws --version`), credentials for account `771650096076`
  with IAM + EC2 + S3 admin rights. **Do not use the account's root
  credentials for repeated provisioning if you can help it** - this
  runbook works with root creds (that's what was available when it was
  written) but an IAM user/role scoped to this work is safer if you have
  time to set one up first.
- An existing EC2 key pair in `ap-south-1` (or create one - see
  `scripts/02-launch-instance.sh`'s header comment for the exact
  command). This runbook never generates or stores a private key for
  you; that's a conscious action you take yourself.
- Your current public IP (`curl -s ifconfig.me`) - SSH access is locked
  to this one address, never `0.0.0.0/0`.
- DNS control over `september.wtf` (to add two `A` records - section 5).
- `ssh`, `rsync` (both ship with macOS/Linux by default).

Cost: `m5.metal` on-demand in `ap-south-1` is **$4.848/hr**. DESIGN.md
section 9's throughput math is ~14-16 hours for a full ~6-game-day run,
so budget **~$70-80 in compute** for the host itself - well inside the
task's $400 hard cap, which is dominated by inference spend, not infra.
Tear the host down (section 12) as soon as the run ends; the EBS volume
and S3 bucket are cheap to leave running, the metal instance is not.

## 2. Why on-demand, not spot

DESIGN.md's architecture diagram (section 9) says "m5.metal (spot,
~$0.75/hr)" - that line is stale. The live decision for this run
(carried in this task's brief) is **on-demand, deliberately not spot**:
a spot reclamation mid-run would kill all 250 microVMs and the world/
broker/game processes at once, and while Litestream + per-game-day
checkpoints make that *recoverable*, "recoverable" still means losing
wall-clock time against a hard budget on a day where the run must start
today. On-demand costs ~6.5x more per hour but removes the single
failure mode most likely to actually happen during a long-running batch
job on spot capacity.

## 3. S3 bucket + IAM (local machine)

```bash
export AWS_REGION=ap-south-1
bash app/infra/scripts/01-setup-s3-and-iam.sh
```

This creates (idempotently - safe to re-run):

- An S3 bucket `kasukabe-genesis-<account-id>-ap-south-1`, versioned,
  encrypted (SSE-S3), all four public-access-block settings on.
- Four prefixes under it - `litestream/`, `archives/payloads/`,
  `archives/checkpoints/`, `exports/parquet/` - matching DESIGN.md
  section 9's four S3-bound artifacts (WAL replica, gzipped raw LLM
  payloads, world-checkpoint dumps, per-game-day Parquet). This layout
  is the interface the trace-store track's S3 client and
  `litestream.yml` should target.
- An IAM role + instance profile scoped to ONLY that bucket, plus
  `bedrock:InvokeModel*` restricted to the two allow-listed model
  families in `ap-south-1` (mirrors `broker/config.py`'s
  `MODEL_ALLOWLIST` - if that tuple changes, this policy needs to change
  with it).

The script prints `BUCKET_NAME` and `INSTANCE_PROFILE_NAME` - export
both, you need them in the next step.

**Before the real run**, confirm in the Bedrock console (Model access /
Cross-region inference) whether Kimi/DeepSeek in `ap-south-1` are
fronted by a bare foundation-model ARN or an inference profile ARN, and
tighten the wildcard `inference-profile/*` resource in the generated
policy if you can - the script's own comment explains why it's a
wildcard today. `broker/config.py`'s `MODEL_ALLOWLIST` is the hard
application-layer gate either way, so this is defense in depth, not the
only check standing between the instance and an unintended model call.

## 4. Launch the instance (local machine)

```bash
export BUCKET_NAME=...              # from step 3's output
export INSTANCE_PROFILE_NAME=...    # from step 3's output
export KEY_NAME=your-ec2-key-name   # must already exist - see script header
export OPERATOR_IP=$(curl -s ifconfig.me)/32
bash app/infra/scripts/02-launch-instance.sh
```

This creates a security group (SSH from `OPERATOR_IP` only, 80/443 from
the internet, nothing else - 8100/8200/8300/8400 are never opened
publicly), looks up the latest Ubuntu 22.04 LTS amd64 AMI, and launches:

- `m5.metal`, **on-demand** (no spot request), 96 vCPU / 384 GiB.
- A 500 GiB **gp3** root volume, 6000 IOPS / 500 MB/s throughput,
  `DeleteOnTermination=false` (persistent - survives an accidental
  `terminate-instances`; you decide when to actually delete it, see
  teardown). Sized for the OS, the app venv, 250 Firecracker rootfs
  images, the SQLite trace DB, and logs with real headroom.
- An Elastic IP, associated immediately.

It prints `INSTANCE_ID`, `PUBLIC_IP`, `ALLOC_ID`, and `SG_ID` - **save
all four**, you need them for teardown at the end.

## 5. DNS

Point two `A` records at the `PUBLIC_IP` from step 4:

```
kasukabe.september.wtf         A   <PUBLIC_IP>
console.kasukabe.september.wtf A   <PUBLIC_IP>
```

Two separate hostnames rather than one host with two paths
(`/console`) on purpose: `game/server.py` and `game/console/app.py` each
serve their own static assets and API routes assuming they own the
whole origin (see `game/console/README.md` - it's a standalone FastAPI
service, not mounted under another app's path), and Caddy's automatic
HTTPS wants one cert per hostname anyway. Wait for propagation
(`dig +short kasukabe.september.wtf`) before section 8 - Caddy's ACME
challenge will fail silently-then-retry against a domain that doesn't
resolve yet.

## 6. Bootstrap the host + sync the repo

SSH in once the instance shows `running` (give cloud-init ~1-2 minutes
after that):

```bash
ssh -i ~/.ssh/<KEY_NAME>.pem ubuntu@<PUBLIC_IP>
```

From your **local machine**, push the bootstrap script up and run it,
then sync the repo:

```bash
scp -i ~/.ssh/<KEY_NAME>.pem app/infra/scripts/03-bootstrap-host.sh ubuntu@<PUBLIC_IP>:/tmp/
ssh -i ~/.ssh/<KEY_NAME>.pem ubuntu@<PUBLIC_IP> 'bash /tmp/03-bootstrap-host.sh'

export PUBLIC_IP=<PUBLIC_IP>
export KEY_NAME=<KEY_NAME>
bash app/infra/scripts/04-sync-repo.sh
```

`03-bootstrap-host.sh` installs system packages, creates the `kasukabe`
service user, installs `uv` (system-wide, at `/usr/local/bin`, so every
user sees it), Python 3.12 via `uv`, Caddy, and Litestream. It also
checks `/dev/kvm` exists - `m5.metal`'s whole reason for being in this
design is real hardware virtualization for Firecracker, and a missing
`/dev/kvm` here means the instance type is wrong.

`04-sync-repo.sh` rsyncs the whole `/Vat/kasukabe` tree (`app/`,
`engine/`, `september-sdk/`, `smallville/`) to `/opt/kasukabe/` on the
host. **This is the primary sync path today**, not a fallback - the repo
has no git remote as of this writing (`git status` at the repo root:
"not a git repository"). Re-run this script any time you need to push a
hotfix during bring-up; it's a diff-only sync, safe to run repeatedly.

## 7. Configure environment files and start the app services

On the instance:

```bash
cd /opt/kasukabe/app/infra/env
for f in world broker game console; do cp $f.env.example $f.env; done
# edit each *.env, replacing every REPLACE_ME:
#   broker.env:  AUTH_SHARED_SECRET  -> openssl rand -hex 32
#                SUPABASE_URL / SUPABASE_SERVICE_KEY -> see "Known gaps" below,
#                this is a live cross-track blocker, read it before filling these in
#   console.env: OPERATOR_TOKEN      -> openssl rand -base64 32
nano broker.env console.env   # world.env and game.env need no secrets, review anyway

cd /opt/kasukabe/app
bash infra/scripts/05-deploy-services.sh
```

This runs `uv sync` as the `kasukabe` user, installs the four systemd
units (`kasukabe-world`, `kasukabe-broker`, `kasukabe-game`,
`kasukabe-console`), and starts them in dependency order. It refuses to
proceed if any `.env` file is still missing or has an unfilled
`REPLACE_ME` - it will not start services against placeholder secrets.

Verify:

```bash
curl -s localhost:8100/health
curl -s localhost:8200/health
curl -s localhost:8400/health
curl -s localhost:8300/health
journalctl -u kasukabe-world -u kasukabe-broker -u kasukabe-game -u kasukabe-console -n 50 --no-pager
```

## 8. Caddy: TLS + token gating

Generate a shared operator credential and its bcrypt hash:

```bash
# on the instance (caddy is already installed by step 6)
caddy hash-password
# paste a strong passphrase when prompted, e.g. output of:
#   openssl rand -base64 24
# copy the $2a$... hash it prints - you need it below
```

```bash
cd /opt/kasukabe/app/infra/env
cp caddy.env.example caddy.env
nano caddy.env
#   KASUKABE_DOMAIN_ROOT=kasukabe.september.wtf
#   KASUKABE_DOMAIN_CONSOLE=console.kasukabe.september.wtf
#   KASUKABE_ACME_EMAIL=<an address that can receive cert renewal failure notices>
#   KASUKABE_BASIC_AUTH_HASH=<the $2a$... hash from caddy hash-password>

cd /opt/kasukabe/app
bash infra/scripts/06-configure-caddy.sh
```

This is the **token gating** the task spec calls for: HTTP Basic Auth in
front of both public hostnames, checked by Caddy before a request ever
reaches `game/` or `game/console/`. It is a second, independent layer on
top of the console's own `OPERATOR_TOKEN` bearer check
(`game/console/auth.py`) - the live town view has **no app-level auth at
all** (`game/config.py`'s own docstring calls it "the unauthenticated
live town view"), so Caddy's basic-auth is the only thing standing
between the public internet and a page streaming the entire town's live
state, 250 agents' balances included.

Verify from your **local machine** (not the instance):

```bash
curl -u operator:<the plaintext passphrase you hashed> -I https://kasukabe.september.wtf/
curl -u operator:<the plaintext passphrase you hashed> -I https://console.kasukabe.september.wtf/
```

`401` means Caddy is up but the credential is wrong. A TLS handshake
failure usually means DNS hasn't propagated or 80/443 aren't reachable -
check the security group from step 4.

## 9. Provision agent broker credentials

`broker/auth.py`'s own module docstring: "each agent's microVM is
provisioned with `CDT_URL` pointed at broker and `CDT_KEY` set to that
agent's broker credential... infra/'s job to mint these at
agent-provisioning time."

```bash
cd /opt/kasukabe/app
python3 infra/scripts/07-mint-agent-tokens.py \
  --secret "$(grep '^AUTH_SHARED_SECRET=' infra/env/broker.env | cut -d= -f2)" \
  --genesis world/genesis.json \
  --out /opt/kasukabe/data/agent-tokens.csv
```

Produces one `agent_id,token` row per agent in `world/genesis.json`'s
already-generated 250-agent population (`chmod 600`, contains live
credentials - never commit, log, or paste this file). Hand this CSV to
whoever runs the Firecracker microVM provisioning
(`app/infra/firecracker/`, a different track) - it is the exact input
their per-microVM `CDT_KEY` needs. Each token is
`<agent_id>.<hex-hmac-of-agent_id-with-AUTH_SHARED_SECRET>`, verified by
`broker/auth.py::resolve_agent_id` on every request broker receives.

## 10. Litestream

`litestream.yml` (owned by the trace-store track, not this one - it
landed at `app/infra/litestream.yml` while this runbook was being
written) expands `${KASUKABE_SQLITE_PATH}`, `${KASUKABE_S3_BUCKET}`, and
`${AWS_REGION}` from `infra/env/litestream.env` at load time. It is
**deliberately not started by `05-deploy-services.sh`** (that script only
installs the world/broker/game/console units) so a missing bucket name
never gets a chance to silently no-op - start it explicitly, once, here:

```bash
cd /opt/kasukabe/app/infra/env
cp litestream.env.example litestream.env
nano litestream.env
#   KASUKABE_S3_BUCKET=<BUCKET_NAME from section 3's output>
# KASUKABE_SQLITE_PATH and AWS_REGION are already filled correctly by
# the template - only KASUKABE_S3_BUCKET needs a real value.

sudo cp /opt/kasukabe/app/infra/systemd/kasukabe-litestream.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kasukabe-litestream
journalctl -u kasukabe-litestream -f
```

Confirm replication is actually landing in S3:

```bash
aws s3 ls s3://<BUCKET_NAME>/litestream/ --recursive | tail -5
```

## 11. Smoke-test before the real run

Before letting 250 microVMs loose on this host:

1. `curl` all four `/health` endpoints (section 7) - all `200`.
2. Both public hostnames return `401` unauthenticated and `200` with the
   basic-auth credential (section 8).
3. Send one broker request with a minted agent token (section 9) against
   `https://<a bridge-network address once firecracker/ sets that up>:8200/v1/messages`
   or, before that network exists, from the host itself against
   `localhost:8200` - confirm it either succeeds (`MOCK_MODE=true`
   during this smoke test is fine) or fails with a clear allowlist/auth
   error, not a silent hang.
4. `journalctl -u kasukabe-broker -n 100` - RPM buckets, wallet
   metering, and trace-write logs should be visible and un-alarming.
5. Once `litestream.yml` lands (section 10), kill `-9` the world process
   and confirm `systemctl` restarts it and the SQLite file's WAL is
   intact (`sqlite3 /opt/kasukabe/data/kasukabe.db "PRAGMA integrity_check;"`).

## 12. Teardown (one command)

From your **local machine**:

```bash
export INSTANCE_ID=...   # from step 4's output
export ALLOC_ID=...
export SG_ID=...
bash app/infra/scripts/teardown.sh
```

Default behavior: terminates the instance, releases the Elastic IP.
Leaves the EBS volume (still holding the SQLite DB and everything else,
in case Litestream hadn't caught up), the security group, the S3
bucket, and the IAM role/instance profile in place - cheap to leave,
and lets you re-launch against the same data with just
`02-launch-instance.sh` again.

Before you're fully done with the experiment, confirm S3 has everything
you need:

```bash
aws s3 ls s3://<BUCKET_NAME>/litestream/ --recursive | tail
aws s3 ls s3://<BUCKET_NAME>/exports/parquet/ --recursive
aws s3 ls s3://<BUCKET_NAME>/archives/checkpoints/ --recursive | tail
```

Then run the same command with `--nuke-all` to also delete the orphaned
EBS volume, the security group, the IAM role/policy/instance profile,
and (after typing the bucket name to confirm) the S3 bucket itself. This
is not reversible - only pass it once you've verified the above.

## Known gaps (cross-track, not mine to fix here)

This section exists so nobody re-derives these from scratch mid-run -
they were true as of this writing and may have changed by the time you
read this; re-check the files named before assuming either way.

1. **`broker/config.py` hard-requires `SUPABASE_URL` +
   `SUPABASE_SERVICE_KEY` whenever `MOCK_MODE=false`**, and
   `broker/app.py` wires `broker/trace.py`'s `SupabaseRestSink` (a real
   Postgres/PostgREST endpoint) as its trace and event sink. This is now
   the ONE piece of the SQLite-replaces-Supabase decision (DESIGN.md
   section 9, 2026-07-30) that has not landed - the trace-store track has
   since shipped the SQLite pragma hardening, the PostgREST driver
   removal from `supabase/`, the boto3-free S3 client, and
   `litestream.yml` itself (see item 2 - it now exists), but
   `broker/config.py`/`broker/app.py` still point at the old Supabase
   REST sink as of this runbook being written. **Do not "fix" this by
   pointing `SUPABASE_URL` at a throwaway project just to make broker
   boot** - a broker silently unable to write `llm_calls`/`events` is a
   broken causal record (DESIGN.md section 2: "the Supabase trace is a
   complete causal record of the world"), worse than the process
   refusing to start. `broker.env.example` carries the same warning
   inline. Re-check `broker/config.py` and `broker/app.py` before the
   live run; if this has landed, this paragraph is stale.
2. **`litestream.yml` now exists** (`app/infra/litestream.yml`, owned by
   the trace-store track) and section 10 above is written against its
   actual contents, not a guess. It expects `KASUKABE_S3_BUCKET` in
   `infra/env/litestream.env`, which this track's `litestream.env.example`
   now includes as a placeholder - fill it in from section 3's
   `BUCKET_NAME` output before section 10.
3. **`world/app.py` is still a `/health`-only scaffold** as of this
   writing (its own module docstring says so). `game/console/README.md`
   documents the exact admin HTTP contract (`WorldAdminClient` in
   `game/console/world_client.py`) it's expected to grow into. This
   runbook's systemd units point at `world.app:app` regardless of how
   complete that module is - they'll pick up real functionality as soon
   as world/'s owner ships it, with no infra-side changes needed.
4. **The Firecracker bridge network** (`app/infra/firecracker/network/`)
   is not this track's to build. `kasukabe-broker.service` binds
   `0.0.0.0:8200` specifically so it's reachable from whatever bridge
   gateway address that track picks - coordinate with them on that IP
   before section 9's tokens go into 250 microVM configs, since the
   token itself doesn't encode a URL, only `CDT_URL` (set at microVM
   provisioning time, outside this runbook) does.
5. **Exact Bedrock ARN shape** (bare foundation-model vs. inference
   profile) for Kimi/DeepSeek flex tier in `ap-south-1` needs confirming
   in the Bedrock console before the live run - section 3 flags this
   where the IAM policy is generated.
