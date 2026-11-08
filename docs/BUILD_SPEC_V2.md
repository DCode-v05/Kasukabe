# Build Spec v2 — the town that talks

**What this is.** The specification for the next build of Kasukabe, written
*before* the code, as the thing the tests and evals are derived from. It states
what must be true, what must be proved, and what evidence counts. It contains no
test code and no implementation. It is the contract a test suite and an eval
suite are written against, in that order, before a line of production code moves.

**Why it exists.** Build Spec v1 was written after the system, so its live-eval
thresholds were chosen after seeing results — which the conformance audit
correctly refused to grade as passes. This spec is written before the work, and
every threshold in §9 is fixed here, in advance, with the reasoning attached.

**Status.** Draft for approval. Nothing in §3 is built. Nothing in §4 or §5
exists yet. The defects in §2 are measured, reproduced, and root-caused at the
line level.

---

## §0 How to read this

| Section | What it fixes |
|---|---|
| §1 | What "done" means. Three gates, no exceptions. |
| §2 | The defect register. Evidence, not suspicion. |
| §3 | Objectives — what the build must make true. |
| §4 | Deterministic tests — what must be provable without a model. |
| §5 | Live evals — what must be proved *with* real models. |
| §6 | Error handling and edge cases. |
| §7 | Observability — what must never again be invisible. |
| §8 | The cast: ten agents, two families. |
| §9 | Thresholds, agreed in advance. |
| §10 | Evidence rules — what is rejected. |
| §11 | Harness rules — failure isolates, review at the end. |
| §12 | Order of work and the TDD protocol. |

---

## §1 Definition of done

A change is **not done** until all three gates pass. Any one missing means the
work is unfinished, regardless of how much code exists.

**Gate 1 — wired to a live call site.** The code is reached by the production
path, not only by its own test. A module nothing imports, an event nobody
subscribes to, a route no host mounts, and a config key nothing reads all pass
their own unit tests and are all still broken. The proof obligation is a test
that severs the wire and fails.

**Gate 2 — proven by a real, non-mock, end-to-end test.** At least one test
exercises the whole chain with the real composition root, a real world server,
real engine processes, and — for anything on the cognition path — a real model.
A mock on either end of the seam under test invalidates the proof. Mocks remain
legitimate for the *other* seams in that test.

**Gate 3 — fails loudly.** When the thing breaks, something goes red within one
tick, names the hop that broke, and refuses to continue if continuing would
produce a misleading result. Silence, a swallowed exception, a default that
substitutes for a missing value, and a green board over a dead dependency are
all failures of this gate.

**A corollary that has bitten this project three times:** a metric read from a
truncated or partial source is not evidence. If a body is capped at 64 KB, the
metric reads `_original_bytes`. If a run is still going, the number is labelled
mid-run or not reported at all.

---

## §2 The defect register

Every entry below was measured on this machine against the real trace stores in
`app/data/` and the fifteen run reports in `docs/runs/`. Each names its evidence
and its root cause.

### D1 — Streamed content is discarded by the broker's trace accumulator

**Severity: critical. This is the cause of "reasoning is empty, response is empty".**

`StreamAccumulator.ingest` (`app/broker/service.py`) keys every content block on
a `content_block_start` event. `anthropic_sse_events_from_converse_stream`
(`app/broker/translate.py`) emits `content_block_start` **only when Bedrock sends
`contentBlockStart`**, which Bedrock does for `toolUse` blocks and frequently
does *not* do for plain text — text arrives as bare `contentBlockDelta`. The
translator handles that case with `block_kind.setdefault(index, "text")` and
yields a `content_block_delta` with no preceding start. The accumulator then
looks up an index it has never seen, gets `None`, and **returns without
recording the text**.

Measured, `app/data/dryrun.db`:

| | |
|---|---|
| Successful calls with **zero** content blocks recorded | **938** |
| Shape of every one of them | `{"content": [], "stop_reason": "..."}` |
| Output tokens billed on those calls | **360,250** on the no-system bucket alone |
| Same defect, `kasukabe_l3.db` | 360 of 779 successful calls (**46%**) |

The engine is **not** affected: `herald/provider.py` yields a text `StreamEvent`
straight off `content_block_delta` without needing the start event. So the model
output reaches the agent and is lost only from the record. Every run report's
"Model — reasoning & output" column reads the trace store, which is why every
report shows empty model output while the town was in fact thinking.

**This is an observability defect with a cost consequence:** we paid for
360k output tokens we cannot read.

### D2 — Reasoning is never requested, and would be dropped if it arrived

Two independent faults compound.

1. **Never requested.** Of 6,000 successful calls sampled, **0** carried a
   `thinking` or reasoning configuration. The model is never asked to reason.
2. **Dropped on arrival.** `content_block_from_bedrock` translates
   `reasoningContent` into a `thinking` block — but that function is on the
   **non-streaming** path only. The streaming SSE translator handles
   `contentBlockStart`, `contentBlockDelta` (text and toolUse), `contentBlockStop`,
   `messageStop` and `metadata`, and has **no `reasoningContent` branch at all**.
   The sim streams. So even if reasoning were requested, it would be discarded.

Consequence: `session_log.jsonl`'s `reasoning` field is empty in every run ever
made, and "the model did not reason" is indistinguishable from "we threw it away".

### D3 — Agents think and do not act

The dominant behavioural defect, and the reason the town is inert.

Run 15, the best run to date, last 12 turns of each of ten engines:

| Engine | Turns shown | Succeeded | Made a tool call |
|---|---:|---:|---:|
| eight of ten | 12 | 12 | **0** |
| one | 12 | 12 | 3 |
| one | 12 | 11 | 11 |

**120 turns, 16 tool calls.** The agents are reasoning successfully and then
ending the turn without touching the world. This is not a permissions problem
and not a registry problem: 49 tools were served and reachable.

### D4 — Conversation delivery works; answering does not

Across all fifteen runs, utterances per run were 3, 5, 9, 23, 28, 15, 27, 9, 24,
4, 7, 4, 7, 5, and 135. Run 15's 135 is the only healthy number, and it decomposes
badly:

| | |
|---|---:|
| Exchanges | 81 |
| **Ended after one turn, no reply** | **56 (69%)** |
| Two or more turns | 25 |
| Longest exchange | 7 turns |

The conformance audit measured the same thing from the other side: of 11 remarks
addressed to a named resident, **2 were answered by the person asked**. Delivery
is wired and verified. Turn-taking is not.

`world_say` was called **9 times** in a whole 30-agent run, against `world_move`
86, `world_look_around` 53, `world_describe_place` 32. The town navigates instead
of talking.

### D5 — 61% of all spend is learning-centre background work, billed as cognition

Classifying every successful call in `dryrun.db` by its system prompt:

| What the call actually is | Calls | Share |
|---|---:|---:|
| Learning-centre knowledge extraction | 3,696 | 61% |
| No system prompt at all | 1,556 | 26% |
| Agent turns carrying a real persona | ~430 | 7% |
| Learning-centre episode creation | 135 | 2% |

Every one of them is tagged `purpose=unspecified` and therefore classified
`call_class=cognition`. Measured across four trace stores: **100% cognition, 0%
background, in every run.** ROADMAP RC-5 is unfixed. The consequences are three:
background work competes with agent cognition for Kimi lanes; it is billed to
agent wallets, which is what drives them into cognitive poverty; and the trace
cannot answer whether the learning centre ran.

### D6 — Refusal storms: the system pays to be told "no" thousands of times

| Trace store | Failure | Count | What should have happened |
|---|---|---:|---|
| `kasukabe_l3.db` | `SpendCapExceededError` | **5,155** | Stop dispatching at the cap |
| `run_full.db` | `SpendCapExceededError` | 3,014 | Same |
| `dryrun.db` | `InsufficientFundsError` | **3,651** | Stop scheduling a broke agent |
| `kasukabe.db` | `UnknownAgentError` | **5,921 (100%)** | Refuse to boot |

`kasukabe_l3.db` ran 5,994 calls of which 5,155 were refusals against a cap
already reached — the run continued for thousands of calls after it was over.
`dryrun.db` shows agent `a0064` dispatched repeatedly at 0, 1 and 2 cents against
a 3-cent estimate. `kasukabe.db` is 5,921 calls at a 0% success rate, every one
`agent 'kasukabe-sim-broker' is not known to world's ledger` — a whole run where
the broker sent a service name where an agent id belonged, and nothing stopped it.

These are the `broker-internal ×325`, `bedrock ×260`, `ledger ×61` bars in the
reported run. They are not upstream flakiness. They are missing back-pressure.

### D7 — `toolConfig` rejections when history contains tool blocks

`Bedrock ValidationException: The toolConfig field must be defined when using
toolUse and toolResult content blocks.` Measured: of 4,000 sampled requests, 36
carried `tool_use`/`tool_result` blocks in history while declaring no tools.
Bedrock hard-rejects those. Low volume in the current build, guaranteed to grow
with conversation length, and unrecoverable when it happens because the history
cannot be un-said.

### D8 — The engine's native identity leaks into the perception

Real perception text observed in the trace:

> `<system-reminder>This task appears complex. Consider calling request_plan to
> create a structured approach before proceeding.</system-reminder>` followed by
> *"It is game-day 0, 06:00 (day). You are Esme Jarrah, the town's child."*

`request_replan`, `search_content` and `list_content` appear in the tool-call
census. This is round 1's RC-1 in a quieter form: the engine still frames the
turn as a task to be planned rather than a life to be lived, and it does so
inside the prompt the persona is trying to own.

### D9 — Four durable tables never fill on this machine

Every local trace store: `utterances=0`, `memories=0`, `transactions=0`,
`skills=0`. Only `llm_calls` and `events` have rows, in all seven databases.
The conformance report shows these filling on the audited build, so this is
either a host-specific wiring break or a regression — either way the four
recorders are unproven here and must be proved by §4.3.

### D10 — Sixty unaudited silent defaults

The audit found 60 `getattr(obj, "field", default)` sites in `world/`. One was a
live bug that reported dormancy as zero for **every run ever made** — dormancy
being the last step of the starvation escalation this project exists to measure.
Fifty-nine remain unchecked.

---

## §3 Objectives

Seven objectives. Each states what must be true, and what would count as proof.
None may be graded on a mid-run sample (§10).

### 3.1 A conversation is a loop, not a broadcast

Two co-located residents must be able to hold an exchange in which each speaks,
each hears, and each replies to what was actually said — sustained over at least
four alternating turns, with the content of turn *n+1* demonstrably conditioned
on turn *n*.

**Expected behaviour.** A resident who is addressed by name, is still present,
and has an open question directed at it will answer it before pursuing an
unrelated errand. A resident whose question goes unanswered may repeat, rephrase,
or give up — all three are acceptable; silence with no internal trace is not.

**Proof.** Reconstructible from the trace alone: speaker, listener, location,
tick, content, and the perception that carried it to the listener.

### 3.2 The model's whole output is captured, always

Every turn records what the model **said** and what it **reasoned**, separately.

**Expected behaviour.** Text output is present on every turn that produced any.
Reasoning is present whenever the model produced it and provably absent — not
merely empty — when it did not. "Did not reason" and "we discarded the reasoning"
must never look the same.

**Proof.** Both fields present in the session log and in the trace store, with a
count of streamed deltas that matches the assembled text. A turn billed for *N*
output tokens whose recorded content is empty is a **failure**, not a nil result.

### 3.3 Thinking and text arrive as blocks and as deltas

The system must carry, end to end: `thinking` deltas, `thinking` blocks, `text`
deltas, and `text` blocks — from Bedrock, through the broker's streaming
translation, through herald, onto the engine's event bus, into the session log
and the trace store.

**Expected behaviour.** A streamed text response with no `contentBlockStart`
event is assembled correctly. A `reasoningContent` block on the streaming path is
translated, not dropped. Deltas are observable while a turn is in flight, not
only after it ends.

### 3.4 The loop closes: output feeds the next input

A model's output, and every tool result it caused, must be present in the input
of that agent's next turn. An agent must be able to see what it just did and what
came back before deciding what to do next.

**Expected behaviour.** After a tool call, the result reaches the model and the
model gets another turn to act on it within the same tick where the engine's own
loop allows it. A turn that ends immediately after a tool call, with the result
never shown to the model, is a broken loop.

### 3.5 Agents act, and acting is the normal case

The dominant failure today is thinking without acting (D3). A turn that produces
neither a tool call nor an utterance is a wasted turn, and the build must make
wasted turns rare and visible.

**Expected behaviour.** Over a settled run, the majority of turns produce at
least one world effect. Turns that produce none are counted, attributed, and
surfaced — never averaged away.

### 3.6 Failure stops the run before it wastes the budget

No run may continue dispatching against a condition that cannot succeed: a
reached spend cap, an agent with no money, an unknown agent id, an unreachable
endpoint, or a dependency that has gone away.

**Expected behaviour.** The first occurrence is recorded with its hop. A short
run of consecutive identical failures stops dispatch for that scope — the agent,
the lane, or the whole run as appropriate — and says which, in one line, naming
the hop and the remedy. Thousands of identical refusals are a defect in this
system, not a property of the upstream.

### 3.7 Every call is honestly classified

Agent cognition, learning-centre work, summarisation and importance scoring must
be distinguishable in the trace, routed to their intended lanes, and billed to
the intended payer.

**Expected behaviour.** `call_class` reflects what the call actually is.
Background work runs on the background lane. An agent's wallet is not drained by
the engine's own housekeeping unless that is a deliberate, stated design choice.

---

## §4 Deterministic test requirements

What must be provable without spending a cent. These are not smoke tests. Each
clause below must be hard enough that the defect it targets could not have
reached production past it.

**4.1 Severing any wire fails a test.** For every recorder, subscriber, route
and config key introduced or touched by this build, a test must exist that
disconnects it and observes a failure. The test must fail when the wire is cut
and pass when it is restored — verified by actually cutting it, not by assertion.

**4.2 Streaming assembly is proved against adversarial event orders.** The
streaming translator and the accumulator must both be driven by synthetic Bedrock
event sequences that include, at minimum: text with no `contentBlockStart`;
interleaved text and toolUse blocks at different indices; a `contentBlockDelta`
arriving for an index that never started; a block that starts and never stops; a
`reasoningContent` delta; a stream that ends mid-block; and out-of-order indices.
Every one must assemble to the correct content or fail loudly, never silently
produce empty content. **This clause exists because of D1 and is the single most
important test in the suite.**

**4.3 Every durable recorder is proved to write.** A parametrised test names
every table the system claims to fill — `utterances`, `memories`, `transactions`,
`skills`, `businesses`, `tools_registry`, `relationships` — and fails on any that
is neither written by a live path nor listed as deliberately excluded with a
stated reason. Zero rows is a failure, not a pass.

**4.4 No silent defaults on any field that carries a finding.** Every
`getattr(obj, field, default)` in the paths this build touches is audited. Any
field whose absence would change a reported number must be read directly, so a
rename breaks the line instead of resuming the lie. The sixty existing sites are
inventoried and each is either fixed or explicitly justified.

**4.5 Back-pressure is proved to engage.** Given a reached spend cap, an
exhausted wallet, an unknown agent, and an unreachable endpoint, each in turn:
dispatch must stop within a bounded number of attempts, and the test must assert
the bound. A test that allows thousands of retries passes today and is worthless.

**4.6 Fault injection turns a board red.** Kill an engine, break the ledger,
delete the trace file under a running broker, and make Bedrock unreachable —
each must change a health surface within one tick, and the test must assert the
change. Every green board in this project's history is currently an untested
green.

**4.7 Purpose classification is exhaustive.** Every call site that reaches the
broker is enumerated, and each is asserted to carry the correct purpose tag. A
new unclassified call site must fail the test rather than default to cognition.

**4.8 A turn's record is complete or the turn is marked incomplete.** Given a
turn that produced output tokens, the assembled record must contain output. A
test drives a real streamed response through the whole recording path and asserts
that billed tokens and recorded content cannot disagree.

**4.9 The conversation loop is deterministic under a scripted model.** With a
scripted model that always answers a directed question, an exchange must reach
four alternating turns. This isolates the *wiring* of turn-taking from the
*behaviour* of the model, so §5 measures only behaviour.

**4.10 Restart is clean and lossless.** An engine killed mid-turn restarts,
recovers its context from its own session log, and continues. Nothing is
double-charged and nothing is lost.

---

## §5 Live eval requirements

Run against **real models** and real engines. These measure behaviour, and
behaviour cannot be unit-tested. Every threshold is in §9 and was fixed before
the first run.

**5.1 The sustained conversation eval.** Two families, ten residents, seeded with
real reasons to talk (a debt, a shared meal, an unfilled order). Measure: reply
rate to directed questions, exchange depth, whether the reply addresses the
question asked, and whether any conversation leads to a settlement — a `say` that
agrees a number followed by a `buy`/`sell`/`transfer` at that number.

Adversarial variants, all required: a resident addressed by two people at once; a
resident asked a question by someone who then walks away; two residents who both
open with a question; a question addressed to someone not present; and a
conversation interrupted by a third party.

**5.2 Zero phantom entities.** No remark addressed to a resident who does not
exist. No agent standing in a place the town does not have. No tool called by a
name the registry does not serve. The threshold is zero and needs no negotiation.

**5.3 Induced poverty.** A resident seeded at zero with no household support must
be observed through the full escalation: unable to act, hungry, ill, dormant, and
either revived by another agent or dead. The escalation has never once been
observed end to end. The eval must also confirm nobody else's outcome changed as
a side effect.

**5.4 Adversarial ledger play.** Deliberately attempt: selling to oneself,
registering a duplicate business, paying from an empty account, transferring a
negative amount, and two concurrent debits racing on one wallet. The invariant
must hold and each attempt must be refused with a distinguishable error.

**5.5 Authored capability is executed.** A resident authors a multi-step workflow,
and that workflow is then **walked end to end**, its proficiency rises, and the
rise lowers a real cost. Authoring alone does not pass this objective. If no tool
exists that runs a skill, this eval fails and says so.

**5.6 Refusal recovery.** Given a legitimately refused action with a correction
attached, measure whether the same agent later succeeds at the same tool. The
eval requires a run in which something is legitimately refused; a run with zero
refusals cannot compute a rate and must be reported as not-measurable rather than
as a pass.

**5.7 Cost and prompt discipline over distance.** Prompt size must plateau rather
than grow. Measured from the untruncated figure, first third against last third,
over a run long enough for compaction to fire several times.

**5.8 Endurance.** A run of the agreed length completes with no cancelled
sessions, a complete trace, and — if it ends early — ends by a guard that names
the reason, not by a surprise.

**5.9 The wasted-turn eval.** Directly targets D3. Measure the share of turns
producing no world effect, and the share of spend those turns consumed. Attribute
by agent and by cause.

---

## §6 Error handling and edge cases

What the build must do when things go wrong. Each is a required behaviour, and
each needs a test in §4 or an eval in §5.

**Upstream.** A throttle is retried under the existing circuit breaker and never
spin-retried. A validation error is never retried, because it will fail
identically. A request too large is a bug in prompt construction and must halt
that agent's dispatch and say so, not be retried into the same wall.

**Ledger.** A debit that fails after a successful model call must not turn a
paid-for response into a 500. The uncollected cost is carried, logged, and
queryable. An agent that cannot pay stops being scheduled rather than being
dispatched into a refusal thousands of times.

**Identity.** An agent id the ledger does not know must fail the run at boot, not
5,921 calls in.

**World.** A tool call for a tool that does not exist is a capability gap and is
recorded as one. A location gate refusal carries the location the agent should
have been at. A refusal must never leave world state half-mutated.

**Engine.** A process that dies is restarted by the watchdog with backoff and a
restart budget. A wedged engine that still reports healthy is caught by staleness,
not by state alone. A turn that overruns its dispatch deadline is counted, and the
calls it already paid for are attributed to it.

**Trace.** A trace store that cannot be written halts the run. A body over the cap
is truncated with the original size recorded. A metric computed from a capped body
must read the original size or refuse to report.

**Conversation.** A remark addressed to an absent listener is refused with a
reason the speaker can act on. A remark addressed to oneself is not delivered back
as an open question. An unanswered question stays open for a bounded window and
then expires, visibly.

---

## §7 Observability requirements

The rule: **anything that can be wrong must be visible while it is wrong, not
after the run.**

Required, and absent today:

1. **Per-turn completeness.** For every turn: input, reasoning, text output, tool
   calls with results, stop reason, token counts, cost, model, lane, and whether
   the turn produced a world effect. A turn missing any of these is flagged in the
   record itself.
2. **Delta-level visibility.** Counts of text deltas and thinking deltas per turn,
   so an assembly bug shows as a mismatch between deltas seen and content recorded.
3. **Billed-versus-recorded reconciliation.** A standing check that output tokens
   billed and output content recorded agree. D1 would have been caught on day one
   by this single line.
4. **Purpose and lane on every call**, with a report that shows the split and
   fails a preflight check if background share is zero over a run long enough to
   have run the learning centre.
5. **Conversation health.** Directed remarks, delivered remarks, answered remarks,
   exchange depth distribution, and unanswered-question age — live, not
   post-hoc.
6. **Wasted-turn and wasted-spend counters**, attributed by agent and cause.
7. **Refusal storm detection.** Consecutive identical failures per scope, with the
   threshold that stops dispatch shown next to the count.
8. **The perception, verbatim, as sent.** Including any engine-injected reminders,
   so D8-class contamination is visible rather than inferred.
9. **A liveness surface that has been observed failing** — §4.6.

---

## §8 The cast: ten residents, two families

The run that proves conversation must make conversation *likely* before it
measures whether it happens. Thirty residents scattered over a large map produced
almost no talk, and the reason was arithmetic rather than behavioural.

**Requirements:**

- **Ten residents in two households**, related by blood or marriage, with a small
  number of cross-family ties so the two families have reasons to meet.
- **Everybody knows everybody by name**, and every persona names specific others
  with a specific shared fact — a debt with an amount, a grudge with a cause, an
  unfilled order, a promise. Each fact exists on both sides, consistently.
- **Co-location by construction.** Homes, workplaces and the market are close
  enough that residents are routinely in the same place at the same time. Being
  alone must be the exception.
- **Spread across ages and trades** so dependency is real: at least one minor, at
  least one retired resident, and earners whose trades feed each other — one who
  grows, one who mills, one who bakes, one who sells.
- **At least one genuine conflict at genesis** that cannot be resolved without
  two residents talking to each other.
- **Reproducible from a seed**, and small enough to run repeatedly for a few
  dollars.

The cast is a research instrument, not set dressing: if a conversation does not
happen in this town, the finding is about the agents, and that is only true if
the town gave them every reason to speak.

---

## §9 Thresholds, agreed in advance

Fixed here, before the first run. Choosing any of these after seeing a result is
forbidden by §10.

| # | Measure | Threshold | Why this number |
|---|---|---|---|
| 9.1 | Reply rate to directed, answerable questions | **≥ 60%** | Today it is 18% (2 of 11). Below half is not a conversation. |
| 9.2 | Exchanges reaching 4+ alternating turns | **≥ 3 per run** | One sustained exchange could be luck; three is a behaviour. |
| 9.3 | Turns producing a world effect | **≥ 50%** | Today it is 13% (16 of 120). Half is the minimum for a town that does anything. |
| 9.4 | Turns billed for output but recording none | **0** | D1. Any nonzero value means the record is lying. |
| 9.5 | Reasoning captured when the model reasons | **100%** | Presence or provable absence. No third state. |
| 9.6 | Background share of calls, over a run that sleeps | **> 0%** | Today it is 0% in every run. |
| 9.7 | Consecutive identical refusals before dispatch stops | **≤ 20** | Today it is 5,155. |
| 9.8 | Phantom entities | **0** | Non-negotiable. |
| 9.9 | Prompt size, last third vs first third | **≤ 1.25×** | Bounded growth, not flat — compaction should fire, not freeze. |
| 9.10 | Settlements (a spoken price followed by a matching trade) | **≥ 1 per run** | The single clearest proof that talking changed the world. |

A run that misses a threshold is a **fail**, reported as one. A threshold that
turns out to be wrong is renegotiated **before** the next run and the change is
recorded with its reason.

---

## §10 Evidence rules

What is rejected as evidence, learned from three audit errors already committed
in this project:

1. **A mid-run sample presented as a result.** Report the whole run or label the
   sample as mid-run. Reading tick 132 of a run that reached 631 and writing it up
   as the outcome happened, and it inverted the conclusion.
2. **A metric read from a truncated source.** Prompt sizes read from a capped body
   measure the cap.
3. **Matching a string in a comment or docstring.** A wiring audit that greps for
   a function name will find the docstring explaining why it was deliberately left
   out. Parse the code with docstrings stripped.
4. **A threshold chosen after the result.** §9 exists for this.
5. **A green board over an untested dependency.** §4.6.
6. **A passing test that never fails.** Every test introduced by this build is
   verified by breaking the thing it covers and watching it go red.
7. **"It works on the other machine."** Four durable tables are empty on this host
   and full on another. Until that is explained, neither is evidence.

---

## §11 Harness rules

**Failure isolates, work continues.** A failing eval or test does not stop the
build. It is captured with its evidence, quarantined, and the remaining work
proceeds. A build blocked on the first red light produces one fix and no map.

**Everything is reviewed at the end.** Individual green lights are not the
deliverable. The deliverable is a single review pass over the whole board, at
which point every failure is triaged together — because failures in this system
cluster, and three symptoms have repeatedly turned out to be one cause.

**Every eval run is recorded as its own document**, kept, never overwritten, in
the same shape as `docs/runs/`. The engines that produced it are gone the moment
it stops, and the report is the only thing left of them.

**Real models, real engines.** Evals that mock inference measure the mock.

---

## §12 Order of work and the TDD protocol

**The protocol, per objective:**

1. Write the tests and evals for the objective, from §4 and §5. No production code
   is touched.
2. Run them. They must fail, and the failure must be the one expected. A new test
   that passes immediately is either testing nothing or the objective was already
   met — establish which before continuing.
3. Write the smallest change that makes them pass.
4. Break the change deliberately and confirm the tests go red.
5. Verify the three gates of §1 explicitly, in writing.

**The order, by how much each unblocks:**

| # | Work | Unblocks |
|---|---|---|
| 1 | D1 — streaming assembly and the billed-vs-recorded check | Everything. Until the record is honest, nothing else can be measured. |
| 2 | D2 — reasoning requested and carried on the streaming path | 3.2, 3.3, and the ability to see *why* agents do not act |
| 3 | §8 — the ten-resident, two-family cast | 3.1, and every conversation eval |
| 4 | D3/D4 — the conversation loop and turn-taking | 3.1, 3.4, 3.5 |
| 5 | D6 — back-pressure and refusal storms | 3.6, and the cost of every run after this |
| 6 | D5 — purpose tagging | 3.7, and returns the budget to the agents |
| 7 | §7 — the remaining observability surfaces | Makes the next defect findable in a day instead of a week |
| 8 | D9/D10 — the empty recorders and the silent defaults | Confidence that any number reported is real |

Items 1 and 2 are prerequisites for measuring anything else and are not
parallelisable with the rest. Items 3 through 8 may proceed in parallel under §11.

---

## §13 What this build is expected to look like when it is finished

A run of ten residents across two families, on real Bedrock, on plain OS
processes with no container, for a bounded number of game-days and a bounded
budget, which produces:

- A conversation an outside reader can follow: several exchanges of four or more
  turns, in which residents answer the questions they are asked, and at least one
  in which talking changes what somebody then does.
- A per-turn record in which the model's reasoning and its words are both present
  and legible, and in which no turn was billed for output that the record does not
  contain.
- A majority of turns that produce a world effect, and an honest count of the ones
  that did not, with reasons.
- A run that, if it fails, stops itself and names the hop — rather than spending
  the remaining budget being refused.
- A trace from which every claim above can be independently recomputed by someone
  who did not watch the run.

Anything less is a run that produced numbers rather than findings.

---

*September · internal research · Build Spec v2 · written before the build, against
measured defects · thresholds fixed in §9 in advance*

---

## §14 Findings from the first build against this spec

Recorded here rather than fixed, per ROADMAP Law 1: a change needed under
`engine/src/` outside `sim/` is a finding to write up, not a thing to patch
around.

### F1 — Nothing asks the model to reason, and the sim cannot ask

Threshold 9.5 is **not met**, and cannot be met from `app/` alone.

The carry path is now complete and tested: broker's streaming translator
turns a Bedrock `reasoningContent` delta into a `thinking_delta` (the one
spelling herald's stream parser understands), the accumulator records it as
a `thinking` block, and `build_converse_request` turns an Anthropic
`thinking` parameter into Bedrock's `reasoning_config`, clamped below
`maxTokens`. Fourteen tests cover it.

What is missing is a caller. `herald/provider.py` sets `thinking` only for
tiers resolved through `SUPER_THINKING_BUDGET` and `THINKING_LATCH_BUDGET`,
and the sim's pinned topology never resolves either: `pinned_boot` builds
one guarded adapter for one model id and never calls `herald_api.init`, so
the per-turn model-routing block that could select such a tier is dead code
in this process. Making the sim request reasoning means changing how herald
chooses a tier, which is frozen code.

**Two honest options**, neither taken here:

1. Write it up as an engine finding and have whoever owns `herald/` expose a
   settings-level reasoning budget. This is the Law 1 path.
2. Have the sim seam set the field on the request it already builds. This is
   legal under Law 1 (`engine/src/sim/*` is the seam) but would mean the sim
   constructing wire-level request bodies herald otherwise owns, which is a
   layering violation traded for a research capability. Worth a deliberate
   decision, not a quiet one.

Until one is chosen, `session_log.reasoning` stays empty, and it stays empty
for a reason that is now written down and testable rather than invisible.
There is also no evidence yet that Kimi K2.5 on Bedrock supports a reasoning
config at all: that question could not previously be asked, and can now be
answered by one live call whenever someone wants it.

### F2 — Defect D9 was a stale-database artifact, not a live break

The spec recorded four durable tables reading zero on this host. Measured on
the verification runs, with the same code: `utterances` 39, `transactions`
631, `memories` 270, `skills` 15, all carrying real content. The zeros came
from trace stores written by earlier builds. Evidence rule §10.7 applied to
this correctly and the spec's own suspicion was wrong.

### F3 — Run reports count housekeeping as turns

"Twelve turns shown, zero made a tool call" in the run reports counts the
learning centre's post-turn summariser as an agent turn. Those calls never
call tools, and roughly one fires per real turn, so the reports have been
overstating how idle the town is. Now separable, because `call_class` is
finally populated: the report should filter to `cognition` and say so.
