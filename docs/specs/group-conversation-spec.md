# Spec — dynamic group conversation, seen in the UI, proved by two runs

Status: written before any code, per the project's TDD standard. Tests and
evals in this document are stated in words only.

## 1. Why

Run 17 produced nine conversations, four with a real second speaker, one of
sixteen turns among four Vances. So group talk happens. What does not exist
is a *conversation* — an object with a life of its own. Today
`conversation_id` is inferred at the moment somebody speaks, from the room
and a time window, and thrown forward onto the next remark. Three things
follow, and all three are visible in run 17:

* **Nobody can be in a conversation without speaking.** A resident standing
  in the room, addressed by name, listening, is not a participant. There is
  no roster to be on.
* **One room can hold only one conversation.** Two pairs talking about
  different things in the market are recorded, and rendered, as a single
  thread. The remark that answers one lands under the other.
* **No agent can convene anything.** A resident who wants the household
  around a table has no way to say so. They can only speak and hope the
  window catches whoever answers.

The last one is what Shrivathsan asked for: conversations should be
*created*, dynamically, by residents who want them — and a created
conversation can hold more than two people from the start.

## 2. Objectives

1. A conversation is a first-class record with an id, a place, an opening
   tick, a topic, an ordered roster of participants each with the tick they
   joined, and an open/closed state.
2. A resident can convene one deliberately, naming who they want in it, and
   the invited residents are on the roster before they have said a word.
3. A resident can speak into a *named* conversation, so two conversations in
   one room stay two.
4. Joining stays automatic as well: speaking in a room with an open
   conversation joins it. Convening is an addition, never a precondition.
5. A conversation closes on its own when the room falls quiet for longer
   than the window, and a closed conversation is never reopened.
6. The UI shows every conversation as a group chat: who is in it, where, how
   many turns, and the messages in order, updating while the run is live.
7. The map is visible in the same UI and shows where the town is.
8. Two runs, reported separately: one that puts the town in a position to
   talk, one that puts it in a position to act on its surroundings.

## 3. Expected behaviours

**Convening.** A resident calls a tool naming a topic and any number of
other residents. Those residents must be reachable in the same place — a
conversation is a thing that happens in a room, not a broadcast. The caller
is on the roster. The conversation is open and empty of remarks until
somebody speaks.

**Speaking.** `say` continues to work unchanged when no conversation is
named: the room's open conversation, or a new one. When a conversation is
named, the remark joins that one and the speaker joins its roster if they
were not already on it. Naming a conversation that is closed, or that is
happening somewhere else, is refused with a message that says which.

**Addressing.** A remark addressed to a named resident puts that resident on
the roster. Being spoken to is participation; the roster is who the
conversation is *about*, not only who has produced text.

**Closing.** A conversation whose last remark is older than the window is
closed. Closing is observable — it changes what the UI shows and what a new
remark in that room joins.

**More than two.** Nothing in the model treats two participants as special.
A roster of five is the same object as a roster of two.

## 4. Test coverage required

Unit and integration, real objects, no mocked world:

* A convened conversation exists, is open, has the convener and every
  invitee on its roster, and has zero remarks.
* Convening with somebody who is not in the room is refused, and the refusal
  names them and where they actually are.
* Convening with nobody named is still a conversation — a resident may open
  a table and wait.
* Two conversations convened in one room stay distinct across a dozen
  interleaved remarks, and every remark lands in the one it named.
* A remark that names no conversation in a room with exactly one open
  conversation joins it — the old behaviour, unbroken.
* A remark that names no conversation in a room with two open conversations
  joins the one the speaker is already on; if they are on neither, it joins
  the most recent.
* Speaking into a closed conversation is refused and says so.
* Speaking into a conversation in another room is refused and says where it
  is.
* Being addressed by name puts a silent resident on the roster.
* A conversation closes after the window and a later remark in that room
  opens a new one.
* Rosters survive a checkpoint and resume.
* The world API exposes every conversation with its roster, place, state and
  turn count; the shape is asserted against what the UI reads, not against
  itself.
* Costing: convening is priced, and the price is in the schedule the
  existing "every registered tool has a price" test walks.

Edge cases that must be covered explicitly:

* Convening a conversation naming yourself, twice, or naming a dead or
  dormant resident.
* Two residents convening in the same room in the same tick.
* A participant who walks out mid-conversation — the roster keeps them, the
  transcript shows they stopped.
* A conversation with a hundred remarks does not degrade the room's
  matching, and the tail the UI reads stays bounded.
* Rendering with a missing display name, an empty topic, and content that
  contains HTML.

## 5. UI coverage required

The UI is a deliverable here, not a by-product, so it is tested the way the
map is: against a real page, headless, screenshotted, asserted on pixels and
on DOM state — never against a Node stub, which has lied about this exact
panel before.

* The group chat panel exists, is reachable, and is open by default when
  there is conversation to show.
* A conversation with five participants renders all five, not "and 3
  others", and names them.
* Two conversations in one place render as two cards.
* The panel updates while the run is live — a new remark appears without a
  reload.
* The panel has non-zero height and its text is actually on screen. The Talk
  panel once rendered 20,473 characters at zero height behind a shut drawer
  and every DOM assertion passed.
* The map renders, fills the view, and shows residents at their locations.
* Both are asserted in the same screenshot, because "the map works" and "the
  chat works" have each been true separately while the page was unusable.

## 6. Eval coverage required

Evals run against a real model on a real run; failure isolates and the
board is reviewed at the end. Thresholds are fixed in writing before the
run, and each must be able to go red.

**Conversation run.**

* At least three conversations reach three or more participants.
* At least one conversation is convened deliberately, by an agent choosing
  the tool, not by the room's fallback.
* In multi-party conversations, at least half the remarks are answered by
  somebody other than the person addressed — the thing a group chat does
  that a pair cannot.
* No conversation contains a remark from a resident who was not in the room.
* At least one conversation survives a change of subject and stays one
  conversation.
* A resident who is addressed and does not reply is visible as such — the
  eval must be able to distinguish silence from a lost record.

**Environment run.**

* Every gated workshop is entered by somebody whose work needs it.
* At least three distinct interaction tools succeed against the environment
  (harvest, craft, cook, build, trade), each by a resident whose occupation
  matches.
* Movement resolves: journeys started are journeys arrived, and the arrival
  is proved by the agent's own later perception, not by the estimate.
* A refusal teaches: after a gated tool refuses, the same resident's next
  attempt is better formed, measured across the run.
* Something is authored — a skill or a tool — by a resident who repeated a
  job.

**Both runs.**

* Zero records dropped by the recorder.
* Trace-store spend reconciles with the broker to the cent.
* Every resident present in the record.

## 7. What "done" means here

Wired: the tools are registered, priced, reachable by a real agent, and the
UI reads a real endpoint. Proven: two real runs against real models, each
with its own report, and the UI verified by screenshot on those runs' data.
Loud: convening a conversation that cannot exist is refused with a reason,
and a conversation the UI cannot render fails visibly rather than showing an
empty card.
