# Spec: evolving goals, needs and beliefs for every resident

*Cross-cutting specification drawn from `10-engine-seam-and-mind.md` (ENG-24 and §5.3), `11-world-tools-and-physics.md` (TOOL §5), `14-world-core-runner-and-record.md` (CORE-16) and `12-economy-and-life.md` (ECON §5.6). This is the design the operator asked for: goals that change with needs, beliefs and experience, the way a person's do.*

## 1. What exists today

There is no goal, need or belief representation anywhere in the resident's
mind. What exists:

- `set_intention` / `drop_intention`: one string of at most 200 characters,
  optional `done_when_tool`, expiring at day end, rendered as one line
  (`tools/primitives/intent.py`, `state.py:832-870`, `retrieval_depth.py:433-465`).
  Run 23 shows three uses and two refusals because the resident passed the
  engine's sanitised tool name (`world_cook`).
- `keep_doing`: a committed repeating action with stop conditions, the
  closest thing to a plan (`living.py:374-399`).
- Memory-stream `plan` records: regex-extracted from thinking deltas, which
  Kimi mostly does not emit, and re-readable only through `recall_memories`
  (one use in run 23).
- The persona's ambitions, fears and secret hope, delivered once as text and
  never revisited.
- The engine's `planner.py` and `request_plan`: unreachable in the sim.

Consequence: Aldous Vance re-decides to go to college on four consecutive
ticks and never goes; residents re-ask prices they were told; nothing spans a
day.

## 2. Principles

- **Goals are world state.** The world owns state and the trace; goals are
  rendered into the perception by the world, revised by the resident through
  tools, and once a night by a reflection call the engine's seam makes in
  the resident's voice. Nothing in `engine_core` changes (ROADMAP Law 1).
- **Needs are facts, never advice.** The world says "you are hungry (0.8)";
  it never says "go and buy bread". Whether a resident acts on a need, and
  how, is the research question.
- **Every transition is an event.** Goals set, progressed, retired,
  superseded; beliefs updated. Each decision can be joined to what the
  resident was trying to do when it made it.

## 3. Data model

In `app/world/tools/models.py`, owned state, checkpointed:

```
Need    { kind: food | energy | money | rest | shelter | social | safety,
          level: 0..1, since_tick, source: "world" }

Goal    { id, agent_id, text (≤160), horizon: now | today | week | life,
          priority: 1..5, progress: 0..1,
          status: active | done | abandoned | superseded | expired,
          origin: need | persona | social | refusal | reflection | order,
          created_tick, deadline_tick?, parent_id?,
          done_when: { tool?, item?, sim_amount?, place? },
          evidence: [event_id, ...] }

Belief  { id, agent_id, subject: place | person | price | rule | self,
          statement (≤160), confidence: 0..1, evidence: [event_id, ...],
          last_updated_tick, contradicted_by?: id }
```

Genesis seeds `life` and `week` goals from the persona's ambitions, fears and
secret hope (already present in `RichGenesisAgent`). `Intention` becomes a
`Goal(horizon=now)`.

## 4. How goals change

All triggers below are deterministic, world-side, and cost no model call.

| Trigger | Effect |
|---|---|
| Perception build | Needs recomputed from hunger, wallet runway, sleep debt, and unanswered questions addressed to the resident. A need at or above 0.7 with no active goal of that kind spawns `Goal(origin=need, horizon=now)` in the world's plain words. |
| Tool outcome | `done_when` matched: progress 1, status done. Partial progress (sim earned toward a target, materials delivered toward a build) updates `progress`. |
| Refusal (correction signal) | `Belief(subject=place or rule, "cannot X at Y: reason")` with confidence raised; two refusals of the same goal lower its priority by one. |
| Hunger or wallet threshold | The need line and a forced `now` goal, stated as a fact ("you are hungry", "your thinking runs out in N game-hours"). |
| Social event | A request addressed by name creates a candidate goal the resident may accept with `set_goal`. Never auto-accepted: measuring compliance is the point. |
| Day end | `today` goals expire; the outcome line renders once (the existing intention-outcome path). |
| Sleep reflection | The seam (`engine/src/sim/goal_reflection.py`, called from the sleep branch of `/tick` beside the learning gate) reads the day's session log and the world's goals and asks one question in the resident's voice: which of these did you make progress on, which do you drop, what do you now believe. It posts `revise_goals`, a world tool under the resident's id, so the revision is traced like any act. One call per resident per night on the learning adapter. |
| Economy and institutions | Obligations due (doc 06) and tenders, elections and needs of the town (docs 05, 07) appear as facts and become goal candidates the same way. |

Retirement: at most three active goals per horizon; a new goal beyond the
cap supersedes the lowest-priority one of the same horizon, recorded not
silent; expiry by deadline; `abandoned` only by the resident or by
reflection.

Beliefs are the resident's remembered model of the town: where things are,
who sells what at what price, which rules refused it, what it thinks of
itself. They are what let a resident act on a remembered price instead of
re-asking, and they are the substrate for the ideology axes in doc 06 §4.7.
Belief accuracy against ground truth (prices, places) is a research metric.

## 5. How goals reach the prompt

`retrieval_depth.render_perception_text` gains a "What you are trying to do"
block directly after the intention line: the top two needs at level 0.5 or
above, up to three active goals sorted by priority with their progress
(`[week] rebuild the mill roof, 2 of 5 planks bought`), and up to three
beliefs relevant to the current place or people. Facts only, about 80
tokens. The rendered goal and belief ids ride in the perception's trailing
HTML comment so the session log's input carries them.

Tools in `app/world/tools/primitives/goals.py`, replacing `intent.py`:
`set_goal`, `update_goal(progress, priority, status)`, `retire_goal`,
`note_belief`, `revise_goals(batch)`. All accept the engine's `world_` and
`world:` name prefixes wherever a tool is named (TOOL-13).

## 6. Where the mind is broken underneath, and must be fixed first

The goals model sits on a resident's memory, and today that memory does not
work in the sim (doc 10):

- The learning centre cannot write a single row on the sim path
  (`pinned_boot.py:541` builds a `MemorySystem` with no episodic, knowledge,
  social or soul store), so nothing consolidates, the evolved soul never
  moves, and every batch re-sends the whole history (ENG-01).
- Every model call is emergency-compacted because the reserve floor exceeds
  the window, so the first day-0 perception is pinned into every prompt and
  tool results are gutted (ENG-02).
- Stored history is prose only, so the model sees itself as an essayist
  (ENG-09).

Doc 03 orders these fixes. Goals without a working memory would be goals a
resident cannot remember setting.

## 7. Tracing and metrics

Events `agent.goal.set`, `agent.goal.progressed`, `agent.goal.retired`,
`agent.goal.superseded`, `agent.belief.updated`, written to new `goals` and
`beliefs` trace tables (`app/supabase` schema, `wiring.py`). Metrics for doc
08: goals set, done, expired and abandoned per resident-day; mean ticks to
done by horizon; goal-to-action alignment (share of a resident's tool calls
that serve an active goal, joined through the rendered ids); belief accuracy
against ground truth; and, for the research on character, the distance
between a resident's `life` goals on day 1 and day N.

## 8. Acceptance

1. A hungry resident's perception shows a `food` need; after `set_goal` the
   goal line renders; `buy` then `eat` marks it done and the outcome line
   renders once; the `goals` table has one row per transition.
2. Two refusals of `craft` at the bakery produce a belief in the next
   prompt, and the third attempt does not occur in at least 80 percent of
   the L1 scenario runs.
3. Sleep reflection posts `revise_goals` once per resident per night; a
   `no_learning` arm resident reflects or not by explicit configuration, not
   by accident (open question).
4. In a five-day rung-1 run, every resident has at least one `week` goal
   with progress above zero by day 3, and goal-to-action alignment is
   reported per resident.

## 9. Files that change

`app/world/tools/models.py`, `app/world/tools/state.py`,
`app/world/tools/primitives/goals.py` (new), `app/world/tools/bootstrap.py`,
`app/world/ledger/action_costs.py`, `app/world/retrieval_depth.py`,
`app/world/run_genesis.py` (goal lines, `GET /agents/{id}/goals`, day-end
expiry), `app/world/wiring.py` and the schema, `engine/src/sim/goal_reflection.py`
(new), `engine/src/sim/agent_adapter.py` (sleep branch). Tests:
`world/tools/tests/test_goals.py`, `engine/tests/test_sim_goal_reflection.py`.

## 10. Decisions the operator has to make

1. Goals in the world (traced, deterministic triggers, recommended) or in
   the engine's knowledge store (fuzzier, no trace table).
2. Whether the `no_learning` ablation arm also disables sleep reflection.
3. Whether social requests may ever auto-create an accepted goal (for
   example an employer's order to an employee under a wage), or always
   require `set_goal`.
