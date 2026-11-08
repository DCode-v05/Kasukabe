# Kasukabe: what is done, what is not, and who did it

*One page over `FINDINGS-REGISTER.md`, `OPEN-QUESTIONS-ANSWERED.md`,
`SHARAN-PROGRESS.md` and `WORK-SPLIT.md`. Built by parsing the register and
attributing every closed finding with `git log -S`, not by remembering.*

## The whole picture

| | Count | Of |
|---|---:|---:|
| **Findings closed** | **52** | 268 |
| — by **Sharan** | 27 | |
| — by **Deni** | 25 | |
| **Half done, or re-checked and still broken** | **6** | |
| **Untouched** — the reviewers' word, not re-checked | **210** | |

| | Count | Of |
|---|---:|---:|
| **Open questions built** | **29** | 99 |
| — answered, no code yet | 46 | |
| — still needs the operator | 23 | |

**Suite:** 3,182 passed · 10 skipped · 0 failed (`world/`, `broker/`,
`supabase/`); 175 passed · 1 skipped in the engine's sim seam.

**The bar.** Every closed finding was watched to fail before its fix was
kept: guard written, fix reverted, failure observed, restored.

## By subsystem

| Doc | Subsystem | Lane | Closed | Total |
|---|---|---|---:|---:|
| `11` | World tools & physics | Deni | **12** | 30 |
| `13` | Governance & justice | Deni | **9** | 25 |
| `14` | Runner & record | Sharan | **7** | 33 |
| `10` | Engine seam & mind | Sharan | **6** | 27 |
| `18` | Growth & construction | Deni | **5** | 30 |
| `17` | Evals, tests, docs | Sharan | **4** | 28 |
| `12` | Economy & life | Deni | **3** | 32 |
| `15` | Broker, trace, infra | Sharan | **3** | 30 |
| `16` | Frontend & console | Deni | **3** | 33 |
| | | | **52** | **268** |

---

# ✅ Sharan — 27 findings closed

| Id | P | What was wrong |
|---|---|---|
| `CORE-01` | — | `--tick-interval-s` is dead: the runner's loop never sleeps, so the town runs at "as fast as the slowest turn" |
| `CORE-02` | — | Charter events never reach the bus in a live run (no `rewire_charter_event_sink` in the embedded root) |
| `CORE-04` | — | Day-boundary jobs run on the first tick of every run (and only then, so far) |
| `CORE-05` | — | The pre-halt learning drain cannot fire on a live run: `run_continuous` never calls `drain_before_halt` |
| `CORE-17` | — | Night is neither cheap nor consolidated: no burst, continuing actions work through it, interrupts are discarded |
| `CORE-23` | — | Tests that cannot fail (source-string assertions) on the exact paths that are broken |
| `CORE-24` | — | Recorder statistics are invisible; no run manifest; no event schema version; no per-tick perception record |
| `ECON-05` ¹ | P0 | The exchange rate makes energy unpurchasable from honest work |
| `ECON-09` ¹ | P1 | Pensions are never paid; the elderly have no income |
| `ENG-01` | P0 | The learning centre cannot write anything in the sim |
| `ENG-02` | P0 | Every model call is emergency-compacted; the first perception is pinned forever |
| `ENG-03` | P1 | All ~79 tool schemas are sent on every call; they are the dominant input cost |
| `ENG-04` | P1 | Learning signals grow quadratically |
| `ENG-16` | P1 | Location-reduced schemas are defeated by herald's tool cache |
| `ENG-26` | P2 | Tests that cannot catch the P0s |
| `EVAL-01` | P0 | `run_report.py` reads event types the world never emits; the economy sections are false by construction |
| `EVAL-02` | P0 | in a genesis run the `events` table is never written, so nothing that reads it can report anything |
| `EVAL-03` | P0 | `llm_calls.tick` is always `None` and `task_label` is never written; C1 cannot be computed |
| `EVAL-21` | — | (P3, tests, S) |
| `GOV-04` ¹ | P1 | Charter engine events are lost in production; duplicated in the other host |
| `GROW-01` ¹ | P0 | No production path can create a `Plot`; `build_structure` is unreachable |
| `GROW-02` ¹ | P0 | No path issues a `builder_license`; the licence gate refuses everyone including the construction company |
| `GROW-03` ¹ | P0 | A completed building does not become a usable place: sector name never satisfies any gate, and no Business/Storage/workplace is created |
| `GROW-04` ¹ | P0 | `complete_construction` / `buildings_due_for_completion` have no caller; construction never finishes |
| `INFRA-01` | — | `world_snapshots` rows and checkpoint archives go to a throwaway in-memory database on every real run |
| `INFRA-02` | — | `llm_calls.tick` and `task_label` are always NULL, so per-game-day analysis and the Parquet export of calls are empty by construction |
| `INFRA-03` | — | A resident at $0 is still dispatched every due tick and refused every time; the refusal storm at $0 (Build Spec D6, threshold 9.7) is not closed on th |

¹ In **Deni's lane**, built by Sharan: `GOV-04`, `ECON-05`, `ECON-09` and
all four `GROW-01`–`GROW-04`.

**Plus 10 of the 99 open questions**, **all four operator decisions**, and
one bug reported from a run that no reviewer found (the temple, below).

### The five work blocks — all closed

| | Block | |
|---|---|---|
| S1 | One composition root | ✅ |
| S2 | The record becomes true | ✅ |
| S3 | A day that is affordable | ✅ |
| S4 | The resident can actually think | ✅ |
| S5 | Tests that can fail | ✅ |


---

# ✅ Deni — 25 findings closed

| Id | P | What was wrong |
|---|---|---|
| `ECON-01` | P0 | Head tax bills 0 sim because the genesis rate is 0, and the S1 gate cannot tell |
| `GOV-01` | P0 | The election never concludes; government is vacant for the whole run |
| `GOV-02` | P0 | A theft case is filed against the victim, so the justice tools convict the robbed |
| `GOV-03` | P0 | No amendment can ever pass: there is no vote tool and no table tool |
| `GOV-06` | P1 | The only tax the town can charge is zero, and the S1 gate cannot fail |
| `GOV-13` | P2 | Suffrage age is hard-coded in the registry, so an amendment to `suffrage_min_age` cannot take effect |
| `GOV-16` | P2 | A jail sentence never ends on its own |
| `GOV-17` | P2 | Residents cannot discover an election or the government |
| `GOV-20` | P2 | `declare_candidacy` wakes the whole town |
| `GROW-28` | — | (P3) `known_places` lists workshops that do not physically exist as "places you could go" |
| `TOOL-01` | P0 | Three spellings of every place, no resolver at the tool boundary |
| `TOOL-02` | P0 | Workshops are ids without ground; the mill business is not a mill |
| `TOOL-03` | P0 | A business's shelf and till are unreachable by its owner |
| `TOOL-04` | P0 | Residents cannot see what they hold |
| `TOOL-08` | P1 | Charter engines compare location strings strictly; the registry folds them |
| `TOOL-09` | P1 | The utterance log is attached only when the map loads |
| `TOOL-11` | P1 | Newly registered businesses live in the government complex |
| `TOOL-13` | P2 | `keep_doing` and `set_intention` refuse the engine's own tool names |
| `TOOL-14` | P2 | `craft`/`cook` descriptions name no recipe, `harvest` no crop |
| `TOOL-15` | P2 | Owners can hire and pay themselves; hiring needs no consent or presence |
| `TOOL-19` | P2 | `trade` and `steal` compare places strictly |
| `TOOL-22` | P2 | Confinement never ends on its own |
| `UI-03` | P1 | The browser's own health/food model can hide a real, living agent from the map |
| `UI-09` | P2 | HTML injection from agent-authored text in the township page |
| `UI-17` | P2 | Two clocks |

### Deni's six blocks

| | Block | |
|---|---|---|
| D1 | One vocabulary for places | ✅ `TOOL-01` |
| D2 | Things to do with a day | ✅ `TOOL-02`, `TOOL-03`, `TOOL-04` |
| D3 | The economy stops being free | ◐ `ECON-01`, `ECON-05`, `ECON-09` done; `ECON-04` open |
| D4 | Someone can build something | ✅ **all four locks off** — `GROW-01`–`GROW-04`, built by Sharan |
| D5 | Institutions that run | ✅ `GOV-01`, `GOV-02`, `GOV-03` |
| D6 | The viewer stops lying | ◐ `UI-03` done; `UI-04` open |

---

# The temple — a bug no reviewer found

Reported from a run: *"Kasukabe Temple doesn't even exist in the map."*
Both halves were true at once, which is what made it confusing.

**The world has always had it.** `build_kasukabe_genesis_map` mints sector
**900001, "Kasukabe Temple"**; `known_places` lists it; `move` walks there;
`pray` and `marry` are gated on it. DESIGN section 1 calls it a genesis
building, section 4 puts marriage in it, section 7 gives it weddings,
prayers and donations.

**The viewer never drew it.** `village.js` had thirty zones and no temple.
`village2.js` registered none. `township-v2.js` — 272 KB, the map a person
actually looks at — contained the string `temple` **zero times**, while
`agents.js` mentioned it **105 times** in the residents' own personas.

So a resident walked to the temple in the world and was drawn standing
nowhere, while its persona talked about the place.

Fixed: the temple is a zone on both maps, with its own palette so it is not
drawn as a cottage (`ROOM_STYLE` silently falls back to `house` for an
unknown style), counted as civic in both viewers, and on ground verified
empty against every other zone. A test holds all of it.

### A correction, while fixing it

I first reported this as a **case-sensitivity bug** — that `"Temple"` was
refused where `"temple"` worked, and that "the town's religious life sat
behind a capital letter". **That was overstated.** Checking the live path:
`move` already folded `"Temple"`, `"the temple"` and `"Kasukabe Temple"`
correctly through `canonical_destination`, and a resident could pray at all
of them.

The case fold I added is still worth having — `satisfies_gate("Temple",
"temple")` was genuinely False, a hazard for any caller not going through
`canonical_destination` — but it was **not** the failure you saw. The
failure was the map.

---

# ❌ Incomplete

## Re-checked, and still not done

**6 findings.**

| Id | Status | Lane | What remains |
|---|---|---|---|
| `GOV-15` | **PARTLY** | Deni | Head-tax debt events are never emitted; `rule` fines are booked as tax |
| `GOV-18` | **PARTLY** | Deni | Two rulebooks |
| `GROW-06` | **OPEN** | Deni | The production host has no `MapService`; land approval is a logged no-op |
| `TOOL-12` | **PARTLY** | Deni | Refusal log and gap feedback see under half of the refusals |
| `TOOL-25` | **PARTLY** | Deni | `is_real_destination`, `task_label.py`, `render_economic_context_block` are dead |
| `UI-02` | **OPEN** | Deni | The DESIGN §8 research viewers are unreachable on a live run |

## Untouched

**210 findings** carry the reviewers' assessment.

### The sweep, and a correction to it

The reviewers' strongest claim shape — *"X has no caller"* — is
mechanically checkable, so I swept the ones I could extract (11 claims,
symbol by symbol, excluding tests and comments).

**An earlier version of this page reported three symbols as still
uncalled. That was wrong.** The threshold treated *one* caller as *no*
caller. Checked by hand:

| Symbol | Callers | Verdict |
|---|---:|---|
| `pay_pension` | **0** | genuinely uncalled — now fixed (`ECON-09`) |
| `refund_action` | 1 | called at `tools/registry.py:584` — fine |
| `unmark_processed` | 1 | called at `learning_centre/pipeline.py:427` — fine |

So the sweep found **one** real orphan, not three, and it is now closed.

**The sweep's limit, restated:** 11 claims out of 268 findings, one claim
shape. It is a spot-check, not a re-review. The other 205 untouched findings
remain the reviewers' word — strong for *"nothing calls this"*, weaker for
*"this would not work"*, because they never ran the town.

## The biggest things still open

**`ECON-04` — the treasury has nothing to spend.** Wiring `ECON-09` found
it: the treasury holds **zero sim** at genesis while residents hold about
1.57 million between them. So the government can fund nothing — no pension,
no payroll, no relief — until head-tax revenue arrives. Whether to seed it
is genesis policy (`06`.1: fixed sim supply, or minted against production),
so it is recorded with a test that pins the fact rather than a number I
chose.

**`GROW-06` / `UI-02` — this runner still has no map service.** Map growth
is a logged no-op and the research viewers are unreachable. It is the last
piece of decision 1, and it is why a completed building is placeable by gate
but not yet walkable on the map.

**`UI-04`** — the in-browser simulation can still hide living residents.

## The 46 questions answered but not built

The largest: the justice tools routing through the crime engine (`GOV-05`),
accounts for the temple, pharmacy and college, upkeep payable in kind,
escrow for public works, roads changing travel speed, goals as world state.

## The 23 still with the operator

1. **`15`.1 — the AWS lane capacity.** Every throughput and cost figure
   downstream is an assumption until somebody looks.
2. **`16`.3 — one map or two.** Keeping both builds every construction
   feature twice.
3. **`17`.7 — the canonical cast.** The docs give two answers; the run
   manifest (`CORE-24`, now written) needs one.

And the standing one: **the AWS keys pasted in chat still need rotating.**

---

## Three things worth carrying forward

**A test never seen red is not evidence.** Four times a test passed with its
fix reverted, because it exercised a helper rather than the call site —
`INFRA-02` was caught at *zero* call sites after already going green. Every
predicate added since carries an AST check on its caller.

**`ENG-16`** was stamped FIXED with no commit behind it, found by
cross-checking the register against `git log -S`. It had made `ENG-03`'s
saving nearly worthless: herald caches the wire array on the tool NAME SET,
which never changes when only descriptions move, so 14.8K → 7.6K reached the
wire once per run.

**The sweep over-reported.** Three symbols called "still uncalled" turned
out to be one. Both corrections are on this page rather than quietly fixed,
because a status document that only ever gets better is not a status
document.

