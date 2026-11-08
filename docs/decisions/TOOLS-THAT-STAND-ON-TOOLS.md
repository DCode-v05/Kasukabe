# Tools that stand on tools, and tools that answer

*Written 2026-09-12. The change is TOOL-31; the reason is five runs of zero.*

---

## What the trace said

| Run | `toolify` calls | `skillify` calls | resident calls |
|---|--:|--:|--:|
| 25 | 0 | 1 | 2,377 |
| 26 | 0 | 2 | 536 |
| 27 | 0 | 0 | 521 |
| 28 | 0 | 0 | 1,225 |
| 28b | 0 | 0 | 885 |

`toolify` has never been called by a resident. One filing exists in the whole
series (Jovana's `harvest_maintain`, run 26, via an earlier path) and nobody
ever called it. `do_in_order` shipped into run 28b served in full and was
called zero times.

## Two things were unwritable

The operator's example: *see world is a tool; the agent uses toolify to add
layers, so the resultant tool lets it find and search for food — the new tool
becomes `search_food` wrapped around see world.*

Neither half of that could be filed.

1. **A tool could not stand on a tool.** `validate_steps` required every step
   to be in `registry.primitive_names`, frozen at bootstrap, and the refusal
   said so: *"may only compose primitives that existed at bootstrap, never
   another invented tool"*. So a resident that had written `look_here` could not
   write `find_food` on top of it. Every tool restated the same primitives, and
   the town's tools could never compound.
2. **A tool could not answer.** A composite's result was the account of its own
   run — `steps_executed`, `calls_made`, `last_step_result`. `search_food` could
   look around and hand back a log with the whole place in it; the resident
   still had to read the log to find the food. A tool that cannot SELECT cannot
   be built on, because every layer above it re-reads everything below it.

## What changed

**A step may now be any action the caller can already take**, including an
invented tool (`meta.citable_tool_names`). Safety is unchanged and is now
proved by a walk rather than asserted by a whitelist:

- `meta.flatten_to_primitives` expands a filing to the primitives it eventually
  performs. Every leaf must be a primitive — "no new physics" is a property of
  the leaves, not of the height.
- `MAX_TOOL_DEPTH` (3, env `KASUKABE_MAX_TOOL_DEPTH`) bounds the tower; a
  deeper filing is refused and the refusal says how deep it may go.
- A cycle is refused, naming the loop. Revising is the only way one could be
  filed, so `revise_tool` validates with the tool under revision marked.
- A filing whose flattened leaves exceed `PROGRAM_MAX_CALLS` is refused on
  paper, before any act.
- `program.ProgramBudget` gives a whole nest **one** dispatch ceiling and
  **one** wallet share, held on `world.program_budget` and cleared by
  `execute()`. Before this, a tool calling a tool calling a tool would have had
  three times the budget of the one call the resident made.
- A tool the caller cannot see — another business's trade secret — is refused
  exactly as an unknown name is. Secrecy is secrecy from the composition layer.
- The meta-tools remain uncitable: a program that could call `toolify` would
  write its own physics one indirection removed.
- **Cost is unchanged:** a two-layer tool costs exactly its leaves, pinned by
  `test_a_tower_costs_exactly_its_leaves`.

**A tool may now answer**, via `returns` — the projection, and still data:

- each entry is a reference (`@bound`, `@bound.field`, `$param`) or one closed
  selector `{from, where, take}`;
- `where` is one comparison from the same closed set `when` uses, now including
  `contains` / `not_contains`, because the town answers a great many questions
  in sentences;
- `take` is `all`, `first` or `count`;
- a dict is read as its values and one level of nesting is flattened, so
  `look_around`'s `{shop: [rows]}` is selectable;
- nothing in a projection can read the world. It reads only what a step already
  paid for, which is the rule `when` has always kept.

`do_in_order` takes `returns` too, so a resident can search once without
keeping a tool. Skills already composed skills and tools; `skillify`'s
description now says so.

## What the evals said — before and after

The honest history first: **L1 verb reach has been run once**, for run 22, at 5
samples rather than the spec's 20. It measured `skillify` at **0% reach** and
that number stood for six runs. `toolify` and `do_in_order` had scenarios
written after that run and had **never been scored at all**. PROG-03's gates
passed *vacuously* in run 26 — zero programs, so nothing was sampled.

Run of 2026-09-12, Kimi K2.5 through the broker, 5 samples per scenario, $1.49:

| Scenario | Kind | Reach | Verdict |
|---|---|--:|---|
| `toolify/positive_answer_with_what_it_found` | positive | **60%**, params 100% | **PASS** |
| `toolify/after_refusal_unbound_reference` | after_refusal | 100%, recovery 100% | PASS |
| `toolify/canonical` | positive | 0% | FAIL |
| `toolify/positive_stand_on_a_tool` | positive | 0% | FAIL |
| `toolify/program_with_a_guard` / `_repeat` | positive | 0% / 0% | FAIL |
| `do_in_order/positive_ten_harvests_then_the_mill` | positive | 0% | FAIL |
| `skillify/canonical` | positive | 0% | FAIL |
| three negatives | negative | 0% | PASS |
| `skillify` / `do_in_order` after_refusal | after_refusal | 40% recovery | FAIL |

**5 of 16 pass.** The one new positive that passes is the projection: asked to
find one line in a list it re-reads every morning, the model reaches for
`toolify` three times in five **and uses `returns` in every one of them**. So
the answering half is reachable from its description alone.

The failures are not about mechanics. Across 129 eval calls the model reached
for `look_around` 33 times and `list_places` 15, then did the job by hand. It
orients and acts; it does not decide "this is a routine, file it".

Then one more measurement, because the first one was unfair: the harness showed
the tool descriptions with **no authoring hint**, and a live resident that has
repeated a job has read one. With the hint shown (the world's own wording, via
`state.authoring_hint_text`):

| Scenario, hint shown | Reach |
|---|--:|
| `skillify/positive_canonical_with_the_hint` | **80%** (4 of 5) — first non-zero since run 22 |
| `toolify/positive_canonical_with_the_hint` | 0% |
| `toolify/positive_stand_on_a_tool_with_the_hint` | 0% |
| `do_in_order/positive_ten_harvests_with_the_hint` | 0% |

Reordering the hint to name the three by commitment (run now, keep, write)
moved skillify to 3 of 5 and the others not at all — noise at this sample size,
so the wording stands and the question is open. **What the pair does say: the
hint is what moves `skillify`, and nothing yet moves `toolify` or
`do_in_order`.**

## Four evals that could not fail

`toolify/negative_a_one_off_errand`, `pay_tax/negative_you_owe_none`,
`steal/negative_hungry_beside_an_open_shelf` and
`stock_shelf/negative_holding_nothing` all expected `"verb": null`. A negative
is scored as "reach stayed at or under 10%", so with no verb named reach was 0
whatever the model did. All four now name the verb, and
`test_no_authoring_scenario_can_pass_by_construction` keeps it that way.

## What is still open

- **Reach for `toolify` and `do_in_order` is 0% in every situation built for
  them.** The description is not the problem and the hint is not enough. The
  next lever is the perception's own evidence — a resident that has just run the
  same four acts for the fourth time is told so; it is not told what that costs
  it. Measuring a "this cost you N decisions" line is the obvious next
  experiment, and it is a prompt change, so it needs L1 before a run.
- **20 samples, not 5.** The eval spec fixes 20; every number above is 5.
- **Skill nesting is capped at 2** while tools now go 3 deep. No reason beyond
  history; raising it is a one-line change and an eval.
- **Nothing has run live.** Layering, projections and the new scenarios have
  never met a real run, and a feature measured only offline is provisional
  (X1).
