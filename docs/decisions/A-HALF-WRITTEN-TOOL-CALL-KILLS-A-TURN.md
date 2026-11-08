# A half-written tool call kills a turn

*A finding about the engine, written up rather than patched around — ROADMAP
Law 1. Found in the 50-resident run of 2026-09-16, diagnosed 22 September.*

## What happens

The 50-resident run lost **41 turns** to a single exception, spread across
**33 of its 50 residents**. Every one was a Bedrock call that was billed,
answered `HTTP/1.1 200 OK` at the broker, and then produced nothing:

```
INFO:httpx:HTTP Request: POST http://localhost:8200/v1/messages "HTTP/1.1 200 OK"
ERROR:src.engine_core.coordinator:Thread execution failed for task
  kasukabe-agent-a0204: Unterminated string starting at: line 1 column 241
```

66 of the run's 2,224 ticks returned 500. 41 of those 66 were this.

## Why

`herald/provider.py` accumulates a streamed tool call's arguments across
`input_json_delta` events into `current_tool_json`, then at
`content_block_stop` does:

```python
input=json.loads(current_tool_json) if current_tool_json else {},
```

with no guard. `sim/config.py` caps every response at
`SIM_MAX_RESPONSE_TOKENS` (768), so a response that runs out of tokens while
those arguments are still being written leaves incomplete JSON.
`json.JSONDecodeError` is raised out of the generator, travels up through
`thread.py`'s agent loop to the coordinator, and fails the whole task.

The failing offsets in the run run from column 8 to column 1,513. That is a
cap landing wherever the model happened to be, not a fixed truncation point.

## The severity is the turn, not the call

The exception escapes the generator, so every tool call the model completed
**earlier in the same response** — already yielded, already valid — dies with
it. A resident that harvested, walked, and then began a malformed third call
loses the harvest and the walk as well.

## Why it is not fixed where it breaks

`src/herald/` is frozen under ROADMAP Law 1, which is explicit about the
alternative:

> If a round appears to need a change under `engine/src/` outside `sim/`, that
> is a finding about the engine and gets written up rather than patched around.

So the unguarded parse stands. This document is the write-up the law asks for.

**What September may want to know:** a single `try/except json.JSONDecodeError`
at that `content_block_stop` — yielding the tool call with its best available
arguments, or skipping it — would turn a dead task into a degraded one. The
broker in this repository already does exactly that for its own trace
(`app/broker/service.py::seal`), on the reasoning that *"a half-recorded tool
call is a finding while a silently dropped one is not."* The same argument
applies on the live path.

## What the town does instead

`engine/src/sim/half_written_tool_call.py` — in the seam, which Law 1 makes
changeable — catches the `JSONDecodeError` coming out of the stream and closes
it with an ordinary `end_turn` stop. Every event already yielded stands, so the
turn keeps the work it really did and the agent loop takes its normal
end-of-turn path. The malformed call is the only thing lost, and it was never
usable.

It is narrow on purpose: only `json.JSONDecodeError`. A `TypeError` or a
transport failure still travels, because swallowing those would trade 41
visible dead turns for an invisible broken engine.

It counts (`tool_calls_half_written`) and logs, for the same reason
`bounded_monologue.py` counts its rewrites: a run losing many of these is
telling the operator that the 768-token cap is too low for the tools it is
asking residents to call. That is a decision, not a defect.

## Its sibling

`sim/bounded_monologue.py` handles the other half of the same cap — a response
that runs out mid-**sentence** — and says in its own exclusions that it
deliberately does not touch *"a stream that produced a tool call and then hit
the cap."* This is the case it left: the cap landing inside the arguments,
before the call was ever complete enough to yield.

## Verdict

**Not re-measured.** Per the checklist's fix-ledger bar, this gets a verdict —
*moved*, *did not move*, or *not re-measured* — in the next run's report. The
figure to compare is 500-status ticks attributable to `Unterminated string`:
**41 of 2,224 ticks (1.8%)** in the 50-resident run.

*Code: `engine/src/sim/half_written_tool_call.py`, wired outermost in
`pinned_boot.py`. Tests:
`engine/tests/test_a_half_written_tool_call_does_not_kill_a_turn.py` (6).
Evidence: `docs/runs/series/teambox50/runner.log`.*
