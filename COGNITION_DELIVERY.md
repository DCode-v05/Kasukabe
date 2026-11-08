# Cognition delivery: giving agents a personality and a voice

**In one sentence:** right now, every AI character in Kasukabe thinks with no memory of who it is, and has no way to actually speak to anyone. This is the plan to fix both.

---

## The problem, in plain terms

We already write a detailed backstory for every one of the 250 characters — name, personality, fears, secrets, and relationships with named neighbors (who they owe money to, who they resent, and so on). That data already exists, sitting in a file called `genesis.json`.

But it never actually gets shown to the AI model that's "playing" that character. Instead of thinking as "Priya, a careful haggler who doesn't trust strangers," the model just sees a list of 30 possible actions (move, buy, harvest, trade...) with no idea *who it is*. **That's Problem 1 — no personality is ever delivered.**

On top of that, there is no way for one character to actually talk to another. Thirty actions are wired up — move, harvest, trade, pray, marry — and none of them is "say something out loud." Agents can stand next to each other in the same place but have no way to have a conversation. **That's Problem 2 — there is no speech.**

Both were flagged after the very first full test run (250 agents, 8 days, $214 spent): the agents behaved like disconnected task-runners, never like people living in a town.

---

## Fix 1: give every agent its personality

The character data already exists — this is purely a delivery problem. We just need to get that text from the file into the actual message sent to the AI model, on every single turn.

```
STEP 1 — the character data already exists
┌───────────────────────────────────────┐
│ genesis.json: name, bio, personality, │
│ fears, secrets, who they know and how │
└───────────────────────────────────────┘
                    │
                    │  read once, right before the agent's program starts
                    ▼

STEP 2 — write it to a plain text file
┌─────────────────────────────────┐
│ "Here's who you are" text file, │
│ one per agent, written to disk  │
└─────────────────────────────────┘
                 │
                 │  the agent's program reads this file when it boots up
                 ▼

STEP 3 — hand it to the AI engine's identity slot
┌────────────────────────────────────┐
│ a small piece of memory the engine │
│ already has, but was left empty    │
└────────────────────────────────────┘
                   │
                   │  now filled in with the real character
                   ▼

STEP 4 — every message includes it
┌───────────────────────────────────────┐
│ every single turn, the AI is reminded │
│ "you are this specific person"        │
└───────────────────────────────────────┘
```

The key point: **none of the core AI engine's code changes.** The engine already has a slot for "who am I" — nobody had ever connected it up for these characters. We're just plugging it in correctly.

---

## Fix 2: give every agent a voice

```
STEP 1 — a character decides to speak
┌────────────────────────────────────────┐
│ "say" this out loud, to everyone here, │
│ or to one specific person if named     │
└────────────────────────────────────────┘
                     │
                     │  recorded as: who said it, where, to whom (if anyone)
                     ▼

┌───────────────────────────────┐    ┌───────────────────────────────┐
│ STEP 2a — others can hear it  │    │ STEP 2b — it's saved for good │
│                               │    │                               │
│ anyone standing in the same   │    │ the exact words, speaker,     │
│ place (or directly spoken to) │    │ listener, place and time      │
│ sees it on their next turn,   │    │ get logged, so it can be      │
│ and can reply in character    │    │ replayed and watched later    │
└───────────────────────────────┘    └───────────────────────────────┘
                │
                │  the reply becomes their own next "say"
                ▼

STEP 3 — a real back-and-forth
  layered on top of each speaker's own personality from Fix 1 —
  two characters actually talking, not two silent robots taking turns
```

---

## What actually changes, in one table

| Thing | Where it happens | In plain terms |
|---|---|---|
| A new text file per agent | written right before their program starts | "here's who you are" |
| One new setting turned on | inside the agent's own boot-up code | "please actually use this character's identity" |
| One new action agents can take | called `say` | "talk out loud, to everyone or to one person" |
| One new row saved per thing said | the conversation log | "so it can be replayed and watched later" |

---

## How to test this without spending real money

We don't need to spend real AI budget to prove this works. There are two separate "practice mode" switches in the system:

- One controls whether the agents' programs actually start up at all.
- The other controls whether the AI provider is actually called, or just returns a made-up reply.

By turning the first one **off** (real programs really start) and the second one **on** (fake AI replies, $0 cost), we get the full real pipeline running — the file gets written, the program reads it, the message gets built — except the "AI thinking" part is faked. We can then look at exactly what message would have been sent, and confirm the character's personality is really in there, before spending a single real dollar.

Only after that check passes is turning on real AI calls (real cost) worth doing.

---

*Plan only — not yet built.*
