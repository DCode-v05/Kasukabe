# A sale is agreed in conversation

*Decided 2026-09-21 by the operator. Built and tested the same day. This reverses a
named rule of the original tool spec — read "What this reverses" before changing it
back.*

---

## The problem

`buy`, `sell` and `trade` checked one thing about the two parties: that they stood
in the same place. Reproduced against a booted town:

| Situation | Before |
|---|---|
| Two residents in the same room who had **never spoken** | `buy` and `sell` both **succeeded** |
| A shop whose owner and staff were **all somewhere else** | a buyer walked in and **bought from the empty shop** |

So an agent could buy from somebody it had never addressed, across a crowded
square, or from a shop nobody was minding. Every price paid in that economy was paid
to somebody who never agreed to it — the economics of vending machines, not people.

## The rule

A purchase happens only between two parties who are **in a conversation**:

- **Person to person** — buyer and seller share an *open* conversation at the place
  where both are standing, and both are on its roster.
- **Buying from a shop** — somebody who runs it (its owner **or an employee**) is at
  the premises and in that conversation with the buyer.
- **Applies to** `buy`, `sell` and `trade`.

"Open" is the world's existing definition: a conversation closes after
`CONVERSATION_WINDOW_TICKS` (12 ticks, two game-hours) of quiet. A deal struck
yesterday is not an open negotiation. A third person talking to the seller does not
put the buyer in the conversation.

**The honest path costs one call.** `say` addressed to somebody already puts both
people on the roster ("being spoken to is being in the conversation",
`primitives/social.py`). So a resident speaks to the seller, then buys.

## Details that matter

**The conversation is checked last, just before money moves.** After "are you
there", "do they sell it" and "is it on the shelf". Every refusal is a paid model
call, and telling a resident to go and talk to a seller whose shelf is empty buys
it a wasted one — it would speak, and then be told there is nothing to buy.

**Refusals name people by id.** The refusal passes through `names.name_mentions`,
which writes every agent id as "Name (id)"; a resident needs the id to address the
person. Found by two existing naming tests failing on the first draft.

**The market board says which shops are open.** `market_offers` carries `minded`,
the board prints "nobody minding it right now", and minded, stocked offers are
listed first. Otherwise the board would send residents walking to shops that cannot
sell — the 87 refusals of the 50-resident run by a different route.

## Exemptions, and why

- **Gifts** — `give` and `transfer` are not purchases and are unchanged.
- **The world's own scripted actors** — the control bakery (owner `control:*`,
  registered in `world.control_actors`) is the experiment's baseline, has no
  resident to talk to, and deals on **both** sides: it sells bread and buys flour.
  The first draft exempted it only as a seller; `test_control_bakery` caught it
  failing to restock.

## What this reverses

The original tool spec's fourth named test was
`test_absent_owner_still_sells_at_the_posted_price`, with the reason:

> "a shop that only trades while its owner is standing in it is a person, not a
> business."

That rule is replaced. What survives of its point is that a business is not one
person: **an employee can mind the shop** while the owner is away
(`test_a_shop_minded_by_its_employee_sells_while_the_owner_is_away`). A shop with
nobody in it does not sell.

## Switch, and record

`KASUKABE_SALES_NEED_CONVERSATION=0` restores the old behaviour for a control arm
measuring what the rule costs. The run manifest records which was in force.

## What it costs, and what it buys

About one extra call per purchase — the remark before it. Against that, a sale now
means two parties agreed it, and the town's prices are prices somebody accepted.

## Addendum, same day: `buy` is not offered outside a conversation

*Operator, 2026-09-21: "The buy tool will only be enabled after the participants of
the buy process exist in a conversation. Single or group conversations are equally
applicable."*

The rule above made a purchase **refuse** without a conversation, but `buy` still
sat in every resident's tool list all the time. So a resident with nobody to talk
to could still spend a paid call on it just to be told no. Now the tool is not
**offered** at all until the resident is talking to somebody:

- **When it appears.** `buy` is in the list a resident's engine fetches
  (`GET /tools/registry?agent_id=…`) only while the resident is on the roster of an
  open conversation here, with at least one other member standing here too.
  **One-to-one and group conversations count the same.** A remark to the room with
  nobody else on the roster does not count (`WorldState.conversation_partners`,
  `registry.CONVERSATION_ENABLED_TOOLS`).
- **When it goes away.** When the conversation goes quiet (12 ticks) or the other
  person walks off. The engine used to add tools but never remove them. It now drops
  any world tool the world stops offering, on a real list only; an unchanged answer
  (HTTP 304) removes nothing (`engine/src/sim/world_tools.py`).
- **The flow is two turns.** The engine builds a turn's tool list once, at the
  start of the turn, and that code is frozen (Law 1). A resident says something to the
  seller in one turn and sees `buy` in its next turn. It does not need an extra
  call, because the remark was already needed.
- **The refusal stays as the backstop.** A call that still reaches `buy` (a
  saved program's step, or an engine holding an older list) gets the precise reason
  ("you and X are not in a conversation"), not a bare "no such tool". That is
  why the tool is left out of the served list rather than hidden in `visible_to`,
  which would also block dispatch.
- **Only `buy`.** `sell` and `trade` are still offered and still refused at call
  time. The instruction named `buy`.
- **The switch covers both.** With `KASUKABE_SALES_NEED_CONVERSATION=0`, `buy` is
  offered always, as before.

The market board now says so: *"A purchase is agreed in conversation with the
seller."* The `buy` scenarios in `world/evals/scenarios/buy/` now put the resident
in a conversation, and a new near miss has a price posted but no conversation. The
right move there is to speak to the seller, not to call a tool it does not have.

Found while building it: `do_in_order` refusals came back as HTTP 500. The help a
refusal carries listed tools with their place, and for a tool whose place depends
on what you name, that place was a Python function the route could not serialise.
It is now written in words, and the route cannot 500 on a refusal
(`world/tests/test_a_refusal_is_never_a_server_error.py`).

Guarded by `world/tools/tests/test_buy_is_offered_only_in_conversation.py` (11 tests:
alone, one-to-one, group, speaking turns it on, a room remark does not, quiet, walking
off, sell/trade unchanged, the backstop refusal, the unfiltered list, the switch) and
`engine/tests/test_a_tool_no_longer_offered_is_removed.py` (4).

## How it is guarded

- `world/tools/tests/test_a_sale_is_agreed_in_conversation.py` — 18 tests: refused
  without a conversation, allowed with one, for buy, sell, trade and shops; stale
  conversations; a third party; employees; the control bakery on both sides; gifts;
  check ordering; the switch; the market board.
- `world/tests/test_the_town_keeps_its_books.py` — the seeded soak now checks, with
  its own independent logic, that **every completed sale happened between people who
  were talking**. With the rule removed it fails at once: *"a trade completed
  between two people who were not talking"*.
