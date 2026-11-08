# Price controls and licensing are policy

*Built 2026-09-21 for GOV-4 ("policy is more than one number"). This records one
choice that differs from the governance review's design, and why.*

---

## What exists now

A sitting seat (PM or minister) at the government complex can set three things.
Each is a field on `GovernmentPolicy`, versioned and snapshotted like the tax rate:

| Lever | Tool | Read by | Default |
|---|---|---|---|
| `price_caps[item]` | `set_price_control` | `post_price`, and `buy` / `sell` / `trade` at settlement | none |
| `price_floors[item]` | `set_price_control` | the same | none |
| `business_licensing` | `set_business_licensing` | `register_business`, before the fee is taken | `open` |

`business_licensing` is `open`, `permit_required` (hold an active `permit:business`,
which a seat issues with `issue_business_permit` for the charter's `permit_fee_sim`),
or `state_only` (only a sitting seat may found a business).

With the defaults, the market is exactly as free as it was.

## Details that matter

- **One rule for the sign and for the till.** `GovernmentPolicy.price_band_refusal`
  is the only place the band is written. `post_price` asks it, and so does every
  settlement. So a price posted *before* a cap cannot be *paid* after it.
- **Per unit.** A `trade` is checked at `price_sim / quantity`. A price of 0 is a
  gift, and no floor forbids giving something away.
- **Checked before the conversation.** Talking to the seller cannot make an
  unlawful price lawful, and telling a resident to go and speak first would cost
  it a wasted call.
- **Refusals quote the rule.** They give the item, the band, the policy version,
  and the tool that changes it.
- **Existing businesses are untouched** by a change of licensing. It governs
  founding a business, not trading.
- **The control bakery obeys them too.** It is the experiment's baseline, not
  above the law. Its script never raises and records a refusal as a finding.

## What differs from the review, and why

`docs/review/13-governance-organisations-justice.md` §5.4 proposes a sixteen-field
`PolicyBook` in a new `charter/policy.py`. It puts *structural* levers (licensing,
property, borders) behind the 3/5 amendment path, and *routine* ones (caps, wage
floor, relief) behind a seat tool.

Built instead:

1. **Fields on `GovernmentPolicy`, not a new model.** The policy history, the
   snapshot and restore, and the treasury view already carry `GovernmentPolicy`.
   A second model would need all three rebuilt, and the review itself says the
   book "extends, does not replace" it.
2. **Licensing is a seat tool, not an amendment.** An amendment needs three of five
   seats *and* the veto window. No run has ever filled one seat (GOV-3). Putting the
   lever behind a path that has never been walked would build a lever nobody can
   pull. It can move onto the charter later without changing any reader: the
   readers ask the policy, not the tool.
3. **Four fields now, not sixteen.** GOV-4's bar is four levers read at dispatch.
   Income tax, wage floor, rationing, relief, property rules, collective ownership
   and borders are still open, and each belongs to the item that needs it (ECON-11
   for relief, for example).

## How it is guarded

`app/world/tools/tests/test_policy_is_more_than_one_number.py` has 16 tests: the
defaults, the floor-above-cap refusal, seat-only setting, versioning, lifting a
control, and refusals at posting and at settlement. It also covers all three
licensing modes, founders charged nothing when refused, a seat founding under
`state_only`, and a source check that each lever is read where it acts.
