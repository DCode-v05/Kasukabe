# The 99 open questions, answered

*Every "Open questions" / "Decisions" section across the 19 review documents,
collected and answered. 99 questions, 96 distinct after de-duplicating the ones
asked in more than one document. Three kinds of answer:*

| | Meaning | Questions | Code? |
|---|---|---|---|
| ✅ **Settled and built** | The four decisions of 2026-09-04, or work landed since. Shipped, with a test watched to fail first. | **29** | **yes** |
| ◆ **Answered, not built** | A position taken; the work is still to do. Overrule any in one line. | **46** | **no** |
| ○ **Still yours** | Genuinely needs you: money, scope, research design, or an AWS console. | **23** | n/a |

> **Answered is not the same as fixed.** Only Part 1 has code behind it.
> Part 2 is a set of stated positions - decisions taken so the work is not
> blocked on them, but the work itself is still to do.
>
> That distinction was earned: when this document was first written I
> sampled eight of the defaults against the tree and **seven had zero
> references**. Ten have been built since (the table below); the rest are
> still positions. What remains unbuilt includes the justice tools routing
> through the crime engine, the temple's account, upkeep payable in kind,
> escrow for public works, roads changing travel speed, and goals as world
> state.
>
> Two questions (`12`.10 and `14`.4) are cited in two parts, because their
> answer is genuinely split: I have a default for the mechanism and the
> research meaning is yours.

> **Update, later the same day.** Ten of the Part 2 defaults have since been
> **built and proven**, so they have moved to Part 1 in spirit even though
> they are still listed below with their reasoning:
>
> | Question | What landed |
> |---|---|
> | `12`.4 / `06`.5 | Upkeep bills at midnight, not dawn (was already true after CORE-04; now proven) |
> | `12`.6 / `06`.6 | The household's **richest** member pays its head tax, not `heads[0]` |
> | `11`.7 ✅ | Hiring asks the employee first, on `teach_skill`'s consent path |
> | `11`.8 ✅ | `steal` reaches a shop's shelf — shoplifting was forbidden by omission |
> | `17`.5 ✅ | **CORE-17: a night is cheap.** Sleepers are not dispatched; quiet nights burst |
> | `11`.4 ✅ | Invented tools can be put on a `keep_doing` routine |
> | `15`.7 ✅ | `supabase/migrations/` **frozen**, with a test that fails on a new one |
> | `14`.7 ✅ | A lost row blocks the run at preflight, not three failures later |
> | `03`.6 / `10`.5 | The Law 1 manifest exists (`docs/policy/LAW-1-MANIFEST.md`) |
> | `15`.8 ✅ | The run-artifact layout is agreed (`docs/policy/RUN-ARTIFACT-LAYOUT.md`) |
>
> **Not done, deliberately:** `15`.2 recommended deleting
> `app/infra/firecracker/`. It has six importers and a whole directory, and
> deleting a subsystem on my own recommendation is not mine to do. The runner
> already refuses that backend with a clear message; the deletion needs an
> explicit go-ahead.

---

## Part 1 — Settled by the four decisions ✅

Your four answers close 24 of the 99 outright. Several documents asked the
same question in different words, which is itself a finding: these were the
load-bearing ones.

### "History bounded by the seam, or the engine's compaction?"
*Asked in `03` Q1 and `10` Q2.* **The seam.** Landed: `DISABLE_AUTO_COMPACT=1`,
the window raised from 12,500 to the model's real 131,072, and
`sim/bounded_history.py` hands a turn its recent tail by a rule we control.
The old setting was worse than nobody realised — `RESERVE_FLOOR` is an
absolute 25,000, so the preflight was true with **zero messages** and every
call in all 23 runs took the emergency path.

### "Which root is production — `run_genesis` or `main.py`?"
*Asked in `12` Q9, `14` Q2, `16` Q2.* **`run_genesis`; it absorbs the
server's wiring.** Landed: the charter sink (CORE-02) and the crime engine
(GOV-04). `bind_event_bus` turned out redundant — `persist()` already does
all six of its writes, with a *bounded* `record_event` rather than the
unbounded one that once grew a trace store to 21 GB.

### "`SIM_PER_USD`: change the pinned 10,000, or scale the price level?"
*Asked in `06` Q2 and `12` Q3.* **Changed, to 5,000.** The $1,000 invariant
is denominated in cents and is untouched — which is exactly why the rate
was changeable. `REVIVAL_COST_SIM` is now *derived* from it: DESIGN's
"10,000 sim" was only ever a spelling of "one dollar of runway", and
pinning the spelling is what made a rate change break an import.

### "Is the President a hard gate, or a veto window?"
*Asked in `05` Q1, `07` Q1, `13` Q1, `18` Q1.* **A veto window.** Landed by
Deni: `close_the_veto_window` runs on the tick path, `--veto-window-ticks`
sets the wait, zero auto-signs immediately, negative disables auto-signing.
The veto itself is untouched — an operator who vetoes inside the window
still stops the amendment.

### Also settled, by work already landed

| Question | Answer | Where |
|---|---|---|
| `14` Q1 — was billing upkeep on tick 1 intended? | **No, it was a bug.** Every run silently billed a day nobody lived. | CORE-04, fixed |
| `14` Q3, `06` Q4, `07` Q2, `13` Q2 — non-zero genesis tax rate? | **Yes, 300 bps, read from `village_rules.json`.** A zero rate stays a legal policy. | ECON-01, Deni |
| `17` Q1 — is `events` part of the record? | **Yes, with a domain filter** — exactly the W-1 the question proposes. | EVAL-02, fixed |
| `17` Q6 — `table_amendment` before Round 3? | **Yes, it exists.** DESIGN §7's authoritarianism question is reachable now. | GOV-03, Deni |
| `11` Q5 — is `village_rules.json` the world's parameters or the viewer's? | **The world's.** The genesis tax rate now reads from it, so it is no longer viewer-only. | ECON-01, Deni |

---

## Part 2 — Answered, with a default ◆

I have taken a position on each. These are defaults, not decisions: say the
word on any line and it changes.

### Cost and pacing

| # | Question | My answer |
|---|---|---|
| `03`.5, `14`.4 | Event-driven wakeups with a per-day budget, or the 400-turn cadence? | **Event-driven, with a budget.** Doc 14 §6's own model gets a resident-day from 32 turns to ≤10. The cadence baseline was never chosen, it was inherited. |
| `03`.3, `15`.3 | Who pays for the learning centre? | **A `learning` account.** Billing it to the resident pollutes C1, which is the one number the whole project turns on. |
| `17`.5 | Does the next budget go to a night? | **Yes.** No run has reached tick 144. Every compounding claim and half the eval suite have zero data until one does. CORE-01's pacing now exists; CORE-17 is the remaining piece. |
| `17`.4 | Run the L1 gate with the real composed prompt first? | **Yes, before any more description work.** The harness's own numbers moved 2× with the prompt — measuring descriptions through the wrong prompt measures the prompt. |
| `12`.8 | Minors age to adulthood in 18 game-hours. | **Slow it for small casts.** In a 20-agent town it removes the parent-debit mechanic before lunch. |

### The economy

| # | Question | My answer |
|---|---|---|
| `06`.1, `12`.2 | Fixed sim supply, or minted against production? | **Fixed, with a spending treasury.** A closed economy makes every sim traceable; minting makes the inflation question interesting but unfalsifiable this early. |
| `06`.3, `12`.1, `17`.3 | Actions priced in sim, or USD? | **Sim, as built — and fix the text.** The code and the persona currently disagree, which is a documentation bug, not a design one. |
| `12`.4, `06`.5 ✅ | Upkeep at dawn or midnight? | **Midnight**, the clock's own `is_day_boundary`. Dawn was accidental. |
| `12`.5 | Upkeep's food component payable in kind? | **Yes.** It is what makes farming load-bearing rather than decorative. |
| `12`.6, `06`.6 ✅ | Household tax payer? | **The richest earner.** "First member" makes the tax an accident of seeding order. |
| `06`.7, `12`.7, `13`.7 | Do temple, pharmacy and college get accounts? | **Yes**, and seed the temple as a RELIGION organisation with a keeper. Fees that vanish into the treasury are not an economy. |
| `06`.8, `12`.10 | Is a third of the cast reaching $0 the intended stakes? | **Unknown, and now differently unknown.** The re-rate halves the cost of a day and INFRA-03 stops dormant residents burning turns. Re-measure before calling it. |

### The world

| # | Question | My answer |
|---|---|---|
| `11`.1, `18`.2 | Premises as map sector, or stamped building? | **Sector now, building later.** Deni's TOOL-02 already makes premises provide their trade's workshop, which was the blocking half. |
| `11`.4 | Should invented tools be `keep_doing`-repeatable? | **Yes — this one is not optional.** "Mastery lowers the cost of living in dollars" is only true if an authored tool can run without a model call. Without it the claim cannot be made at all. |
| `11`.3 | `toolify` runs under the caller, DESIGN says the creator. | **Keep the reversal and update DESIGN.md.** Licences need it. |
| `11`.7 | Should hiring require the employee's consent? | **Yes**, as teaching does. Deni's TOOL-15 already stopped self-hire; consent is the other half. |
| `11`.8 | Should `steal` reach shelves and homes? | **Yes.** Deni's TOOL-03 just gave shops a shelf, so there is finally something to steal from. |
| `18`.3, `05`.3 | Materials raw or processed? | **Processed.** It gives the sawmill and smithy a customer, which is the point of having them. |
| `18`.4, `05`.4 | Public works: escrow or pay-on-inspection? | **Escrow at posting.** A visible commitment; an insolvent government defaulting is interesting data but not on the first run. |
| `18`.5, `05`.5 | Roads change travel speed? | **Yes.** Cheapest infrastructure with a measurable effect, and in-flight journeys can be re-validated. |
| `13`.5, `07`.5 | Hide `petition` from non-seats? | **No — add a citizen petition.** A citizen petition and a land petition to the President are different objects; conflating them is why the tool is gated oddly. |
| `07`.4, `13`.4 | Wire the built justice pipeline? | **Yes, and it is now possible** — the crime engine exists on the running host as of GOV-04. Routing the tools through it is the remaining step (GOV-05). |

### Instrument and infrastructure

| # | Question | My answer |
|---|---|---|
| `03`.4, `10`.4 | Where does Kimi markup recovery live? | **The broker.** That is where the stream is; a seam-side parser would have to re-implement it and the trace would record the unparsed form. |
| `04`.1, `10`.3 | Goals in the world, or the engine? | **The world.** Traced, deterministic, replayable — and it keeps the frozen core frozen. |
| `04`.2, `10`.6 | Does `no_learning` also disable sleep reflection? | **Yes.** An ablation that leaves half the mechanism running measures nothing. |
| `04`.3 | May a social request auto-create an accepted goal? | **No.** A goal nobody chose is not a goal. |
| `03`.6, `10`.5 ✅ | Law 1 manifest for the two post-vendoring engine commits? | **Yes, the manifest.** Cheaper than re-vendoring and it makes the exception auditable. |
| `15`.2 | Firecracker: keep or delete? | **Delete `app/infra/firecracker/`.** Nothing has ever booted on KVM; "250 processes on one host" is the honest design. |
| `15`.5 | Raw payload retention? | **Keep the 64 KB cap plus the full system-prompt archive.** Tens of GB in S3 buys very little that the cap does not. |
| `15`.7 | Delete `supabase/migrations/`? | **Freeze, do not delete.** Deleting loses the Postgres path; freezing stops the three-edits-per-change tax. |
| `15`.8 | Agree the run-artifact layout? | **Yes, as proposed.** Agree it before INFRA-09 lands so the report and the observatory read one layout. |
| `14`.6 | Is `smallville/` required? | **Yes** — for movement and construction, and it is 117 of the suite's failures. The checkout and CI must carry it. |
| `14`.7 | Observatory in `runseries.sh`; drop counter blocks a run? | **Yes to both.** A recorder that has already dropped rows should not get three chances. |
| `16`.6 | Where does trace-replay live? | **`research/`**, read-only against the DB, per CONVENTIONS. |
| `11`.6, `11`.9 | Two "I could not reproduce this from the excerpt" questions. | **Both answerable from a trace now** that `events` is written and `llm_calls.tick` is populated. Ask them again after the next run rather than guessing. |

---

## Part 3 — Still yours ○

Twenty-two need you. Grouped by what they actually are.

### Money and account (5)
- `15`.1 **Are the Kimi/DeepSeek lanes still ap-south-1 only at 200 RPM?** Every throughput number in DESIGN §9/§11 and the whole 250-resident plan assumes 600. Nothing can be planned honestly until this is checked.
- `15`.4 Is a **$1 live probe** of `reasoning_config` on Kimi and DeepSeek authorised?
- `15`.6 Who owns `AUTH_SHARED_SECRET` / `OPERATOR_TOKEN` for a run, and is `/opt/kasukabe/app/infra/env/` the agreed home?
- `03`.2, `10`.1, `17`.2 **Is DeepSeek in the study?** DESIGN §4 says background work runs on it; the sim is Kimi-only. This changes cost-per-agent-day and the whole C-series.
- **And the standing one:** the AWS keys pasted in chat still need rotating. That is your console.

### Research design (7)
- `13`.6 Are the six ideology axes acceptable as research variables, and is a background probe per resident per sleep affordable at rung 2?
- `17`.7 Canonical population: **the ladder (10/50/250 at $2) or the series cast (20 at $6)?** The docs disagree and the run manifest needs one answer.
- `07`.3, `13`.3 Election windows: keep one day each, or shorten so a rung-1 run sees a complete cycle?
- `14`.4 Is a 400-turn/day town the intended baseline? *(I have a default above; the research meaning of "a tick" is yours.)*
- `18`.7 **Ownership ideology:** private plots at genesis by auction, or only by grant — and may a charter amendment abolish private land? The default decides what "free-market genesis" means.
- `12`.10 Is dormancy a stakes mechanism or a failure? *(Re-measure first.)*
- `05`.2 Do the six workshops exist at genesis, or must the town build them? *(Cast-dependent: the 20-resident cast has a miller, a sawyer and a baker with nowhere to work.)*

### Scope: what stays and what goes (6)
- `16`.1 Is the file:// showcase (the 314-resident in-browser town) still a deliverable, or can the simulation be deleted? Decides UI-04's size.
- `16`.3, `18`.6, `05`.6 **Is the Phaser page retired, or does it become the single map?** Two maps means every construction feature is built twice. This one costs real weeks.
- `16`.5 Is `game/realsim.py` still wanted? It is the only place the viewer spends money and it is untested end to end.
- `16`.7 The paper needs a stable visual identity per resident — **which page is the paper's?**
- `13`.8, `16` Is the browser sim's government meant to converge with the world, or be retired?

### Scale (2)
- `14`.5 For 250 residents: a single world process, or may tool dispatch be sharded per location? The event bus, the ring buffer and schema regeneration are all single-loop today.
- `16`.4 Does anything deliver `president/broadcast` and `honors` into perception, or are they event-log-only?

---

## What I would ask you to answer next

Three, in order:

1. **`15`.1 — the lane capacity.** One look at the AWS console. Every cost and throughput number downstream is currently assumed.
2. **`16`.3 — one map or two.** It is the largest single scope question left, and it silently doubles the cost of every construction feature until it is answered.
3. **`17`.7 — the canonical cast.** The run manifest needs one answer and the docs give two.

Everything else has a default and is moving.
