# Spec: the town grows and builds itself

*Cross-cutting specification drawn from `18-growth-construction-expansion.md` (GROW), `13-governance-organisations-justice.md` (GOV), `11-world-tools-and-physics.md` (TOOL), `12-economy-and-life.md` (ECON) and `16-frontend-and-console.md` (UI). Read those for the file:line evidence; this document is the build plan.*

## 1. The target, stated so it can be failed

When the government approves an expansion, the town's own builders construct
the buildings and infrastructure from materials the town produced, paid for
from the treasury or by private clients, and the result is a real place that
residents can walk to, work in, and that the viewer draws. Nothing in that
sentence requires the operator except, optionally, a veto.

Acceptance, end to end, in a mock run with no model calls (so it can run in
CI) and then in a live rung-1 run:

1. Three residents are refused `craft` for want of a mill. The next day the
   government's perception carries "the town has no mill (4 refusals, 3
   residents)".
2. A seat holder posts a tender for a mill on a parcel; the treasury falls by
   the budget and an escrow account holds it.
3. Two licensed builders bid; one is awarded; a construction site appears on
   the map as a place.
4. Hands hired by the winning yard deliver planks and ingots into the site
   and work it under `keep_doing` for a day at one model call each.
5. An inspection pays the first milestone; at completion the site becomes a
   mill: `craft craft:wheat->flour` succeeds for a resident standing in it.
6. The viewer draws the site, then the building, without a page reload;
   `buildings`, `tenders`, `contracts`, `sites` rows exist in the trace.
7. A resume from checkpoint keeps the mill and the grown map.

## 2. Where it stands today

Nobody can build anything, and land approval grows no map on the host every
run uses. Four independent breaks, each fatal on its own:

| Break | Where | Finding |
|---|---|---|
| No production path creates a `Plot`; `build_structure` needs a permitted one | `tools/primitives/construction.py:98-102` | GROW-01 |
| No tool issues a `builder_license`; the genesis construction company holds none | `construction.py:210`, `charter/government.py:289-300` | GROW-02, GOV-07 |
| Nothing ever calls `complete_construction`; a completed sector would be named `"smithy (n15)"`, which satisfies no gate | `construction.py:157-198`, `map/service.py:277` | GROW-03, GROW-04 |
| The production host has `map_service=None`, and even the standalone host builds two `WorldMap`s so movement never sees growth | `run_genesis.py:2176`, `wiring.py:1424,1440` | GROW-05, GROW-06, UI-01 |

Around those: `petition` is a government-namespace tool and seats are vacant
because no election ever tallies (GOV-01, GROW-12); the bill of materials
exists twice with different constants and consumes raw timber and ore rather
than planks and ingots (GROW-08); processed goods have no consumer anywhere
(GROW-23); the viewer serves the static genesis map forever (GROW-14, UI-01).

## 3. Principles

- Every step is a tool call (ROADMAP Law 2). Institutions act through the
  registry, so the trace is the whole story.
- No new physics: sites, tenders and contracts compose `construction.py`,
  `expansion.py`, `map/service.py`, `charter/government.py`, `labour.py`,
  `commerce.py` and `living.py`.
- A site is a place. Then gates, `LocationIndex`, `keep_doing`, `store_item`,
  theft and perception all work on it for free.
- The operator is a brake, never a bottleneck: a veto window with
  auto-approval, configurable to zero for unattended runs.

## 4. The mechanism

### 4.1 Need signals the government can see

New `world/needs.py`, a pure function over `WorldState` returning ranked
needs with evidence, and a `town_needs` read tool (public, free, ungated).
Sources that already exist:

- `world.tool_refusals` grouped by `unmet_requirement` (`registry.py:672-694`):
  `location:mill` x4 is literally "no mill".
- Hunger streaks (`life/upkeep.py`), food totals in `world.inventory`, the
  family at-risk views.
- Wage promises unpaid vs residents with no business and no continuing
  action for N days (unemployment).
- Travel ticks from home entry tile to workplace, averaged per occupation.
- Occupancy per dwelling sector.
- Untreated medium and long ailments ("no hospital").
- Open petitions and complaints in the charter store.

The top three lines render into seat holders' perception as facts, never
instructions. A daily `needs_snapshots` trace table records what the
government was told.

### 4.2 Public works: tender, bid, award, inspect

Extend `charter/government.py` and expose as tools gated at the government
complex:

| Tool | Namespace | Effect |
|---|---|---|
| `post_tender(title, purpose, plot_id or footprint, budget_sim, deadline_days, milestones)` | government | escrows `budget_sim` from the treasury into `tender:<id>` via `ledger.move`; event `politics.tender.posted` |
| `bid_on_tender(tender_id, price_sim, days)` | public, requires `builder_license` | records a bid; event `business.tender.bid` |
| `award_tender(tender_id, bidder_id)` | government | creates a `Contract` and opens the site; event `business.contract.won` |
| `inspect_site(site_id)` | government, gated at the site | pays the next milestone from escrow when progress crosses its threshold; deducts penalty after the deadline |
| `cancel_tender(tender_id)` | government | refunds escrow |

Private clients use the same shapes: `offer_contract(contractor_id, site_id,
price_sim, milestones)` and `accept_contract(contract_id)`, co-located, with
escrow from the client's own account.

`Contract` is a new model in `charter/models.py`: id, tender id, contractor,
client (treasury or agent), site, price, milestones, penalty per day,
deadline, status. Trace tables `tenders`, `contracts`, `contract_payments`.

### 4.3 The construction site as a place

New `ConstructionSite` in `tools/models.py`, owned state, checkpointed:
site id, plot, footprint, purpose, owner (client), builder, contract,
bill of materials, delivered, labour hours required and done, status,
started tick. Progress is the minimum of materials delivered and labour done.

- `open_site(plot_id, footprint, purpose)` stamps the footprint as a sector
  immediately with canonical id `site:<id>` (`MapService.stamp_building`),
  creates a `Storage` with the same id at that location so deliveries are
  ordinary `store_item` calls, and replaces `build_structure`.
- `work_site(site_id, hours)` is a LABOUR tool gated at the site, allowed to
  the builder, the builder's employees (`Business.employee_agent_ids`) or the
  private owner. It consumes materials pro rata and adds hours. It joins
  `CONTINUABLE_TOOLS` (`state.py:278`) so a crew day is one model call per
  hand. This is what makes building a paid trade: the yard hires hands,
  promises wages, pays from milestone receipts.
- `abandon_site(site_id)` releases the sector.
- Completion is tick-driven from both composition roots: `sites_due_for_completion`
  and `complete_site` restamp the sector with a canonical purpose id
  (`mill:<id>`), register the purpose-to-gate alias so recipes gated on
  `mill` succeed there (fixes GROW-03), optionally register a `Business`,
  mint a `Storage`, mark tiles in `tile_owners`, emit
  `world.building.completed` with location, sector, centre tile and purpose.
  Institutional purposes (hospital, police station) flip a new
  `world.institutions` set that `life/health` and `crime` read.

Roads and infrastructure use the same site with tile-level effects:
`complete_site` for `road` calls a new `MapService.paint_road(tiles)` that
marks tiles as street, so A* cost and walking speed improve
(`pathfinding.py:257-258`, `core/movement.py:97-100`); `well` and
`water_line` lower household heat and food upkeep within N tiles. This is
the one runtime walkability change; in-flight journeys are re-validated.

### 4.4 Private construction

Same site, different authorisation: every footprint tile owned by the
caller in `tile_owners` plus a permit (`issue_permit`, fee to treasury).
Building without a permit is refused with `unmet_requirement="permit:build"`
and repeated attempts open a `CrimeCase` (DESIGN §2 "unlicensed building is
a crime"). A finished house becomes a dwelling the household can move into,
which lowers the shelter component of upkeep; rents emerge as `transfer`
with reason `rent`.

### 4.5 Land: petition, expansion with purpose, parcels, zoning

- Entry point: seed a caretaker government at genesis or make `petition`
  public with a seat-holder `endorse_petition` step (GROW-12, GOV-01). Without
  one of these the pipeline has no in-world entry.
- Approval: the government passes an expansion resolution by its own vote
  (reuse the amendment engine); the President holds a veto window of N ticks
  after which it auto-approves; N configurable, zero for unattended runs
  (GOV §5.3).
- `expand_land(purpose, depth)` (GROW-13): records the strip rectangle,
  writes `tile_owners` for it as government land with `from_expansion=True`,
  seeds resource zones when purpose is farmland, forest or ore, runs
  `open_walkable_corridors` after growth, and emits a delta payload with the
  strip rect, canonical id, purpose and entry tile (GROW-26).
- Parcels: `grant_plot(owner, footprint, purpose, permitted)` and
  `auction_plot / bid_plot / close_auction` create `tools.models.Plot` after
  `PlotRegistry.validate_claim` and write `tile_owners`. The four disconnected
  notions of land (GROW-09) collapse onto one `Plot` record.
- Zoning as policy: `GovernmentPolicy.zoning: dict[sector, set[purpose]]` and
  `set_zoning`; `open_site` refuses a purpose the zone forbids. This is the
  lever an ideology change pulls: collective ownership is a zoning rule plus
  an amendment forbidding `auction_plot`.

### 4.6 Materials: why builders have customers

Three sinks that do not exist today (GROW-23, GROW-08):

1. One bill of materials in `map/parcel.py`, in processed goods: planks per
   floor tile, ingots per perimeter tile, fittings from the smithy.
2. Tool durability: `iron_tool` required by harvest, mine, fell and craft
   beyond a base yield, worn per use in `world.tool_condition`, consumed at
   zero. The smithy's product becomes a recurring purchase.
3. Building decay: every completed building accrues decay per game-day;
   below a threshold its gate stops satisfying until `repair_site` consumes
   planks, ingots and labour. Forest gets finite yield with regeneration.

### 4.7 One map, and the viewer

- Construct `MapService` in `GenesisRunner.boot`, pass `world_map=map_service.world`
  into `build_world`, register it under `checkpoints.register("map")`, hand it
  to the admin, agents and location deps; make `MovementSystem.world_map` read
  the service so growth is visible to `move`, `list_places` and gates
  (GROW-05, GROW-06, GROW-07).
- The game client fetches `/map/tiled` (already served at
  `agents_api.py:495-506`) instead of the on-disk genesis file; `stamp_building`
  paints visual layers from a per-purpose template so the Phaser page draws
  it; site and building events carry tiles (GROW-14, UI-01, UI-33).
- The township page gets `site:` and `building:` prefixes in the zone bridge
  and a "new buildings" list; it cannot draw engine geometry, so the honest
  display there is a place card.
- Console: a Public works panel with tenders, bids, contracts, sites with
  progress, completed buildings (GROW-25).

## 5. Decisions the operator has to make

1. Who approves expansion in an unattended run: in-world vote with an
   operator veto window (recommended), or a console click.
2. Do the six workshops (mill, sawmill, smithy, carpentry, kitchen, loom
   house) exist at genesis as real buildings, or must the town build them?
   Recommended: stamp them as real sectors on the genesis strip and map each
   genesis business's premises onto the workshop it provides (TOOL-02,
   GROW-28), so the first town can work and the second mill is the one that
   gets built.
3. Bill of materials in processed goods (recommended) or raw.
4. Public works payment: escrow at posting (recommended) or pay on
   inspection from the live treasury.
5. Roads change travel speed (recommended; it is the cheapest infrastructure
   with a measurable benefit).
6. Which page is the viewer of record for construction: only the Phaser
   page can draw engine geometry.

## 6. Work items, in order

| # | Item | Findings | Effort | Depends on |
|---|---|---|---|---|
| G1 | Wire completion and zone regeneration into both tick roots, with an AST wiring test | GROW-04, GROW-24, TOOL-20 | S | none |
| G2 | One `WorldMap` and a `MapService` on the production host, checkpointed | GROW-05/06/07, UI-01 | M | none |
| G3 | Genesis licences for the yard; real workshop sectors; business premises satisfy the workshops they provide | GROW-02/10/28, TOOL-02 | S-M | TOOL-W1 canonical ids |
| G4 | Plot grant, auction, permit and licence tools; one `Plot` record | GROW-01/09, GOV-07 | M | G2 |
| G5 | Construction site: `open_site`, `work_site` (continuable), `complete_site` with canonical gate ids; unified BoM in processed goods | GROW-03/08/17/18 | M | G1-G4 |
| G6 | Tenders, bids, contracts, inspections, escrow | GROW-11, GOV-08 | M | G5, ECON W2 treasury outflows |
| G7 | Caretaker government or public petition; expansion resolution with veto window | GROW-12, GOV-01, GOV §5.3 | S | election tick job (GOV W1) |
| G8 | Expansion with purpose: ownership, zones, corridors, delta payload | GROW-13/20/26 | M | G2 |
| G9 | Viewer map path and Public works panel | GROW-14/25, UI-01/33 | M | G2 |
| G10 | Trace tables `petitions`, `buildings`, `sites`, `tenders`, `contracts`, `needs_snapshots` | GROW-19 | S | G5, G6 |
| G11 | Need signals and the `town_needs` tool | §4.1 | M | G10 |
| G12 | Materials sinks: durability, decay, repair, finite forest | GROW-23 | M | G5 |
| G13 | Zoning as policy | §4.5 | S | policy engine (doc 06) |

Each item's acceptance test is the corresponding paragraph in §4; §1 is the
whole-chain test that closes the spec.
