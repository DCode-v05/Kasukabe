export const meta = {
  name: 'kasukabe-phase2-verify',
  description: 'Apply queued spec deltas, then adversarially verify every Kasukabe mechanic',
  phases: [
    { title: 'Deltas', detail: 'single-model, namespaces, SQLite-primary', model: 'sonnet' },
    { title: 'Break it', detail: 'one hostile verifier per mechanic group', model: 'sonnet' },
    { title: 'Fix', detail: 'fix confirmed breaks, add regression tests', model: 'sonnet' },
    { title: 'Gate', detail: 'full suite + verdict for the operator', model: 'sonnet' },
  ],
}

const ROOT = '/Users/septemberai/Vat/kasukabe'
const COMMON = `Kasukabe town-sim verification. Read ${ROOT}/DESIGN.md (canonical spec) and ${ROOT}/app/STATUS.md first.
Style: NO em dashes in Python, no leading-underscore names. Do NOT git commit or push. Never call real LLM APIs; broker mock mode only.
Your job is ADVERSARIAL: assume the mechanic is broken and try to prove it. Write real pytest tests into the repo (tests stay, they are the regression suite).
Report format: VERDICT (SOLID | BROKEN) per attack, with exact repro for every break.`

phase('Deltas')
const deltas = await agent(`${COMMON}
TASK: Apply three spec deltas across ${ROOT}/app (DESIGN.md sections are already updated, code may lag):
1. SINGLE MODEL: broker allowlist becomes exactly ['moonshotai.kimi-k2.5']. In the engine copy's herald seam (${ROOT}/engine sim profile): herald registers ONE model only, kimi-k2.5, as the default AND only option for EVERY herald consumer (main pinned thread, learning centre, summarizer, any subsystem that asks herald for a model); remove DeepSeek everywhere; genesis ablation arms become 210 full / 20 no-reflection / 20 no-learning. Add a test asserting no code path can request any other model id (broker 403 + herald catalog has exactly one entry) and that exactly ONE thread exists per engine (pinned, coordinator bypassed).
2. TOOL NAMESPACES: extend the tool registry with namespaces + ACLs per DESIGN.md: public / government (confidential, incumbent-only, ATOMIC access transfer on election handover) / business (owner + granted employees, transfers with sale/inheritance) / organization (member-gated). Non-members must not be able to discover a confidential tool exists (list/describe/call all deny). Court subpoena via warrant reveals invocation records.
3. SQLITE PRIMARY: the SQLite path becomes the production store (WAL mode, busy_timeout); delete the PostgREST/Supabase client; add a Litestream config file in app/infra/ and a per-game-day Parquet export job in app/research/.
Update tests, run the full suite green. Report what changed.`, { label: 'deltas', model: 'sonnet' })

log('Deltas applied, unleashing verifiers')

phase('Break it')
const ATTACKS = [
  ['ledger', `Attack the ledger invariant (wallets + treasury + burned == $1000): concurrent transfers, exchange spam at the government store, death-inheritance races, negative/zero/fractional amounts, parent-debit for minors, pension payouts, self-transfers, double-spend via interleaved tool calls. Try to mint or destroy USD by any sequence of legal tool calls. Verify the tripwire actually halts the world.`],
  ['meta-tool', `Attack the meta-tool: compose invented tools that try to mint currency, act at a distance (bypass location gates), invoke confidential-namespace tools, recurse (tool calling itself), exceed actor permissions, or side-step the patent office. Verify invented tools execute strictly under the creator's identity/wallet and constraints hold recursively through composition.`],
  ['namespaces', `Attack namespace ACLs: non-incumbent calls/lists government tools (must deny AND not leak existence), access during the exact election-handover instant (race the atomic swap), fired employee retains business-tool access, org tools after leaving the org, subpoena without a valid warrant. Verify outgoing government loses access at transfer with zero gap or overlap.`],
  ['elections', `Attack politics: full democratic path (candidacy, vote in person at town hall only, tally, handover), then the authoritarian path: 3/5 amend to postpone elections, restrict suffrage, collapse to one-person rule. Verify amendments apply exactly, Presidential veto works, the immutable complaint ledger cannot be amended away, votes by non-present or dead agents rejected, unlimited re-election works.`],
  ['crime', `Attack crime/justice: run 2000 simulated thefts, assert success ~70% and witness-event ~30% within binomial tolerance; verify police CANNOT read private state (only public records + own interviews), warrant required for arrest, arrest requires co-location, jail confines (tools locked to jail, no income), fines settle via ledger, fights injure but NEVER kill, ground-truth vs conviction is recorded for the false-conviction metric.`],
  ['life', `Attack life systems: health-tier durations and probabilities (statistical over 10k simulated agent-days: 4%/1%/0.25%/0.08%), terminal death within 3-10 game-days, dormancy at $0 with revival for 100k sim, death at 24 unrevived game-hours, aging 1yr/game-hour until 18 then static, parent-debit stops at 18, pension starts at 70 (25%/50% of last salary), marriage requires temple + fee, inheritance flows on death, population dials (death multiplier 0 disables ALL death paths, blessing throttle, target band).`],
  ['pause-snapshot', `Attack pause/freeze and persistence: pause mid-tick (world must freeze at boundary, broker holds in-flight, no event leaks while paused), resume bit-exact (hash world state before/after pause+resume: identical), checkpoint -> mutate -> restore round-trip equality, SQLite WAL survives simulated crash (kill -9 the writer mid-batch, reopen, no corruption or lost committed rows).`],
  ['broker', `Attack the broker: request any non-Kimi model id (all must 403), burst 500 requests and verify the 100 RPM bucket + fair queue (no starvation), wallet refusal exactly at insufficient balance, metering accuracy against known mock token counts (exact flex-tier math: 0.36/1.80 per 1M), minor's call debits the parent, trace rows written for every call including refused ones, mock mode never touches boto3.`],
  ['building-economy', `Attack building + economy: unlicensed agent tries build_structure (deny + complaint path), footprint overlapping existing structures or outside owned plots (deny), BoM math vs geometry (exact), construction time proportional to size, craft recipes conserve materials (no dupe via concurrent crafts or cancel-mid-craft), buy/sell atomicity under concurrency, business registration fee + tax flow, government tender flow, land expansion appends tiles + collision correctly and pathfinding still works on the grown map.`],
  ['scheduling', `Attack the tick engine: event-driven scheduling stays near 30% active agents per tick under varied load, sleeping agents get ZERO interactive LLM dispatches, learning jobs run only during that agent's sleep window, day/night transitions fire exactly at the right ticks, 144 ticks/game-day invariant, perception delivered only for co-located events, no agent ever gets two overlapping turns.`],
]

const verdicts = await pipeline(
  ATTACKS,
  a => agent(`${COMMON}\nTASK (attack: ${a[0]}): ${a[1]}`, { label: `break:${a[0]}`, phase: 'Break it', model: 'sonnet' }),
  (report, a) => {
    const broken = /VERDICT\s*[:>]?\s*BROKEN/i.test(String(report))
    if (!broken) return { area: a[0], report: String(report).slice(0, 2000), fixed: null }
    return agent(`${COMMON}\nTASK: The ${a[0]} verifier found real breaks. Fix EVERY break at the root cause (no workarounds, no test deletions), keep the verifier's tests as regressions, run them green plus the module's own tests. Verifier report:\n${String(report).slice(0, 6000)}`,
      { label: `fix:${a[0]}`, phase: 'Fix', model: 'sonnet' })
      .then(fix => ({ area: a[0], report: String(report).slice(0, 2000), fixed: String(fix).slice(0, 1500) }))
  }
)

phase('Gate')
const gate = await agent(`${COMMON}
TASK: Final gate. Run the ENTIRE test suite in ${ROOT}/app (and the engine sim-profile tests in ${ROOT}/engine). Zero failures allowed; fix trivial breakage, escalate anything structural in your report instead of hacking around it. Then re-run the 3-agent end-to-end smoke (2 mock game-days) and assert the ledger invariant held throughout. Write ${ROOT}/app/VERIFICATION.md: per-mechanic verdict table (area, attacks tried, initial verdict, fixes applied, final verdict), remaining risks ranked, and an explicit GO / NO-GO recommendation for the 10-agent live rehearsal.
Verifier results:\n${verdicts.filter(Boolean).map(v => `--- ${v.area} ---\n${v.report}\n${v.fixed ? 'FIX: ' + v.fixed : 'no fix needed'}`).join('\n')}`,
  { label: 'gate', model: 'sonnet' })

return { deltas: String(deltas).slice(0, 800), areas: verdicts.filter(Boolean).map(v => ({ area: v.area, hadBreaks: !!v.fixed })), gate: String(gate).slice(0, 2500) }
