# Law 1 manifest: every edit made inside the frozen engine

ROADMAP Law 1 freezes `src/engine_core/`, `src/memory/`, `src/herald/` and
`src/learning_centre/`. The vendored engine's base is `95e8788`
("Vendor the Kasukabe engine"), and the standing rule is that a defect in
those directories **gets written up rather than patched around** —
`docs/decisions/A-HALF-WRITTEN-TOOL-CALL-KILLS-A-TURN.md` is what that
normally looks like.

On the branch `evals/proving-the-town` the operator explicitly overrode
Law 1: findings are to be fixed, frozen engine included. This file is the
condition attached to that. Every edit inside a frozen directory:

- lands as its **own commit**, prefixed `engine(law1):`
- appears here, with the evidence that motivated it and the diff against
  base `95e8788`
- is written to be **revertable in isolation** and **upstreamable** to the
  engine repository

so that the divergence stays a short, reviewable list rather than
something smeared through a branch.

---

## 1. `herald/provider.py` — `STOP_MAP` could not produce `REFUSAL`

**Found by** `proof/agility.py`, two rows:

```
! stop reason 'content_filtered' reaches the agent loop
    the loop was told StopReason.END_TURN - indistinguishable from a normal turn
! stop reason 'guardrail_intervened' reaches the agent loop
    the loop was told StopReason.END_TURN - indistinguishable from a normal turn
```

**The defect.** `STOP_MAP` appears twice, identically — once for the
non-streaming path and once inside `stream_with_tools` — and mapped three
reasons, sending everything else to `END_TURN`. `StopReason.REFUSAL` is
declared in `herald/types.py` and was produced by neither map: a member of
the enum that no code path could reach.

The broker already does its half. `broker/translate.py`'s
`STOP_REASON_BEDROCK_TO_ANTHROPIC` maps Bedrock's `content_filtered` and
`guardrail_intervened` onto `"refusal"` before the stream leaves it,
noting that refusal is "the closest member of Anthropic's stop_reason
enum". So the signal arrived at this line intact and was flattened here.

**Why it matters now rather than earlier.** Kimi K2.5 rarely refuses, so
this has cost the project approximately nothing to date. A GPT-class model
refuses far more often. Every refused turn would be billed, produce
nothing, and be recorded as an ordinary completed turn — and no instrument
downstream could distinguish "the model declined" from "the resident had
nothing it wanted to do". That is the same shape as the records-in-the-
assistant-role bug: not a crash, a quiet mis-reading of what happened.

**The fix.** One key added to both copies of the map:

```python
"refusal": StopReason.REFUSAL,
```

**Why not the seam.** The seam can wrap an adapter's stream
(`bounded_monologue.py` already rewrites a `max_tokens` stop), so a
wrapper here was possible. It was rejected because the information is
already destroyed by the time a wrapper sees it: `STOP_MAP.get(..., END_TURN)`
has collapsed `"refusal"` into `END_TURN` and nothing downstream can tell
that apart from a genuine `end_turn`. A seam fix would have to re-derive
the stop reason from a second source. The one-line map entry restores a
value the enum already declares.

**Blast radius.** Any caller that compares `stop_reason == StopReason.END_TURN`
will now see `REFUSAL` on a refused turn instead. The agent loop
(`engine_core/thread.py`) branches on `!= StopReason.TOOL_USE`, so a
refusal still ends the turn exactly as before — the behaviour is unchanged
and only the recorded reason becomes truthful.

**Revert:** drop the `"refusal"` line from both maps.

---

## 2. `herald/provider.py` — the open tool call lived in three scalars

**Found by** `proof/agility.py`'s `two tool blocks interleaved` hazard,
which raised `JSONDecodeError` where a clean stream delivered two calls.

**The defect.** `stream_with_tools` held the open tool call in
`current_tool_id`, `current_tool_name` and `current_tool_json`, and
**`event["index"]` was read nowhere in the method**. Anthropic and Bedrock
both happen to emit content blocks one at a time, so every start was
followed by its own deltas and its own stop and the bug never fired.

A provider that interleaves two tool blocks concatenates both argument
strings into one buffer. The usual outcome is `JSONDecodeError` — loud,
and the seam already turns that into a lost turn rather than a lost run.
**The outcome that matters is the other one:** if the two fragments happen
to concatenate into *valid* JSON, the turn calls a real verb with arguments
nobody wrote, and nothing anywhere notices. That is TRADE-01's shape, which
cost this project 6,424 calls — *"it returns SUCCESS for an act nobody
asked for."*

**The fix.** `open_tools: dict[object, dict[str, str]]`, keyed by the
content-block index. Each block accumulates into its own slot and is
emitted by its own stop. A delta for a block that was never opened is
dropped rather than accumulated into whatever slot happens to be open —
which is how one call's arguments reached another call's name.

`_NO_INDEX = -1` keeps a provider that omits the field on the single-slot
path it already used, so a stream that opens one block at a time behaves
exactly as before.

**Why not the seam.** The seam sees `StreamEvent`s, not wire events. By
the time a wrapper sees them the two argument strings are already one.
Nothing downstream can unmix them.

**Blast radius.** Sequential streams — every stream in production today —
are byte-identical. Interleaved streams stop corrupting. `proof/agility.py`
records the change: `two tool blocks interleaved` went from *"the turn
failed loudly (JSONDecodeError)"* to *"survived intact: 2 calls"*.

**A tripwire fired, and was updated rather than silenced.**
`engine/tests/test_a_half_written_tool_call_does_not_kill_a_turn.py`
asserts the frozen parse line verbatim, precisely so a fork cannot happen
quietly. It caught this. The assertion now names the new line and adds a
second one checking the parse is still **unguarded** — because that is the
half that matters: `keep_the_turn_on_a_half_written_call` is still the only
thing between a truncated argument and a dead turn, and if the parse ever
becomes guarded that wrapper is dead code pretending to work.

**Tests:** 189 passed (`-k "provider or stream or stop_reason or tool_call
or half_written"`).

**Revert:** restore the three scalars and the original three branches, and
put the old parse line back in the tripwire.

---

## What did NOT need an entry

Two further findings from `proof/agility.py` were fixed **in the seam**,
so Law 1 was never engaged and they are recorded here only so the next
reader does not go looking for them above.

- a tool block whose `content_block_start` never arrives is discarded by
  `provider.py:653-657`/`:674` while `message_stop` still reports
  `tool_use`
- a call written out in a markup the broker does not recover is billed and
  changes nothing (INFRA-07 in a spelling that is not Kimi's)

`engine/src/sim/a_turn_that_tried_to_act.py` counts both, as a fourth
wrapper in `pinned_boot`'s chain. It deliberately does **not** recover
either call: in the first the tool's name never crossed the wire, and in
the second, guessing at an unknown markup is how you call the wrong verb
with the right-looking arguments — the line `kimi_tool_calls.py` already
draws ("a turn that did nothing is bad, and a turn that did SOMETHING
ELSE is worse").

Control flow is untouched: `thread.py` already ends the turn on
`not turn_tool_calls`. What was wrong was the record — a turn that tried
and failed looked exactly like a resident that chose not to act, and every
instrument downstream read it as the second.

The same wrapper also **corrects the contradictory stop reason**:
`tool_use` with no tool call is not a stop reason, it is a claim the turn
cannot support, so it is rewritten to `end_turn` before anything records
it. This too is behaviour-neutral — `thread.py` breaks on
`stop_reason != TOOL_USE or not turn_tool_calls`, and the second clause
was already true — and it too belongs in the seam rather than the parser,
because noticing a contradiction does not require re-parsing the stream.
It is the reason `proof/agility.py` now runs its hazard matrix through the
wrapper chain `pinned_boot` builds: measuring the bare parser measured a
chain nobody deploys.

**One thing this does not fix, and a future entry will.**
`provider.py:624-626` holds the open tool call in three scalars and never
reads `event["index"]`. A provider that interleaves content blocks
concatenates two arguments into one string. Today that raises
`JSONDecodeError` — loud, therefore survivable — which is why it is not
urgent. It becomes a corruption risk the moment two interleaved argument
sets happen to concatenate into *valid* JSON, and fixing it means changing
the parser's control flow, which earns its own commit and its own entry.

*Entries: 2.*
