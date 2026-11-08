export const meta = {
  name: 'kasukabe-phase2-genesis',
  description: 'Apply deltas, add 6 multi-region lanes, verify hard, prepare 250-agent genesis',
  phases: [
    { title: 'Deltas', detail: 'lanes, namespaces, sqlite, spend governor', model: 'sonnet' },
    { title: 'Break it', detail: 'parallel adversarial verification', model: 'sonnet' },
    { title: 'Fix', detail: 'root-cause fixes for confirmed breaks', model: 'sonnet' },
    { title: 'Launch prep', detail: 'host bring-up + genesis runner', model: 'sonnet' },
  ],
}

const ROOT = '/Users/septemberai/Vat/kasukabe'
const COMMON = `Kasukabe 250-agent town sim. Read ${ROOT}/DESIGN.md and ${ROOT}/app/STATUS.md first.
Style: NO em dashes in Python, no leading-underscore names. Do NOT git commit or push.
NEVER call real Bedrock in tests (mock mode only) - real spend is reserved for the genesis run.

DOCKER FOR DEV, DIRECT IN THE MICROVM. Two distinct environments, do not conflate them:
 (a) LOCAL dev/build/test on this machine: everything runs in Docker, never on the host. The engine
     copy at ${ROOT}/engine has its own docker-compose.yml "test" service (platform linux/amd64,
     source bind-mounted live, deps baked into the image): \`docker compose run --rm test\`.
     NB after any dependency change run \`docker compose build test\` first, or it silently uses a
     stale image.
 (b) PRODUCTION genesis run inside the Firecracker microVM: the engine runs DIRECTLY on the guest,
     NO Docker, no container runtime. The microVM IS the isolation boundary; nesting Docker inside
     it would double the layers and cost boot time and memory. Bake engine deps into the rootfs
     image at build time.

RUN ALL APP TESTS IN DOCKER, never on the host. The host has no pytest and PEP 668 blocks pip.
Use ${ROOT}/app/docker-compose.yml service "test": \`docker compose run --rm test\`.
If that service does not exist yet, create it FIRST, mirroring ${ROOT}/engine/docker-compose.yml
and its Dockerfile test target: pin \`platform: linux/amd64\` (production is the x86_64 m5.metal
host, so aarch64-native testing would diverge from production reality), mount the source, install
from app/pyproject.toml, default command pytest. Put local-environment fixes in the Dockerfile or
compose file, NEVER in application source.

Be adversarial and precise. Report VERDICT (SOLID | BROKEN) with exact repro for anything broken.`

const FACTS = `MEASURED FACTS (do not re-derive):
- Kimi K2.5 quota is exactly 100 RPM PER REGION, enforced. Burst bucket ~100 requests then hard 100/min.
- Kimi K2.5 verified invokable in ap-south-1, us-east-1, us-west-2. Quotas are per-region = independent lanes.
- DeepSeek V3.2 same: 100 RPM per region, same 3 regions.
- Real agent turn: 3443 input / 291 output tokens = $0.001764 per call (Kimi flex 0.36/1.80 per 1M).
- DeepSeek flex 0.37/1.11 per 1M.
- Target burn: ~$63/hr across 6 lanes at 600 RPM aggregate. HARD BUDGET CAP $400.`

phase('Deltas')
const deltas = await agent(`${COMMON}\n${FACTS}
TASK: Apply four changes across ${ROOT}/app.
1. SIX-LANE MULTI-REGION BROKER. Replace the single-bucket limiter with a lane pool:
   lanes = [kimi@ap-south-1, kimi@us-east-1, kimi@us-west-2, deepseek@ap-south-1, deepseek@us-east-1, deepseek@us-west-2].
   Each lane owns its OWN 100 RPM token bucket and its own boto3 client for that region.
   Routing policy: agent COGNITION (the pinned-thread turns) must prefer Kimi lanes; background/bulk
   work (learning centre, dream probes, importance scoring, summarizer) routes to DeepSeek lanes.
   If all Kimi lanes are saturated, cognition MAY overflow to a DeepSeek lane rather than stall, and the
   trace row must record which lane+model actually served it (this matters for research validity).
   Health-check and circuit-break a lane on repeated throttles; never spin-retry a saturated lane.
2. SPEND GOVERNOR. A hard, process-wide USD cap (default 400.00, configurable). Track cumulative real
   spend from actual response usage (never estimates). At 80% warn on the console; at 100% STOP dispatch
   entirely and checkpoint the world. Expose burn-rate ($/hr), spend-so-far, and projected-exhaustion
   over the admin API so the console can display them live.
3. TOOL NAMESPACES per DESIGN.md: public / government (confidential, incumbent-only, ATOMIC transfer on
   election handover, non-members cannot even discover existence) / business (owner + granted employees,
   transfers with sale) / organization (member-gated). Court subpoena via warrant reveals invocation records.
4. SQLITE PRIMARY: SQLite (WAL, busy_timeout) becomes the production store; delete the PostgREST/Supabase
   client; add Litestream config in app/infra/ and a per-game-day Parquet export in app/research/.
Run the full suite green. Report what changed.`, { label: 'deltas', model: 'sonnet' })

log('Deltas in. Launching verification fleet.')

phase('Break it')
const ATTACKS = [
  ['ledger-money', `Attack the ledger invariant and the spend governor together. Try to mint or destroy USD via any legal tool sequence: concurrent transfers, exchange spam at the government store, death-inheritance races, parent-debit for minors, pension payouts, negative/fractional amounts, double-spend by interleaving. Verify the invariant tripwire halts the world, and that the spend governor CANNOT be exceeded even under concurrent dispatch across all 6 lanes (race the cap).`],
  ['lanes', `Attack the 6-lane broker. Verify: each lane holds its own 100 RPM bucket independently; saturating Kimi lanes does not throttle DeepSeek lanes; cognition overflow to DeepSeek is recorded truthfully in the trace (research validity depends on this); a lane that starts throttling is circuit-broken not spin-retried; no request is lost or double-charged when a lane breaks mid-flight; model allowlist still rejects anything outside kimi-k2.5/deepseek-v3.2; mock mode never touches boto3.`],
  ['meta-tool', `Attack the meta-tool: invented tools that try to mint currency, bypass location gates, call confidential-namespace tools, recurse, exceed the creator's permissions, or dodge the patent office. Constraints must hold recursively through composition.`],
  ['namespaces', `Attack namespace ACLs: non-incumbents calling AND discovering government tools; the exact election-handover instant (race the atomic swap for a gap or overlap); fired employees retaining business tools; ex-members retaining org tools; subpoena without a valid warrant.`],
  ['elections', `Attack politics: full democratic cycle (candidacy, in-person voting only, tally, handover) then the authoritarian path (3/5 amend to postpone elections, restrict suffrage, collapse to one-person rule). Verify Presidential veto, that the immutable complaint ledger cannot be amended away, and that dead or absent agents cannot vote.`],
  ['life-crime', `Attack life + crime together. Statistical: 10k simulated agent-days must match health tiers (4% / 1% / 0.25% / 0.08%) and 2000 thefts must match ~70% success / ~30% witness within binomial tolerance. Verify terminal death lands in 3-10 game-days, dormancy at $0 and revival for 100k sim, death at 24 unrevived game-hours, aging 1yr/game-hour to 18, parent-debit stops at 18, pensions at 70 (25%/50%), police are NOT omniscient, warrants required, jail confines, fights never kill, death-multiplier 0 disables ALL death paths.`],
  ['pause-snapshot', `Attack pause and persistence, which the genesis run depends on for crash safety. Pause must freeze at a tick boundary with in-flight broker calls held and no events leaking; resume must be bit-exact (hash world state before and after). Checkpoint -> mutate -> restore must round-trip exactly. Kill -9 the SQLite writer mid-batch, reopen, prove no corruption and no lost committed rows. This is the safety net for a live money-burning run: be merciless.`],
  ['economy-build', `Attack economy + building: unlicensed build attempts, footprints overlapping or off-plot, BoM vs geometry exactness, material conservation under concurrent crafts and cancel-mid-craft, buy/sell atomicity under concurrency, tax and registration flows, government tenders, land expansion appending tiles with collision regenerated and pathfinding still valid on the grown map.`],
]

const verdicts = await pipeline(
  ATTACKS,
  a => agent(`${COMMON}\n${FACTS}\nTASK (attack: ${a[0]}): ${a[1]}`, { label: `break:${a[0]}`, phase: 'Break it', model: 'sonnet' }),
  (report, a) => {
    if (!/VERDICT\s*[:>]?\s*BROKEN/i.test(String(report))) return { area: a[0], report: String(report).slice(0, 1800), fixed: null }
    return agent(`${COMMON}\nTASK: The ${a[0]} verifier found real breaks. Fix EVERY one at root cause (no workarounds, no deleted tests), keep its tests as regressions, run them green plus the module suite. Report:\n${String(report).slice(0, 6000)}`,
      { label: `fix:${a[0]}`, phase: 'Fix', model: 'sonnet' })
      .then(f => ({ area: a[0], report: String(report).slice(0, 1800), fixed: String(f).slice(0, 1200) }))
  }
)

phase('Launch prep')
const prep = await parallel([
  () => agent(`${COMMON}\n${FACTS}
TASK: Genesis runner + richer cognition. (1) app/world/run_genesis.py: boots the world from genesis.json,
starts 250 engine processes against the local broker, runs continuously with a checkpoint EVERY game-day
to SQLite + S3, resumes cleanly from the newest checkpoint after any crash, and stops cleanly when the
spend governor trips. (2) Deepen memory retrieval so a cognition turn carries ~5000 input tokens instead
of ~3443 (more retrieved memories, richer world/social context) - this converts budget into agent quality.
Make retrieval depth a config knob so we can tune burn rate live. (3) A live metrics endpoint: spend,
burn rate, game-day, population, births, deaths, elections held, crimes, businesses.
Include a dry-run mode that exercises the whole path with mock inference.`, { label: 'genesis-runner', phase: 'Launch prep', model: 'sonnet' }),
  () => agent(`${COMMON}\n${FACTS}
TASK: Host bring-up for ap-south-1. Write app/infra/ scripts to: launch an m5.metal SPOT instance
(96 vCPU / 384 GB, ~$0.75/hr) with a persistent EBS root, install python + deps, sync the repo, run the
world server + 6-lane broker + game app behind Caddy with TLS and token gating, and stream SQLite via
Litestream to S3. Engines run as supervised OS processes for this run (Firecracker microVM packaging is a
separate track and must NOT block genesis today) - but structure the process supervisor so swapping in
microVMs later is a substitution, not a rewrite. Include: S3 bucket setup, spot-interruption handler that
checkpoints and uploads before reclaim, and a one-command teardown. Document exact commands in
app/infra/RUNBOOK.md.`, { label: 'host-bringup', phase: 'Launch prep', model: 'sonnet' }),
])

const gate = await agent(`${COMMON}
TASK: Final gate before a live money-burning run. Run the ENTIRE suite in ${ROOT}/app and the engine
sim-profile tests in ${ROOT}/engine. Zero failures. Then run the full dry-run: 250 agents, mock inference,
2 game-days, asserting the ledger invariant holds throughout, checkpoints round-trip, and the spend
governor trips correctly at a lowered test cap. Write ${ROOT}/app/VERIFICATION.md with a per-mechanic
verdict table, ranked residual risks, and an explicit GO / NO-GO for the 250-agent genesis run.
Verification results:\n${verdicts.filter(Boolean).map(v => `--- ${v.area} ---\n${v.report}\n${v.fixed ? 'FIX: ' + v.fixed : 'no fix needed'}`).join('\n')}
Launch prep:\n${prep.filter(Boolean).map(p => String(p).slice(0, 1200)).join('\n---\n')}`,
  { label: 'gate', model: 'sonnet' })

return {
  deltas: String(deltas).slice(0, 600),
  areas: verdicts.filter(Boolean).map(v => ({ area: v.area, hadBreaks: !!v.fixed })),
  gate: String(gate).slice(0, 3000),
}
