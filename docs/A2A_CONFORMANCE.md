# Kasukabe against the A2A prototype

**What this is.** An audit of Kasukabe's agent-to-agent communication against
`Prototype/A2A` (Atlas), the in-process A2A implementation built alongside it.
It answers one question: **why do conversations start and group discussions
never happen?**

**The answer, in one sentence.** Atlas *orchestrates* a group conversation as a
directed sequence inside a single task; Kasukabe has no such construct at all,
and every remark is an independent act by one agent on its own scheduled tick,
into a four-tick hearing window, with no shared topic, no membership, and no
notion that a conversation is underway that it is part of.

---

## §1 What Atlas does that produces a group discussion

`atlas/conversation/orchestrator.py :: _run_group` is worth reading in full,
because it is the whole difference:

```
_decide_group()          the LLM is asked: coordinate as a group? which teammates?
                         (only from the agent's REAL roster - it never invents people)
  |
  v
GroupStore.create()      a session object: context, team, topic, members, initiator
router.announce_group()  every member is told the session exists
  |
  v
initiator sends "group_open"        one opening message, to all others
  |
  v
for m in sorted(others):            EVERY MEMBER REPLIES, IN TURN, IN ONE SEQUENCE
    set_status(m, SPEAKING)
    m sends "group_reply"
    metrics.record_hop()
  |
  v
_group_context_exchange()  the initiator sources real context from whichever member
                           owns something relevant -> share / redact / deny / escalate
  |
  v
group.active = False       the session closes
```

Five things make that a discussion rather than a pile of remarks:

1. **A session object exists** (`GroupSession`: `group_id`, `context_id`,
   `team_id`, `topic`, `members`, `initiator`, `message_ids`, `active`).
2. **Membership is explicit.** Every member knows it is in this conversation.
3. **A topic is shared.** Everyone is talking about the same thing.
4. **Turn-taking is driven.** The loop makes each member speak once, in order,
   within one context.
5. **It resolves.** The session ends, and the work it did is attached to a task.

## §2 Feature-by-feature: what Kasukabe has

| A2A concept | Atlas | Kasukabe | Verdict |
|---|---|---|---|
| Message object with `role` / `parts` / `contextId` | `a2a/models.py` | utterance dict with `conversation_id` | **Equivalent** |
| 1:1 thread, reused per pair per context | `ThreadStore.get_or_create` keyed `(context, {a,b})` | `_continuing_conversation` matched both directions, windowed | **Equivalent** |
| Addressing | explicit `recipients` | `listener_agent_id` **plus** vocative parsing out of the words | **Kasukabe is stronger** |
| Delivery feedback to the speaker | none | `heard_by`, `not_here`, and a correction naming who missed it | **Kasukabe is stronger** |
| Overhearing by non-addressees | recipients list | co-location, `involves_self=False` | **Equivalent** |
| **GroupSession object** | `GroupStore` + `GroupSession` | **nothing** | **Absent** |
| **Group membership** | explicit `members` | **nothing** | **Absent** |
| **Shared topic** | `group.topic` | **nothing** | **Absent** |
| **Turn-taking** | driven loop over members | **nothing** - each agent speaks on its own tick | **Absent** |
| **Convening / announcing a session** | `router.announce_group` | **nothing** | **Absent** |
| Task object + `TaskState` (8 states) | `a2a/models.py` | **nothing** | **Absent** |
| Intent on a message (motivation, purpose, scope) | `Intent` in metadata | **nothing** | **Absent** |
| Agent Card (skills, tags, capabilities) | one per agent, seed-built | `world/tools/cards.py :: agent_card` | **Implemented** |
| Discovery: find who can help by skill | router scores skills against the prompt | `rank_people` + `find_people` tool | **Implemented** |
| Need-to-know: share / redact / deny / escalate | owner LLM + policy floor | **nothing** | **Absent** |
| HITL escalation (`input-required`) | real, operator approves | **nothing** | **Absent** |
| Router as the single chokepoint | `bus/router.py` | `ToolRegistry.dispatch` for **tools**; speech has no router | **Partial** |

**The headline:** Kasukabe's *one-to-one* conversation layer is at parity with
Atlas and in two respects better - it reads the addressee out of the words, and
it tells the speaker who actually heard them. Its *group* layer does not exist.

## §3 Why this is the exact cause

A group discussion in Kasukabe can only occur if several agents, each woken
independently every three ticks, each spontaneously choose to speak about the
same subject in the same place, inside a four-tick audibility window, with
nothing telling any of them that the others are engaged in it.

That is a coincidence, not a mechanism. Measured across sixteen runs, the
coincidence happens about as often as you would expect a coincidence to: the
longest exchange ever recorded is seven turns, 69% of exchanges die after one,
and nothing with three or more simultaneous participants on one topic has ever
been observed.

Atlas gets group discussions because it *makes* them happen. Kasukabe does not
get them because nothing makes them happen and nothing tells an agent one is
happening.

## §4 The design tension, stated plainly

**Atlas's `_run_group` cannot be copied into Kasukabe as it stands.**

ROADMAP.md Law 2: *"An engine reaches another engine, an institution, or the
environment **only** through a tool call. No side channel, no injected dialogue,
**no orchestrator passing messages**."*

`_run_group` is precisely an orchestrator passing messages. It decides that a
group forms, who is in it, and that each member speaks - and it makes them speak
by calling them in a loop. Porting it would produce group discussions
immediately and would also destroy the thing Kasukabe exists to measure:
whether agents coordinate **when nobody makes them**. A town that converses
because a loop iterates over it has answered no research question.

Atlas is a *demonstrator of communication quality* - its own CLAUDE.md says it
is "not task-outcome oriented; agents demonstrate how they talk". Driving the
conversation is legitimate there. Kasukabe is an experiment about emergence.
The same code means different things in the two systems.

## §5 What to build instead: the A2A group semantics, as physics

Every structural element of Atlas's group session is portable. Only the
*driving* is not. The translation keeps the mechanism and moves the decision
back to the agent:

| Atlas | Kasukabe equivalent |
|---|---|
| `_decide_group()` - the LLM is asked whether to group | an agent calls a **`convene`** tool because it wants to |
| `GroupStore.create(topic, members, initiator)` | the tool creates the same object in world state |
| `router.announce_group()` | the session appears in every member's **perception** |
| `for m in others: m replies` | each member sees an open session it is part of, whose topic it can read, and **chooses** whether to speak |
| `group.active = False` | the session expires on a bounded clock, or the convener closes it |

The agent still decides everything: whether to convene, whom to call in, what
the topic is, whether to speak when convened, and whether to walk away. What
changes is that a group conversation becomes a **thing that exists in the
world** - visible, addressable, joinable - instead of a coincidence.

This is exactly the shape the open-questions mechanism already took: the world
does not make anyone answer, it makes the obligation *visible*. That change
moved the reply rate from 50% to 75% without forcing a single reply.

## §6 What is deliberately not being ported

- **Need-to-know / redaction / policy engine.** Atlas models a corporate
  information-security floor. Kasukabe's secrets are personal, and its
  equivalent already exists as trade secrets and the business namespace.
- **HITL approval on shares.** The operator console is for petitions, prayers
  and disasters, not per-remark approval.
- **Task / TaskState.** Kasukabe's unit of work is the tick and the tool call.
  Adding a task object is a larger change with no conversation payoff.
- ~~**Agent Cards and skill discovery.**~~ **Built** in a later pass, see section 7.

---

*September · internal research · audited against `Prototype/A2A` (Atlas)*


---

## §7 Agent Cards and discovery, as built

`world/tools/cards.py` + `world/tools/primitives/people.py`, ported from
Atlas's `AgentCard` and `Discovery.rank`. Two tools: `find_people` (who can
help with this) and `describe_person` (one resident's card).

**The card is what a neighbour could observe.** Name, trade, workplace, the
shop owned or worked at, and measured skill proficiency. No wallet, no
secret, nothing from the private half of a persona. Atlas tiers public vs
extended cards on authentication; Kasukabe has no authentication, so the line
is drawn at observability instead. A card that returned balances would hand
every resident a surveillance instrument and turn `steal` from a risk into a
lookup.

**Three findings from building it:**

1. **A stemmer was the wrong tool.** Residents ask in verbs ("who is
   *trading* copper") and the town stores nouns ("*trader*"). Stemming breaks
   on this vocabulary - "baker" stems to `bak`, "bakery" to `bake`, and the
   two stop matching. An explicit alias table, the same idiom
   `place_aliases.py` already uses for the same class of problem, is
   auditable and fixable one row at a time.

2. **Discovery needed no hint, and that is the interesting part.** `skillify`
   sat at zero calls until a hint named the verb; so did `convene`.
   `find_people` was used **ten times in its first 245 calls, unprompted**.
   The difference is that discovery answers a question the agent already
   has - Bram was *actively looking* for a buyer - while authoring and
   convening are verbs for situations an agent does not know it is in. That
   distinction is worth carrying into any future tool: a tool that answers an
   existing question needs only to exist, and a tool that proposes a new kind
   of act needs evidence-driven prompting.

3. **Agents used it as a person-finder, which it was not built for.** Three
   of the first ten queries were name lookups ("where is Tobias Holloway
   working now"), and those only answered by accident, when a surname
   happened to appear in a shared shop name. Name matching was added
   afterwards, weighted below occupation so "Ainsley, who does the mining
   here?" still answers with the miner, plus a full-name bonus so naming one
   resident outranks a surname collected three times over from a household's
   shop and workplace. **The design was corrected by watching it run**, which
   is the only reason it is right.


---

## §8 The last two gaps, closed (2026-09-22)

Re-checked against the code on 2026-09-21, this spec stood at **13 of 19 in
place**. The four left out on purpose in §6 stay out: driven turn-taking, the
task object, need-to-know, and human approval of shares. The two that were
simply missing are now built.

| A2A concept | Before | Now |
|---|---|---|
| **Intent on a message** | **Absent** - a remark carried only its words | `say` takes an optional `intent` (≤ 200 characters). It goes on the utterance row, the event and the trace, and **never into anybody's perception**: a listener hears what was said, not why. An intent that reached the room would be a new channel for telling neighbours what to think while appearing only to explain oneself. |
| **A session resolves into its outcome** | **Partial** - a conversation closed and nothing said what it settled | Every completed `buy`, `sell` and `trade` is filed under the conversation it was agreed in (`WorldState.record_conversation_deal`), and its event carries `conversation_id`. A conversation that closes gets an `outcome`: deals, sim moved, goods. Zero deals is an answer too. `/world/conversations` serves both, and the run report prints **"Settled in this conversation"** under each exchange. |

**Now 15 of 19 in place; the other four are out by design.** Guarded by
`world/tests/test_a_remark_can_say_why.py` (6 tests; the leak test renders
the listener's whole perception - the remark, open questions, awaiting-reply
and the conversation notice - and requires the reason to be absent) and
`world/tools/tests/test_a_conversation_records_what_it_settled.py` (7).

The public A2A protocol (agent card at `/.well-known/agent-card.json`,
JSON-RPC, `message/send`, tasks, push notifications) is still not
implemented, and by the same argument as §4: residents reaching each other
outside the tool registry is the side channel ROADMAP Law 2 forbids.
