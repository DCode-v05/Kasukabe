# Spec: a government, organisations and courts that actually run

*Cross-cutting specification drawn from `13-governance-organisations-justice.md` (GOV), `11-world-tools-and-physics.md` (TOOL), `14-world-core-runner-and-record.md` (CORE), `12-economy-and-life.md` (ECON) and `16-frontend-and-console.md` (UI). Those carry the file:line evidence.*

## 1. The target, stated so it can be failed

Elections happen and conclude on their own; a government sits, is told what
the town needs, sets policy, spends a budget, and is replaced when it
loses. Organisations have members, money, roles, rules and decisions. A
crime produces a case against a suspect, an investigation, a verdict and a
sentence that ends, and the false-conviction rate is a query. The temple
acquires a keeper by a method nobody scripted.

Acceptance, in a mock run of three game-days with no model calls:

1. The genesis election tallies at its closing tick, seats fill, and a
   second election is scheduled; `politics.election.*` rows are in the
   trace and the voting viewer shows two elections.
2. A seat holder tables an amendment lowering the election interval; three
   of five vote yes; after the veto window it takes effect and the next
   election uses the new interval.
3. The treasury pays a police officer's salary and a pensioner's pension at
   each day boundary and the change log shows who set the tax rate.
4. Two residents join an organisation, pay dues into its account, a role
   holder disburses, a non-member is refused an organisation tool.
5. A steals from B; B files a complaint naming C; the seat rules a fine;
   the false-conviction count is 1. A complaint naming A yields a true
   conviction. A jail sentence releases itself at `until_tick`.

## 2. Where it stands today

| Fact | Finding |
|---|---|
| The genesis election is scheduled at boot and never tallied; seats stay vacant, so every government-namespace tool has been uncallable in all 23 runs | GOV-01 |
| No second election is ever scheduled; voting opens at tick 288 while runs end by tick 83 | GOV-01 |
| Charter engine events go to a no-op sink on the production host; politics rows never reach the bus or the trace | GOV-04, CORE-02 |
| Charter engines compare locations strictly while the registry folds them; civic tools refuse residents the registry admitted, with an error that is not logged as a refusal | TOOL-08 |
| No tool tables or votes on an amendment; "collapse to one-person rule is reachable" is false at runtime | GOV-03 |
| A theft case is filed against the victim; `arrest` and `rule` can only act on the robbed | GOV-02 |
| The built `world/crime/` package (desk, investigation, warrants, outcomes, metrics) is constructed only for the host no run uses; the live tools use a parallel three-field case | GOV-05 |
| Jail never ends on its own | GOV-16, TOOL-22 |
| The treasury has revenue and no spending; jobs, police salaries, pensions and tenders move no money; margin and pension policy are dead levers | GOV-08, GOV-09 |
| Permits and builder licences have no issuing tool | GOV-07 |
| Organisations are founder-only rows with no account, roles, decisions or rulesets | GOV-10, TOOL-30 |
| Land expansion is operator-only and decision-only on the production host | GOV-11 |
| Residents cannot discover an election or who governs; `declare_candidacy` wakes the whole town | GOV-17, GOV-20 |
| Head-tax debt and crime state are not checkpointed | GOV-12 |
| Two rulebooks: `village_rules.json` (browser) and the charter (world) disagree on tax, salaries and term length | GOV-18 |

## 3. The mechanism

### 3.1 The election tick job

`GenesisRunner.charter_tick(timestamp)` every tick (GOV W1): advance the
open election's phase; `tally()` at `voting_closes_tick`; schedule the next
election so the cycle exists; rewire the charter sink to the bus in `boot`
(one event per action, delete handler-side duplicates). Shorten genesis
windows (half a day each) so a two-day rung-1 run sees a full cycle. Seed a
caretaker government at genesis (the office administrator as interim PM) so
government tools are reachable from tick 1, or make `petition` public with
an endorsement step.

Residents must be able to find the election: a civic notice line in the
perception (election phase, candidates, your ballot status, amendments
awaiting your vote if seated, tax rate, arrears, who sits) and an election
block in `look_around` at the complex (GOV-17). Candidacy wakes nobody but
the candidate (GOV-20). Civic handlers pass the canonical place to the
charter (TOOL-08), and `CharterError` becomes a logged refusal.

### 3.2 Amendments and the veto window

Two government tools: `table_amendment(field, new_value, title, rationale)`
and `vote_amendment(proposal_id, support)` (GOV-03). Registry-side suffrage
hard-codes go; the charter decides (GOV-13). The President's sign or veto
becomes a window of N ticks with auto-sign after it, N configurable, zero
for unattended runs. Amendments address `charter.*` and `policy.*` fields
alike, so the policy engine (doc 06 §4.7) is amendable by the same path.

### 3.3 The treasury as a budget

Day job (doc 06 §4.3): payroll for every filled `Job` and appointed police,
pensions, relief, tender escrow and milestones, `reserve_floor_sim`.
Tools: `create_job`, `cut_job`, `post_tender`, `bid_on_tender`,
`award_tender`, `issue_permit`, `issue_builder_license`, `revoke_license`,
`set_policy`. Margin applied in `buy_energy` and retained by the treasury
(GOV-09). Fines booked as `FINE`, not `TAX` (GOV-15). A `treasury_view`
already exists and will finally show non-zero rows.

### 3.4 Organisations that run

In order (GOV §5.2, TOOL-30):

1. Membership tools: `join_org`, `leave_org`, `admit_member`,
   `expel_member` over `organizations.add_member` and `remove_member`.
2. An organisation account opened by `register_org` (mirror
   `Ledger.register_business`); `pay_dues`, `org_disburse` by a treasurer
   role.
3. Roles as org-defined strings with a powers map (admit, disburse, amend).
4. Decisions: `org_propose`, `org_vote` with a threshold from the
   organisation's ruleset; enacted changes emit
   `politics.organization.amended`.
5. Rulesets (ROADMAP T6): one `Ruleset` model for organisations, businesses
   and the charter: who may call which tools on what terms, obligations
   (dues, revenue share, wage floor, price floor), and what happens on
   breach. Enforced in `ToolRegistry.execute` after actor checks; a breach
   is a `ruleset.breach` event that reaches the complaints desk. The Town
   Charter is the government's ruleset. Contracts are the same shape with a
   settlement tool and a deadline.
6. The temple as an organisation of kind RELIGION seeded with an empty
   membership and a `keeper` role; `ordain_keeper` by org vote or amendment;
   a temple account receiving donations and wedding fees; `hold_rite`
   emitting `religion.rite.held` with co-located attendees;
   `religion.doctrine.emerged` when a religion authors a skill or tool in
   its namespace.

### 3.5 Justice with ground truth

Fix the live case model first (GOV-02): `CrimeCase` gains `victim_agent_id`,
`accused_agent_id` and a write-once `ground_truth_perpetrator_id` that no
tool returns; `steal` sets victim and ground truth; `file_complaint` names
an accused and may attach to an existing theft case; `arrest` and `rule`
act on the accused; metrics compare verdicts with ground truth. A tick sweep
releases confinements at `until_tick` (GOV-16). Then choose (GOV-05,
operator question): wire the built epistemic pipeline in `world/crime/`
(construct `CrimeEngine` on the production host, add `interview`,
`read_case`, `request_warrant`, `sign_warrant`, checkpoint the store), or
delete it and grow the live model. The false-conviction metric (DESIGN §6,
ROADMAP question 20) needs suspect and ground truth to differ either way.
The `crime.theft.attempted` payload must stop carrying the thief's id to
every perceiver.

### 3.6 Land, expansion approval and public works

Doc 05 §4.5: caretaker government or public petition; an expansion
resolution by the government's own vote; a President veto window;
approval opens a tender rather than stamping tiles, so the town's own
builders do the work.

### 3.7 Record and checkpoint

Write `politics.*` to the trace's `petitions`, `organizations`, `cases`
tables, which exist and have no writer (INFRA-17); register
`HeadTaxDebtStore` and a `CrimeStore` snapshot in the checkpoint; register
the charter adapter in `build_world_server` (GOV-12). Mount `social_api` on
the production host so the scenario feed exists (GOV-19, UI-02). One
rulebook: either the world reads `village_rules.json` as its genesis policy
source or the file is marked viewer-only (GOV-18).

## 4. Decisions the operator has to make

1. Does the President remain a hard gate on amendments and land, or a veto
   window with auto-approval (needed for any run to be "on its own").
2. Genesis policy: 300 bps and the salary schedule from `village_rules.json`,
   or no tax until the first government sets one.
3. Election windows: keep one day each, or shorten so rung 1 sees a cycle.
4. Justice: wire the built epistemic pipeline (recommended, it is the design
   the research needs) or grow the simple model.
5. Should `petition` be hidden from non-seats; a citizen petition is a
   different object from a land petition to the President.
6. Temple: seed as a RELIGION organisation with a keeper role, or let
   residents register one.
7. Is the browser's government model meant to converge with the world or to
   be retired (UI-04).

## 5. Work items, in order

| # | Item | Findings | Effort | Depends on |
|---|---|---|---|---|
| I1 | Election tick job, charter sink rewired, civic notice line, canonical places into charter engines, candidacy wake fan-out | GOV-01/04/17/20, CORE-02, TOOL-08 | S | none |
| I2 | Caretaker government at genesis or public petition | GROW-12 | S | I1 |
| I3 | Case polarity and ground truth; auto-release; fines as FINE | GOV-02/15/16, TOOL-22 | M | none |
| I4 | `table_amendment`, `vote_amendment`, registry suffrage removed, veto window with auto-sign | GOV-03/13 | S | I1 |
| I5 | Permits, licences, jobs, tenders as tools; treasury day job; margin applied | GOV-07/08/09, ECON-09/10 | M | doc 06 E2 |
| I6 | Organisation membership, account, roles, decisions | GOV-10, TOOL-30 | M | none |
| I7 | Rulesets with the Charter as the government's ruleset; temple as a RELIGION organisation | ROADMAP T6, GOV §5.2 | L | I6 |
| I8 | Wire or delete `world/crime/`; interview and warrant tools; checkpoint the store | GOV-05/12 | L | I3 |
| I9 | Trace writers for `petitions`, `organizations`, `cases`; `social_api` mounted; one rulebook | INFRA-17, GOV-18/19 | S | none |
| I10 | Expansion resolution and veto window; approval opens a tender | GOV-11, doc 05 G7 | S | I4, doc 05 G6 |
| I11 | Production-path wiring tests: an election tallies in a mock run, a crime engine exists on the production host, S1 asserts sim | GOV-24 | S | I1, I3 |
