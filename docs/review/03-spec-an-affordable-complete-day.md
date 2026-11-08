# Spec: a game day that is complete, affordable, and remembered

*Cross-cutting specification drawn from `10-engine-seam-and-mind.md` (ENG), `14-world-core-runner-and-record.md` (CORE), `15-broker-trace-and-infra.md` (INFRA) and `11-world-tools-and-physics.md` (TOOL). Everything else in this review set assumes a run can cross a night. None has.*

## 1. The target, stated so it can be failed

A twenty-resident town lives a full game day, including a night, for about
two dollars and about five minutes of wall clock; a 250-resident town for
about twenty dollars. Residents wake to what happened while they slept,
their memory has consolidated, their evolved soul has moved, and the
mechanics that depend on a day boundary (upkeep, tax, payroll, learning,
dormancy) have all fired and are in the trace.

Acceptance, on a rung-1 run (10 to 20 residents, two game-days):

1. Two day boundaries crossed under the spend cap; p95 input tokens per
   call at or below 8,000; spend per resident-day at or below $0.15.
2. The 48 night ticks complete in under a minute of wall clock with no
   cognition calls beyond the sleep-onset edge; a remark made at 23:00 is
   delivered at dawn.
3. Every resident has at least one episode, one knowledge row and a
   non-empty evolved-soul diff after the first night.
4. No request on the wire contains a compaction marker, an assistant
   reminder, or the engine's product name.
5. No turn is discarded after paying; no dormant resident is dispatched.

## 2. Where it stands today

| Fact | Finding |
|---|---|
| Ticks are unpaced: the loop never sleeps, so pacing is turn latency (~63 s per tick in run 23); `--tick-interval-s` is dead | CORE-01 |
| The learning centre cannot write a single row on the sim path; batches re-send the whole history (55K to 108K tokens) | ENG-01 |
| Every model call is emergency-compacted; the day-0 perception is pinned into every prompt; tool results are gutted | ENG-02 |
| All ~79 tool schemas (~13K tokens) go on every call; the location-reduced schemas never reach the wire because herald's cache is keyed on the name set | ENG-03, ENG-16 |
| Learning signals grow quadratically; the whole working memory is flushed after every turn | ENG-04 |
| Ten items of assistant scaffolding are still on the wire (complexity nudge, struggle injection, continuation prompt, compaction markers) | ENG-07 |
| Kimi's text-form tool calls are parsed by nothing | ENG-08, INFRA-07 |
| History is stored as prose only, which reinforces prose-only turns | ENG-09 |
| The 120 s dispatch limit discards paid turns while the engine keeps running them | CORE-11, INFRA-29, ENG-12 |
| Night is neither cheap nor consolidated: sleepers are still dispatched, continuing actions work through the night, interrupts are cleared | CORE-17 |
| Dormant and dead residents keep being dispatched and refused | CORE-10, INFRA-03 |
| The pre-halt learning drain is only called from the test-only burst loop | CORE-05 |
| The learning batch runs inline before the turn, inside the same deadline and lane | ENG-17 |
| Upkeep and the other day jobs run on the first tick, not at a boundary | CORE-04, ECON-02 |

## 3. The mechanism

### 3.1 Pacing: decouple the clock from the turns

- Sleep the remainder of the interval after each tick; derive the interval
  from measured calls per turn against lane capacity (CORE-01, CORE-12,
  INFRA-12). At 20 residents and 200 RPM that is about 3 s per tick; at 250
  residents and 600 RPM about 40 s.
- Turns are asynchronous: the runner keeps an in-flight set, never
  re-dispatches a resident whose turn is running, never cancels; the engine
  answers 409 to a tick that arrives mid-turn, and cancels on client
  disconnect (a `sim/*` change). A per-resident daily call budget replaces
  the wall-clock kill (CORE-11, INFRA-29, ENG-12).
- Wake on events: cadence becomes a ceiling (once per game-hour), and
  residents otherwise wake on interrupts, arrivals, completions and needs
  (hunger, upkeep due, obligation overdue). Target at most ten turns per
  resident-day instead of 32.
- `keep_doing` is the default shape of work; a mastered workflow runs under
  it with no model call (doc 04, TOOL-06).
- Skip the dead and the dormant; unregister on death; suspend the engine on
  dormancy (CORE-10, INFRA-03).

### 3.2 Night

At `not is_daytime`: no per-sleeper dispatch except one sleep-onset call per
resident; continuing actions suspend at 22:00 and resume at dawn; overnight
interrupts are kept and delivered at dawn; the 48 night ticks run in burst
mode while hourly life ticks still run (CORE-17). The learning batch moves
off the tick's critical path and onto the background lane (ENG-17).

### 3.3 Memory that works

In the seam (`engine/src/sim/pinned_boot.py`), construct the episodic,
knowledge, social and soul stores with `embedding_service=None`, pass them
to `MemorySystem` and `ConsciousnessManager`, wire `LCPipeline(on_data_changed=invalidate)`,
so batches write, signals are deleted, consolidation runs and the DYNAMIC
layer (evolved soul, social graph) finally renders (ENG-01). Add a seam
`SignalStore` with a per-thread flush watermark and a cap (ENG-04). Put the
world-as-corrector loop in the seam rather than chasing the engine's
skill-evolution step, which keys on a human corrector (ENG-05).

### 3.4 A prompt that is small and the resident's own

- Stop per-call emergency compaction (`DISABLE_AUTO_COMPACT=1`) and bound
  history in the seam with a `ConversationStore` view: the last three
  exchanges as compact action records ("I said to Marta … heard by X, Y. I
  bought 2 bread for 60 sim.") plus a rolling summary in the resident's
  voice; raise the context window knob so caps stop truncating world results
  (ENG-02, ENG-09).
- Tools at or below 4K tokens: the world serves at most about twenty full
  schemas by location, role and seat and a one-line index for the rest;
  price prose stripped from descriptions; the seam resets herald's tool
  cache on refresh (ENG-03, ENG-16).
- Strip the scaffolding: extend the resident-voice adapter to
  `stream_with_tools`; drop the complexity nudge and the struggle injection;
  rewrite the continuation prompt; zero struggle state at tick start; cap
  output around 700 tokens with continuations suppressed (ENG-07, ENG-21).
- Parse Kimi's tool-call markup at the broker so those actions are real and
  traced (ENG-08, INFRA-07); accept `world_` and `world:` names everywhere a
  resident names a tool (TOOL-13).
- Report a prose-only turn back as a fact ("your last turn reached nobody
  and changed nothing"), which the runner already does after two silent
  turns; make it one.

### 3.5 The day boundary

Day jobs at real boundaries, once, for both hosts (doc 06 §4.1); the
pre-halt learning drain called from the continuous loop with a reserve that
is a share of the cap (CORE-05); pacing applied on resume (CORE-07).

### 3.6 Perception

A structured "ledger of self" block each tick: holdings including non-food,
storages, the business's till and stock for owners and staff, debts owed and
owing, wages owed, arrears, the active commitment and its repetitions, the
journey in progress with its arrival tick, today's upkeep and tax, and the
goals block from doc 04. Move the household roster to the STABLE persona
layer so per-tick text does not grow with population (CORE-16). One
open-questions mechanism, dropping a question when the asker has left the
place (CORE-15, TOOL-17).

## 4. Cost model, measured not modelled

| Quantity | Run 23 | After this spec |
|---|---|---|
| Input tokens per turn | 15K to 20K | ≤ 8K p95 (tools ≤ 4K, history ≤ 2K, persona ~1.3K, perception ~0.5K) |
| Model calls per resident-tick | ~1.7 mean, 12 max | ≤ 4 mean under a daily budget |
| Turns per resident-day | 32 by cadence | ≤ 10 by events |
| Learning-centre call size | 55K to 108K | ≤ 15K, on the background lane, at night |
| Spend per resident-day | ~$2 measured | ≤ $0.15 |
| Wall clock per game day, 20 residents | ~2.5 h at 63 s per tick | ~5 min |

The re-rating of the two currencies in doc 06 §4.4 depends on the last row.

## 5. Decisions the operator has to make

1. History bounded by the seam (recommended) or the engine's compaction
   kept with a raised window and a rewritten compaction prompt.
2. Kimi for everything (the seam's single-model rule) or DeepSeek for the
   learning centre and summariser as DESIGN §4 says.
3. Whether the learning centre is billed to the resident's wallet, to a
   `learning` account, or only runs at night (INFRA-19).
4. Where Kimi markup recovery lives: the broker, which changes what the
   trace records as a tool call (recommended), or the seam, preserving the
   raw text.
5. Whether event-driven wakeups with a per-day budget may replace the
   cadence-3 baseline, which changes what a tick means in every report.
6. A Law 1 manifest for the two post-vendoring engine commits (ENG-25), or
   revert and re-express them in the seam.

## 6. Work items, in order

| # | Item | Findings | Effort | Depends on |
|---|---|---|---|---|
| D1 | Wire the durable memory stores in the seam; signal watermark | ENG-01, ENG-04 | S-M | none |
| D2 | Disable auto-compaction; seam conversation view; window knob | ENG-02, ENG-09 | M | none |
| D3 | Tool schemas ≤ 4K on the wire; reset herald's cache on refresh | ENG-03, ENG-16 | M | none |
| D4 | Strip scaffolding; continuation cap; struggle reset | ENG-07, ENG-21, ENG-22 | S-M | none |
| D5 | Kimi markup parsed at the broker; prefixed names accepted | ENG-08, INFRA-07, TOOL-13 | S-M | none |
| D6 | Real pacing; asynchronous turns; daily call budget; skip dead and dormant | CORE-01/07/10/11/12, INFRA-03/29, ENG-12 | M | none |
| D7 | Night model: burst, suspend continuing actions, keep interrupts, learning off the critical path | CORE-17, ENG-17 | M | D6 |
| D8 | Day jobs at boundaries; drain from the continuous loop; proportional reserve | CORE-04/05, ECON-02 | S | none |
| D9 | Ledger-of-self perception; single open-questions mechanism; roster to STABLE | CORE-15/16, TOOL-04/17 | M | none |
| D10 | Behavioural tests replacing source-string tests; wire-shape assertions | CORE-23, ENG-26 | S | D1-D5 |
| D11 | The first run with a night, then five days, at rung 1 | all | run | D1-D9 |
