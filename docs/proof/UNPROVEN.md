# What is proven, what is not, and what each one needs

*Generated from `docs/proof/findings.json` by the harness itself, on branch
`evals/proving-the-town`. Nothing below was typed by hand.*

**161 proven · 58 unproven · 0 failed.**

Unproven is not failure. It is the instrument refusing to tick a box it has no
evidence for, and it is the most useful column in the table.

| area | proven | unproven | failed |
|---|--:|--:|--:|
| corpus-coverage | 30 | 2 | 0 |
| replay | 19 | 0 | 0 |
| stories | 64 | 2 | 0 |
| verb-reach | 46 | 52 | 0 |
| verbs-not-in-the-registry | 2 | 2 | 0 |

## Why the number moves slowly, and what actually moves it

52 of the 58 open rows are `verb-reach`: *no resident has ever
called this verb in a recorded run*. Writing code does not close one. A story
proving the verb works does not close one either, and it should not - "the code
is correct" and "the code was reached" are different claims, and collapsing them
is how a gate stops meaning anything.

What closes them is a run. Two have been bought so far:

- **23 September, 8 residents, $10.42** - `vote` moved, because the scenario put
  an election in front of residents on their first morning. The government that
  won then sat all day and used none of its powers.
- **25 September, 12 residents, $17.88** - `set_tax_rate` moved. The only thing
  that changed was a sentence in the seat-holder's own perception: *"You hold a
  seat: Prime Minister. A seat may `set_tax_rate`, `set_price_control` and `rule`,
  at Kasukabe Government Complex."* She cut the head tax from 300 bps to 200, and
  the day boundary charged the town at the rate she set.

That is the shape of the remaining work: for each block of verbs, a sentence that
names them where the resident will read it, and a run that gives them the
occasion. Nine words moved GOV-3, GOV-4 and GOV-7 after twenty-three runs of
nothing.

## The 52 verbs still unreached

- `admit_to_org` · `appoint_police` · `approve_patent` · `arrest`
- `ask_for_a_child` · `auction_land` · `build_structure` · `come_of_age`
- `convene` · `define_business_tool` · `define_organization_tool` · `define_tool`
- `deny_subpoena` · `do_in_order` · `file_subpoena` · `fire_employee`
- `grant_plot` · `grant_subpoena` · `hire_employee` · `issue_builder_license`
- `issue_business_permit` · `leave_org` · `license_tool` · `marry`
- `pay_tax` · `pay_wage` · `petition` · `read_will`
- `register_org` · `reject_patent` · `release` · `revise_skill`
- `revise_tool` · `revive` · `revoke_license` · `revoke_patent`
- `rule` · `sell_tool` · `set_business_licensing` · `set_price_control`
- `set_wage` · `set_will` · `steal` · `table_amendment`
- `teach_skill` · `teach_tool` · `transfer_business` · `transfer_tile`
- `update_goal` · `vote_amendment` · `withdraw_item` · `work_site`

## The rows that are not about reaching

### fell_timber_batch (verbs-not-in-the-registry)

called 1 times but the world registry does not serve it

### search_memory (verbs-not-in-the-registry)

called 29 times, every one with no outcome recorded - the corpus does not say whether the town answered, so neither success nor refusal is evidenced here; registered engine-side by pinned_boot (engine/src/sim), not by the world registry this audit reads

### final6 (corpus-coverage)

no per-turn session files; this run cannot be re-audited

### teambox50 (corpus-coverage)

no per-turn session files; this run cannot be re-audited

### land can change hands (stories)

transfer_tile was refused: ActorRequirementError: transfer_tile: actor requirement(s) not met: tile (0, 0) belongs to government:treasury, not a004. Every tile starts as the treasury's; a granted plot is permission to build, not a deed; ownership moves only when a building completes, and completing one is a tick-loop job no resident can call. The treasury cannot sell either - the verb needs an owner with hands

### what a teacher keeps (stories)

the student paid 15 sim; the teacher is +0 sim after the call's own cost of 15 sim - teaching at this price earns nothing

## What each remaining row needs

| what | how many | what would close it |
|---|--:|---|
| verbs never reached | 52 | a run in which a resident chooses the verb, helped by a sentence that names it |
| land changing hands | 1 | a design decision: a deed verb, or a treasury that can act through a seat |
| what a teacher keeps | 1 | a design decision: whether a lesson's call should cost what the lesson earns |
| `final6`, `teambox50` | 2 | nothing. Their session files were never written; the two runs since keep theirs |
| `search_memory` | 1 | a future run, once the platform seam records a status for its own calls |
| `fell_timber_batch` | 1 | nothing to fix - a tool a resident invented this run, which the world registry does not serve because a resident wrote it |
