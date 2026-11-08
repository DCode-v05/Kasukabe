# Law 1 manifest: the engine changes that exist, and why they are allowed

*ENG-25. The operator's answer of 2026-09-04: a manifest, not a re-vendoring.*

## What Law 1 says

ROADMAP Law 1 freezes `engine/src/engine_core/`, `memory/`, `herald/` and
`learning_centre/`. Changeable: `engine/src/sim/*`, `app/world/*`,
`app/game/*`, `app/broker/*`.

The reason is not ceremony. The engine is vendored from an upstream that
this project does not control; every edit inside the frozen tree is a merge
conflict waiting for the next pull, and worse, it makes "is this the engine's
behaviour or ours?" unanswerable — which is precisely the question the
research depends on.

## The problem this manifest solves

Two commits landed inside the frozen tree after vendoring. Re-vendoring to
erase them would cost days and lose whatever they fixed. Pretending they are
not there makes the freeze a fiction, and a fiction is worse than an
exception: an exception can be audited.

So: the exceptions are listed here, and the list is the contract. Anything
inside the frozen tree that is not in this file is a violation.

## The exceptions

The vendored base is **`95e8788` "Vendor the Kasukabe engine" (2026-08-07)**.
Everything inside the frozen tree at that commit is upstream; everything after
it is ours and belongs in the table below.

```
git log --oneline 95e8788..HEAD -- engine/src/engine_core engine/src/memory \
    engine/src/herald engine/src/learning_centre
```

That returns **one commit**, not the two ENG-25 estimated. `0cb1115` is
counted twice in the finding because it also changed `engine/src/sandbox/`
and `engine/src/report/` — both outside `engine_core`/`memory`/`herald`/
`learning_centre`, so both outside the freeze. The freeze has been breached
twice: once by DCode-v05 in August, and once on 2026-09-10 (second row
below), at the operator's explicit instruction after the constraint was
raised and repeated.

| Where | What | Why it could not live in the seam |
|---|---|---|
| `engine/src/learning_centre/pipeline.py` (`0cb1115`, 2026-08-26, DCode-v05) — 40 added lines, 1 removed | `EPISODE_CONTEXT_MAX_SIGNALS = 120`, and a filter dropping `system_event`/`checkpoint` entries from the episode-encoder context. | The unbounded comprehension is inside `LCPipeline.run_batch`'s own body. The seam constructs the pipeline; it cannot reach into a local list comprehension in a method it calls. Wrapping `run_batch` would mean re-implementing it, which is a larger breach than the four-line one. |
| `engine/src/learning_centre/signals.py` + `pipeline.py` (2026-09-10, Sharan) — one new method, one changed call | `SignalStore.delete_batch(batch_id)`, and the batch's cleanup phase calls it instead of `delete_processed()`. | The cleanup ran `DELETE FROM learning_signals WHERE processed = 1` — every row claimed by ANY batch, not this one. Safe by accident while batches were six hours apart and never overlapped; a silent data-loss window now that `SimLearningGate` runs one every 18 waking ticks and `trigger_extra()` runs another at end-of-run (run 25's shutdown fired the drain twice). The learning-cadence spec named this first in its plan — *"the one change that must land BEFORE the cadence change, not with it"* — and the cadence change shipped without it. The `DELETE` is inside `run_batch`'s own cleanup phase, the same method body the row above could not reach: no seam can re-scope somebody else's `DELETE` statement. Guarded by `engine/tests/test_one_batch_cannot_eat_anothers_signals.py`, which demonstrates the old behaviour as well as the new. |

### What that change actually did

`task_context` trimmed each signal's `content` to 300 characters but capped
neither the signal **count** nor `system_event`, while both of its siblings in
the same file already filter `system_event`. A thread alive for six hours
replayed its whole history into one prompt: 118,179 records, 36,673 of them
bare `thread_state_change` noise, and 506 of that run's 893 failures were
"request body too large". Reproduced at 14.6 MB; 46.3 KB after.

The fix does what the two sibling call sites in the same file already do,
which is the narrowest form the change could take — a new rule invented here
would have been a larger exception than adopting the file's existing one.

### The other markers, and why they are not exceptions

ENG-25 also names "removed for the Kasukabe build" at `thread.py:2760,3258,3797`,
`coordinator.py:271,485`, `blocks.py:96` and `model_validation.py:30`. Those
strings are **in the vendored base** — they came with the engine and describe
upstream's own build variants. `git log -S` on each confirms no post-vendoring
commit introduced them. They are not breaches and must not be listed as such:
a manifest that logs upstream's comments as our exceptions is as misleading as
one that omits ours.

## Findings the freeze blocks

A finding whose only fix is inside the frozen tree is not "open" in the
sense the other 146 are - nobody can close it without an exception. Kept
here rather than in the register, so the freeze owns its own consequences.

| Finding | What it asks for | Why it cannot be done in the seam |
|---|---|---|
| **ENG-23** (P3) — post-turn summary subject is the first 40 characters of the perception | `thread.py:4277` builds subjects like "It is 09:10 on day 0. You are at th", and that string is the clustering key the learning pipeline groups knowledge by (`pipeline.py:483-492`), so every resident's knowledge clusters on the time of day rather than on what it was doing. | The subject is written inside `ExecutionThread`'s post-turn signal capture. The seam already rewrites the summary PROMPT (`resident_voice.py`) and that is the whole reach it has: the subject is assigned after the model answers, in frozen code, with no hook. Fixing it means either an exception or a seam-side re-clustering pass that ignores the subject - the second is real work, and it is not started. |

### Standing rule

Anything inside the frozen tree that is not in the table above is a violation.
Re-run the `git log` command at the top of this section to check; it is a
one-line audit, and it should return exactly the commits in the table
above — two as of 2026-09-10.

### What was deliberately NOT breached

The learning pipeline's four encoding system prompts were rewritten for
this simulation on 2026-09-10 — they described "a personal AI assistant's
memory" and asked for facts about "the user, their team, tools,
environment", which is why run 25 encoded a miller's morning as *"the user
exhibits consistent location-based workflow optimization"*.

That rewrite is **not** in this table, because it did not touch the frozen
tree. The four prompts are module-level globals that `pipeline.py` reads
at call time, so `engine/src/sim/resident_learning_prompts.py` rebinds them
from the seam and `pinned_boot.py` installs them before the pipeline is
constructed. Same behaviour change, no merge conflict at the next upstream
pull, and the answer to "is this the engine's behaviour or ours?" stays
readable. Where a seam can do the job, it should.
