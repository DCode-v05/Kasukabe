*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line` relative to `app/` unless stated. Finding ids (`GROW-nn`) are referenced from the cross-cutting documents.*

# Review I: how the town grows (land expansion, construction, materials)

Prefix: **GROW**. Read-only review of `Real` @ 5400e94 (2026-09-03).
Scope: `app/world/map/**`, `app/world/charter/land_petitions.py` (+ government.py tenders/permits/licences), `app/world/admin_api.py` President routes, `app/game/console/*`, `app/world/tools/primitives/construction.py`, `property.py`, `production.py`/`recipes.py`/`resources.py`, `labour.py`/`civic.py` (hire/wage), `living.py` (`keep_doing`), the genesis construction company, and the viewer's map path (`game/position.py`, `game/world_client.py`, `game/server.py`, `game/static/index.html` + `js/town.js`, `township-v2.js` live mode). All paths below are relative to `/home/user/kasukabe/app/` unless they start with `/`.

Test run: `cd app && uv run pytest world/map -q` finished in 0.33 s: **116 passed, 47 skipped, 5 failed**. All 5 failures are `world/map/tests/test_the_town_is_walkable.py` and all fail on `town.movement.world_map` being `None` because `/home/user/kasukabe/smallville` (the Ville assets) is **not present in this checkout** (`bootstrap.py:152` logs the warning). The 47 skips are the same cause (`ville_dir` fixture skips). Nothing about the map code itself failed; see GROW-27 for the test-hygiene point.

---

## 0. Direct answers to the five questions

**Q1. Can any resident build anything in a live run today? No.** The chain `fell_timber -> craft planks -> build_structure -> place with tools` breaks at four independent points, any one of which is fatal:

1. `build_structure` requires `world.plots[plot_id]` to exist and be `permitted=True` (`construction.py:98-102`). **Nothing in production ever creates a `Plot`.** The only writers are tests (`world/tools/tests/test_construction.py:62-64`) and `MapService.claim_plot` (`world/map/service.py:310`), which has zero production callers. There is no tool to grant a plot, and no tool to set `permitted`. Every live call raises `NotFoundError("plot", ...)`.
2. `build_structure` requires an ACTIVE `builder_license` (`construction.py:210`, checked at `world/tools/registry.py:368-378` against `charter.store.list_licenses()`). `GovernmentActions.issue_builder_license` exists (`charter/government.py:289-300`) but **no tool wraps it** (grep of `issue_license|issue_builder_license|put_license` across `world/tools`, `run_genesis.py`, `wiring.py`, `admin_api.py` returns nothing outside tests). Genesis seeds no licences. Even the genesis construction company owner (`n01`, `genesis_20.json:2967-2975`) holds none.
3. Even if 1 and 2 were satisfied, the bill of materials is **raw `timber` and raw `{metal}_ore`** (`construction.py:116,121-122`), not planks or ingots. The sawmill/smithy steps that `recipes.py:188-191` says planks exist for are bypassed; a builder who milled planks holds the wrong item.
4. Even if a building completed, **nothing ever completes it in production**: `complete_construction` / `buildings_due_for_completion` (`construction.py:157-198`) have no caller in `wiring.py:1129-1170` (`advance_tick` calls only `regenerate_zones`) or in `run_genesis.py` (`run_tick` 4255-4290, `run_life_ticks` 4630-4700). And if it did complete, the minted sector is named `f"{purpose} ({owner})"` (`service.py:277`), e.g. `"smithy (n15)"`, and `satisfies_gate("smithy (n15)", "smithy")` is **False** (verified by running `place_aliases.satisfies_gate` under `uv`), so a built smithy would not satisfy `craft`'s `smithy` gate. Nor would it become a `Business`, a `Storage`, or a workplace.

The materials half works only partially: `fell_timber` at `forest`/`Kasukabe Forest` works (`resources.py:390-393`, gate alias `place_aliases.py:50-54`); `craft craft:timber->planks` works **only if the agent's `location_of` is literally `"sawmill"`** (`recipes.py:199`, gate `production.py:223-224`). `"sawmill"` is not a map sector, so `move` can only reach it by the opaque instant-jump fallback (`movement.py:502`). Run 23 shows the two-geographies failure: `location:mill` refused 4x for 3 agents and `location:sawmill` 2x (`/home/user/kasukabe/docs/runs/run-23-report.md:667-674`); the miller standing at her own premises `business:b_mill` cannot mill because `satisfies_gate("business:b_mill","mill")` is False (verified). `build_structure` was never called in any run (`run-23-report.md:680`).

**Q2. Expansion pipeline.** Links that exist: `petition` tool (GOVERNMENT namespace, seat + government complex; `civic.py:440-448`) -> `LandPetitionQueue.file_petition` (`charter/land_petitions.py:52-86`) -> `politics.petition.filed`; console `POST /president/petitions/{id}/approve` (`game/console/app.py:100-107` -> `world_client.py:162-169` -> world `admin_api.py:631-667`) -> `approve_petition` (charter status flip only, `land_petitions.py:98-118`) -> **if `deps.map_service` is not None** `MapService.expand_land` (`service.py:181-214`) -> `approve_land_petition` (`expansion.py:293-392`: clone, append rows/cols, carve one sector, `regenerate_collision_layer`, rebuild `WorldMap`) -> `world.land.expanded` event. Links that are unwired or broken: (a) the production host, `run_genesis.py`'s embedded app, passes `map_service=None` (`run_genesis.py:2176,2198,2203`), so approval logs a warning and grows nothing (`admin_api.py:651-661`); (b) even on `world/main.py`, `build_world_server` builds **two independent `WorldMap`s** (`wiring.py:1424` MapService vs `wiring.py:1440` `build_world()` which loads its own copy via `bootstrap.py:159`), so `MovementSystem`, `move`, `list_places` and `known_places` keep pathing on the **pre-expansion map** after `expand_land`; (c) the petition's `tile_count_requested` is never read by the expansion, the operator types `edge`/`depth` by hand (`console/schemas.py:105-115`); (d) `TileOwnership.from_expansion` (`tools/models.py:249-252`) is never set by anything (grep: no writer), so `auction_land`'s "expanded land" scoping is fiction; (e) the new strip is bare ground: no `ResourceZone`, no spawn point, no corridor join (`open_walkable_corridors` runs only at genesis: `genesis.py:234`, `service.py:170`), no visual tiles beyond grass gid 2 (`tiled_json.py:76`); (f) the viewer's `world.land.expanded` handler re-fetches `/api/map` (`town.js:709-716`) which `game/server.py:708-726` serves from `world_client.get_snapshot()` whose `map_tiled_json` is the **static genesis file on disk** (`game/world_client.py:191-212, 484`), never `GET /map/tiled` (`agents_api.py:495-506`) - so the browser redraws the unexpanded map; township-v2 live mode carries positions only. Neither the tick engine nor `run_genesis` ever calls expansion: the only caller is the admin route. Checkpoints: `MapService.snapshot/restore` exists (`service.py:330-356`) and is registered only in `wiring.py:1460-1461`; `run_genesis.py:3106-3127` registers ledger/world_state/locations/counters/charter/records and **no map**, so a live run's expanded map (if it ever grew) would not survive a resume. Petitions themselves do round-trip (charter store snapshot, `run_genesis.py:1698-1716`).

**Q3.** Designed in section 5.3 below (need signals, tender flow, construction site, private builds, land allocation/zoning, viewer), with files, tools, events, trace tables and acceptance tests.

**Q4. Materials economy.** Consumers today: `craft`/`cook` consume their recipe input (`production.py:182`); `eat` consumes food; `build_structure` would consume `timber` + `{metal}_ore` (never reached). Planks and furniture, ingots, `iron_tool`, `copper_wire`, `tin_ware`, jewelry, cloth: **no consumer anywhere** (grep of `"planks"|"furniture"|_ingot"|"iron_tool"|"cloth"` across `world/` outside tests and `recipes.py` hits only `cards.py:125-126`, which maps the words "furniture"/"cloth" to the artisan trade for `find_people`; nothing debits any of them). "Tool wear" is a sim-currency fee in `action_costs.py:531-557`, not an inventory item that wears (`inventory.py` has quantities only, no durability). No building has upkeep or maintenance (the daily upkeep at `life/upkeep.py:78-85` is a flat household `shelter` charge unrelated to any building). Forest is infinite (`resources.py:312-318`). There is therefore no demand for builders: nothing needs building to function (recipes gate on abstract strings that exist for free), nothing decays, and the only labour buyer, `hire_employee`+`set_wage`, was never called (`run-23-report.md:680`).

**Q5. Other breaks on this pipeline** are enumerated as GROW-01..GROW-30 below; the headline ones are the two-`WorldMap` split (GROW-05), the sector-name/gate mismatch on completion (GROW-03), duplicate bills of materials with different constants (GROW-08), `Sector.active`/classification of built sectors on restore (GROW-15), and trace tables `buildings`/`petitions` that are declared and never written (GROW-19).

---

## 1. Inventory

Status legend: **LIVE** = on the production run path (`world.run_genesis` embedded app, `sim_main.py`, or `game.server`); **LIVE-main** = reachable only via `world/main.py`'s standalone server, which no run uses (`start-genesis.sh:43` passes `--world-mode embedded`); **TEST-ONLY**; **ORPHANED** = no production caller.

| Path | Lines | Purpose | Status and evidence |
|---|---:|---|---|
| `world/map/model.py` | 585 | `TileCoord`, `Sector`, `Arena`, `WorldMap`, `build_world_map` | LIVE: `bootstrap.load_movement_world_map` -> `build_kasukabe_genesis_map` (`bootstrap.py:159`), used by `MovementSystem` |
| `world/map/genesis.py` | 238 | classify sectors, mint genesis zones | LIVE via `bootstrap.py:159`; `MapService.from_kasukabe_genesis` re-implements the same steps (`service.py:161-172`) |
| `world/map/loader.py` | 113 | CSV loader | ORPHANED in production: only `build_kasukabe_base_map` (`genesis.py:204`) calls `load_ville_map`, and nothing production calls that |
| `world/map/blocks.py` | 101 | special-block id->name tables | LIVE (both loaders) |
| `world/map/tiled_json.py` | 303 | Tiled JSON read/grow/collision | LIVE at genesis (append rows for genesis strip); `append_columns` reachable only through `approve_land_petition(edge=EAST)` (`expansion.py:343`); `save_tiled_map` ORPHANED (grep: no caller anywhere, so a grown map is never written to disk) |
| `world/map/expansion.py` | 521 | genesis zones, `approve_land_petition`, `open_walkable_corridors` | genesis half LIVE; `approve_land_petition` LIVE-main only (via `admin_api.py:643` when `map_service` is set) |
| `world/map/parcel.py` | 214 | `PlotRegistry`, `compute_bill_of_materials` | ORPHANED: `PlotRegistry` is instantiated in `MapService.__init__` (`service.py:145`) but `claim_plot/transfer_plot/release_plot` have no non-test callers; `compute_bill_of_materials` is never imported by `construction.py` |
| `world/map/walkability.py` | 33 | neighbour math | LIVE via pathfinding |
| `world/map/pathfinding.py` | 143 | A* with street preference | LIVE via `core/movement.py` |
| `world/map/service.py` | 356 | `MapService`: expand, stamp building, plots, checkpoint | LIVE-main only (`wiring.py:1424`); `run_genesis.py` never constructs one (`:2176,2198,2203`); `stamp_building` ORPHANED (only caller is `complete_construction`, itself unwired) |
| `world/map/__init__.py` | 128 | re-exports | LIVE |
| `world/charter/land_petitions.py` | 135 | file/approve/deny queue | LIVE for `file` (via `petition` tool) and `approve/deny` (via `/admin/president/petitions/*`, mounted by both hosts `run_genesis.py:2183`, `main.py:141`) |
| `world/charter/government.py` (tenders/permits/licences) | 385 | `post_tender/award_tender/cancel_tender`, `issue_license/permit/builder_license/revoke_license` | ORPHANED: no tool, route, or runner calls any of them (grep, section 0) |
| `world/charter/models.py` (`Tender`, `License`, `LandPetition`) | 624 | records | LIVE as store shapes; `Tender`/`License` rows never created in production |
| `world/tools/primitives/construction.py` | 212 | `build_structure`, `complete_construction`, `buildings_due_for_completion` | tool LIVE (registered `bootstrap.py:128`, served to every engine) but unusable (section 0); `complete_construction`/`buildings_due_for_completion` ORPHANED (callers: tests only) |
| `world/tools/primitives/property.py` | 438 | `transfer_tile`, `auction_land`, `set_will`, `read_will`, `settle_property_estate` | tools LIVE-registered, never called in any run (`run-23-report.md:680`); `settle_property_estate` wired via death path (HANDOVER audit item 2) |
| `world/tools/primitives/production.py` + `recipes.py` | 116 + 223 | craft/cook, recipe table | LIVE; 5 ok / 18 refused in run 23 |
| `world/tools/primitives/resources.py` | 174 | harvest/mine/fell_timber, zone regen | LIVE (`regenerate_zones` called `wiring.py:1142`; in `run_genesis` grep found no call: see GROW-24) |
| `world/tools/primitives/labour.py` | 305 | `set_wage`, `pay_wage`, `check_debts` | LIVE, wages never used |
| `world/tools/primitives/civic.py` | 503 | `petition`, `register_business`, `hire_employee`, ... | LIVE; `petition` visible only to seat holders (`registry.py:320-322`) and seats are vacant until an election (`charter/genesis.py:12-16`) |
| `world/tools/primitives/living.py` (`keep_doing`) | 548 | continuing actions | LIVE (`run_genesis.py:4335-4338`); `CONTINUABLE_TOOLS` excludes `build_structure` (`state.py:278`) |
| `world/tools/locations.py` | 117 | gate constants incl. `mill/sawmill/smithy/carpentry/kitchen/loom_house` | LIVE; docstring `:87-95` says these name where a *built* workshop "is expected to register itself"; no code does that |
| `world/tools/place_aliases.py` | 193 | gate satisfaction | LIVE (`registry.py:416`) |
| `world/admin_api.py` (President petition routes) | 1240 | `/admin/president/petitions*` | LIVE (both hosts) |
| `game/console/app.py`, `world_client.py`, `schemas.py` | 540/328/435 | operator console proxy | LIVE (console service on :8300) |
| `game/position.py` | 437 | tint, sprite roster, township zone bridge | LIVE (`game/server.py`) |
| `game/world_client.py` | 725 | HTTP client to world | LIVE; map served from static file (`:191-212, 484`) |
| `game/server.py` | 1044 | `/api/map`, `/api/township/live`, ... | LIVE |
| `game/static/index.html` + `js/town.js` | 50 + 729 | Phaser Ville view; handles `world.building.*`, `world.land.expanded` | LIVE page, but operator uses `/township-v2.html` (SYSTEM_REVIEW 1.7) |
| `game/static/js/township/township-v2.js` live mode | ~3180 | positions only | LIVE; draws its own 104x104 town, no engine map |
| `world/life/genesis.py` + `genesis_20.json` `construction_company` | - | one company `b_yard`, owner `n01`, hands `n03`,`n09`, 6000 sim | LIVE-seeded (`run_genesis.py:3446-3457`) as a `Business` at `business:b_yard`; nothing else references construction companies |
| `supabase/sqlite_schema.sql` `petitions`/`buildings` tables | - | trace tables | ORPHANED: `TraceStore` docstring `wiring.py:553-559` says they are not populated; grep confirms no writer |

---

## 2. How it works today (walkthrough)

### 2.1 Map boot
`run_genesis.GenesisRunner.boot` -> `build_world()` (`run_genesis.py:3074`) -> `load_movement_world_map(DEFAULT_VILLE_DIR)` (`bootstrap.py:134-160`) -> `build_kasukabe_genesis_map` (`map/genesis.py:208-238`): load `the_ville_jan7.json`, `append_kasukabe_genesis_zones` (30 rows south, five sectors side by side: government, temple, farmland x10 arenas, ore x5, forest; `expansion.py:197-290`), `open_walkable_corridors` (`:429-521`), `build_world_map_from_tiled`, `apply_genesis_config`. The resulting `WorldMap` is held **only** by `MovementSystem` (`bootstrap.py:312`). No `MapService` exists in a live run. If the assets are missing, `world.movement = None` and `move` degrades to instant jumps (`bootstrap.py:151-157`).

### 2.2 Location vocabulary (the "two geographies")
Agents stand at opaque strings in `LocationIndex` (`core/perception.py:75-107`), fed by `agent.moved`. Three vocabularies coexist: map sector names (`"Kasukabe Forest"`, `"Hobbs Cafe"`), tool gate slugs (`"forest"`, `"mill"`, `"sawmill"`, `"smithy"`, `"carpentry"`, `"kitchen"`, `"loom_house"`, `"government_complex"`; `locations.py:62-117`), and per-record ids (`home:h_field`, `business:b_mill`; `run_genesis.py:2968-2975`). `place_aliases.satisfies_gate` (`:99-116`) bridges only three sector<->slug pairs plus farmland/ore containment plus town hall<->complex. Workshops are reachable only by opaque jump because they have no tile (`movement.py:432-502`). A business premises never satisfies a workshop gate (verified: `business:b_mill` vs `mill` -> False).

### 2.3 Materials chain
`fell_timber` at `forest` credits `timber` from an infinite zone (`resources.py:312-318,390-393`). `craft craft:timber->planks` at `sawmill` (`recipes.py:194-200`); `craft craft:planks->furniture` at `carpentry` (`:202-207`); `mine` -> `{metal}_ore`; `craft craft:{ore}->{ingot}` and `craft:{ingot}->{good}` at `smithy` (`:163-183`). All 1-in/1-out (`recipes.py:16-18`). Each call is charged the LABOUR fee with proficiency discount (`action_costs.py:531-557`, `registry.py:481`). `keep_doing` can repeat `craft` (`state.py:278`).

### 2.4 `build_structure` as written
Handler `construction.py:95-154`: look up `world.plots[plot_id]` (must be `permitted`), footprint must be a subset of the plot, BoM = `area*5 timber` + `perimeter*2 {metal}_ore`, `completion_tick = now + area*6`, debit inventory, create `Building` (`tools/models.py:294-311`) in `world.buildings`, emit `world.building.construction_started` (no tile coordinates in payload). Gate: `location_gate=None`, `required_license_types={"builder_license"}` (`:209-210`). Completion: `complete_construction` (`:157-183`) optionally calls `MapService.stamp_building` (`service.py:218-306`) which paints `SECTOR_LAYER`/`ARENA_LAYER` only, mints sector name `f"{purpose} ({owner})"`, rebuilds `self.world`, emits `world.building.mapped`. Then status flips to COMPLETE and `world.building.completed` is emitted. **No production caller for any of the completion path.**

### 2.5 Expansion as written
See Q2 in section 0. The one real entry point is `POST /admin/president/petitions/{id}/approve` with `ApprovePetitionRequest{edge, depth, new_sector_name?, arena_names?}` (`console/schemas.py:105-115`; `admin_api.py:631-667`). On `world/main.py` this grows `MapService.tiled/blocks/world` and repoints `PlotRegistry.world`. It does not touch `world.movement.world_map`, `world.resource_zones`, `world.tile_owners`, `known_tool_locations`, or the trace beyond a generic `events` row.

### 2.6 Government side
`post_tender/award_tender/cancel_tender` (`charter/government.py:318-385`) and `issue_permit/issue_builder_license/revoke_license` (`:246-315`) exist with events, store persistence (`store.py`), checkpointing (`run_genesis.py:1698-1716`), and a governance read view (`governance_api.py:742-753`). None is exposed as a tool; `institutions.py` registers only `set_tax_rate, declare_candidacy, convene, appoint_police, arrest, rule, release` (`:446-549`). Seats are vacant at genesis (`charter/genesis.py:12-16`); the first election is scheduled at boot (`run_genesis.py:3481-3485`) but no run has reached it. Therefore `petition` (seat-gated, GOVERNMENT namespace) has been **invisible to every resident in every run** (`registry.py:320-322,456`).

### 2.7 Land ownership
`world.tile_owners` (`state.py:563`) is empty at genesis; `require_tile` (`property.py:55-70`) reads any unrecorded tile as government-owned. `auction_land` (seat-gated) and `transfer_tile` move single 1x1 units and money. There is no link from `tile_owners` to `Plot` (development right), to `Building`, to `Business.location_id`, or to `MapService.plots` - four unconnected notions of "who has this ground" (GROW-09).

### 2.8 Viewer
Phaser page (`index.html`) loads `/api/map` (`town.js:146`) -> `game/server.py:708-726` -> `world_client.get_snapshot().map_tiled_json` = static `the_ville_jan7.json` (`world_client.py:191-212,484`). Note this static file is the **pre-genesis-strip** base map (140x100), so even the genesis zones south of row 100 are not drawn; agents placed there fall back to hashed jitter positions (`position.py:170-185`). `world.building.*` events are handled by `addBuildingMarker` which no-ops without `tile_x/tile_y` (`town.js:382-393`); the real events carry none. `world.land.expanded` triggers `rebuildMapInPlace` -> same static file. `township-v2.html` (the page the operator actually uses) draws its own procedurally generated town and takes only `{id, zone, ...}` from `/api/township/live`; `position.py:214-248` has a `construction` district for `construction_site`/`workshop` ids that nothing emits.

---

## 3. Findings

Each: severity, location, evidence, why it matters, fix, effort (S/M/L), layer.

**GROW-01 (P0) No production path can create a `Plot`; `build_structure` is unreachable.**
`construction.py:98-102` requires `world.plots[plot_id]` with `permitted=True`. Writers: `world/tools/tests/test_construction.py:62-64,153-159`, and `MapService.claim_plot` (`service.py:310`) which has no non-test caller. `tools/models.py:196-209` docstring admits "this module never creates one (that is a not-yet-built government-permitting tool)". Blocks the target ("the town's own builders construct"). Fix: add a `grant_plot` government tool (section 5.3(e)) or make `build_structure` accept a footprint of tiles the caller owns via `tile_owners` (private path) and let a government tool grant/permit. Effort M. Layer: registry + world.

**GROW-02 (P0) No path issues a `builder_license`; the licence gate refuses everyone including the construction company.**
`construction.py:210`, check at `registry.py:368-378`. `GovernmentActions.issue_builder_license` (`government.py:289-300`) has no tool/route caller (grep in section 0). Genesis seeds none (`run_genesis.py:3446-3477` seeds businesses only). Fix: (a) genesis grants `builder_license` to the construction company owner and hands (in `seed_genesis`, via `charter.store.put_license`, fee 0, `issued_by="genesis"`) so DESIGN §3's "one construction company" is actually licensed on day 1; (b) add tools `issue_license`/`revoke_license` (GOVERNMENT namespace, gov complex) wrapping the existing charter actions and debiting `Charter.builder_license_fee_sim` (`charter/models.py:131`) to the treasury; (c) `apply_for_license` PUBLIC tool that files a request so a non-seat resident can ask. Effort S for (a), M for (b)(c). Layer: world + registry.

**GROW-03 (P0) A completed building does not become a usable place: sector name never satisfies any gate, and no Business/Storage/workplace is created.**
`service.py:277` names the sector `f"{purpose} ({owner})"`; `satisfies_gate("smithy (n15)","smithy")` is False (run under `uv`; `place_aliases.py:99-116` compares canonical keys and has no rule for built sectors). `complete_construction` (`construction.py:157-183`) creates no `Business`, no `Storage`, no `ResourceZone`, does not add the place to `known_tool_locations`, and does not set `Building.location_id` (the field `is_real_destination` looks for at `place_aliases.py:186-188` does not exist on `Building`, `tools/models.py:294-311`). `locations.py:87-95` promises "a built one is expected to register itself" and nothing does. Fix: on completion (i) stamp the sector with a canonical slug id (e.g. `smithy:<building_id>`), (ii) extend `place_aliases` with a `SECTOR_PROVIDES_GATE` map populated from `world.buildings` (purpose -> gate slug), so a building whose `purpose` is `smithy` satisfies `smithy`; (iii) optionally auto-register a `Business` owned by the plot owner at that location; (iv) record `Building.location_id`. Effort M. Layer: world (`construction.py`, `place_aliases.py`, `service.py`).

**GROW-04 (P0) `complete_construction` / `buildings_due_for_completion` have no caller; construction never finishes.**
`construction.py:186-198` docstring names `WorldServer.advance_tick` as the intended caller; `wiring.py:1129-1170` calls only `regenerate_zones`; `run_genesis.py` has no call (grep). Fix: call `buildings_due_for_completion` + `complete_construction(world, id, map_service=...)` in both `WorldServer.advance_tick` and `GenesisRunner.run_tick` (after `tick_engine.tick()`), publishing the returned event. Add a wiring test in `world/tests/test_track_wiring.py` asserting both roots reference it (the style used for `movement_system=`). Effort S. Layer: world.

**GROW-05 (P0) Two independent `WorldMap` instances on `world/main.py`; expansion and stamped buildings are invisible to movement, `list_places`, and gates.**
`wiring.py:1424` builds `MapService.from_kasukabe_genesis`; `wiring.py:1440` calls `build_world(genesis_tick, seed)` without `world_map=`, so `bootstrap.py:310` loads a second copy for `MovementSystem`. `expand_land` reassigns `self.world` (`service.py:209-212`) and `stamp_building` rebuilds `self.world` (`:291`), but `world.movement.world_map` (used by `move` at `movement.py:432`, and by `known_places` at `places.py:135-142`) is the stale object. `bootstrap.py:23-26` docstring even describes passing `world_map=` for exactly this reason. In `run_genesis` there is no `MapService` at all (GROW-06). Fix: pass `world_map=map_service.world` into `build_world`, and have `MapService` publish a `map_changed` hook that replaces `MovementSystem.world_map` (or make `MovementSystem` hold a reference to `MapService` and read `.world` each call). In-flight journeys computed on the old map should be re-validated. Effort M. Layer: world.

**GROW-06 (P0) The production host has no `MapService`; land approval is a logged no-op.**
`run_genesis.py:2176,2198,2203` hard-code `map_service=None`; `admin_api.py:651-661` warns "decision recorded, no physical map growth applied". ROADMAP T3 said `build_embedded_world_app` would be demoted to a fixture; instead it is the only host (`start-genesis.sh:43`). Fix: construct `MapService` in `GenesisRunner.boot` from the same `WorldMap` `build_world` loads (or build `MapService` first and pass `world_map=` to `build_world`), register it under `checkpoints.register("map", ...)`, and hand it to `AdminDeps`, `AgentsDeps`, `LocationDeps`. Effort M. Layer: world.

**GROW-07 (P1) Expanded map is not checkpointed in a live run.**
`run_genesis.py:3106-3127` registers no map component; `MapService.snapshot` exists (`service.py:330-343`). With GROW-06 fixed this becomes a resume-correctness bug: agents' `agent_tiles` beyond the old bounds would be out of `in_bounds` after restore. Fix: part of GROW-06. Effort S. Layer: world.

**GROW-08 (P1) Two bills of materials with different constants and different inputs.**
`parcel.py:56-57` (`TIMBER_PER_FLOOR_TILE=2`, `METAL_PER_PERIMETER_TILE=1`, `compute_bill_of_materials`) versus `construction.py:63-64` (`TIMBER_PER_TILE=5`, `METAL_PER_TILE=2`, `compute_perimeter`). `construction.py` never imports `parcel.compute_bill_of_materials`. `recipes.py:188-191` says planks are the construction input; `construction.py:121-122` debits raw `timber` and raw `{metal}_ore`, so the sawmill and smithy have no role in building. Why it matters: the target needs "materials the town produces" to be demanded by construction; today the only consumer of processed goods is nothing. Fix: one BoM in `parcel.py` (keep it pure), expressed in **processed** goods: `planks` per floor tile, `{metal}_ingot` per perimeter tile, plus a `nails/fittings` item from the smithy for doors; `construction.py` imports it. Effort S. Layer: world.

**GROW-09 (P1) Four disconnected notions of land: `tile_owners`, `WorldState.plots`, `MapService.plots` (`PlotRegistry`), and `Business.location_id`.**
`property.py` (1x1 `TileOwnership`, `state.py:563`), `tools/models.Plot` (development right, `:196-209`), `map/parcel.Plot`+`PlotRegistry` (footprint claims validated against map geometry, `parcel.py:108-214`), and buildings/businesses keyed by opaque location strings. `build_structure` checks only `tools.models.Plot.permitted`; it never checks `tile_owners` (a resident could be permitted to build on ground owned by someone else once plots exist), and `PlotRegistry.validate_claim` (the only code that checks "in bounds, walkable, no sector, contiguous") is never consulted by `build_structure`. Fix: make `tools.models.Plot` the single record and have its creation (the new `grant_plot`/`claim_plot` tools) validate via `PlotRegistry.validate_claim` and require `tile_owners` ownership or a government grant; delete `MapService.plots` or make it a view. Effort M. Layer: world.

**GROW-10 (P1) The genesis construction company is a business record and nothing more.**
`genesis_20.json:2967-2975` (`b_yard`, owner `n01`, hands `n03`,`n09`, 6000 sim) is seeded as a `Business` at `business:b_yard` (`run_genesis.py:3446-3457`). It has no licence (GROW-02), no plot, no tools beyond `hire_employee/set_wage/pay_wage`, no workplace on the map (`WORKPLACE_BY_OCCUPATION` sends `construction_worker` to the opaque `carpentry`, `run_genesis.py:226-227`, while the company's premises are `business:b_yard`), and its owner `n01` is also the town's only miner. Run 23: Silas Vance alone at `business:b_yard` saying "the carpenter needs timber" (`run-23-report.md:644-646`). Fix: genesis grants `builder_license` to owner and hands; give `b_yard` a stamped sector (a real yard on the genesis strip, like the temple) whose id satisfies the `carpentry` gate; seed a `Storage` at the yard; brief hands to work at the yard. Effort S-M. Layer: world + genesis data.

**GROW-11 (P1) Tenders, permits and contracts exist in charter/ but have no tool surface; ROADMAP T3's "open a tender, award a contract, issue and revoke permits" is unmet.**
`government.py:318-385` (tenders), `:246-315` (permits/licences) versus the registered institutional tools `institutions.py:446-549`. `Tender` has no bids, no milestones, no site, no payment (`charter/models.py:465-488` docstring: "bidding itself belongs to the not-yet-built business module"). Fix: section 5.3(b): tools `post_tender`, `bid_on_tender`, `award_tender`, `cancel_tender`, `inspect_site`, `pay_milestone`. Effort M-L. Layer: registry + world + charter.

**GROW-12 (P1) `petition` is invisible to everyone because seats are vacant and no election has ever concluded.**
`civic.py:445-447` (GOVERNMENT namespace, seat required) + `registry.py:320-322`; `charter/genesis.py:12-16`; run reports never crossed the first election. The whole expansion pipeline therefore has no in-world entry point in any run to date. Fix: either seed a caretaker government at genesis (`RichGenesis` field, e.g. the office administrator as interim PM until the first election), or allow any resident to `petition` (PUBLIC) with government endorsement as a second step. Effort S. Layer: world + genesis.

**GROW-13 (P1) Expansion ignores the petition and grows bare ground.**
`admin_api.py:643-649` takes `edge/depth` from the operator; `tile_count_requested` (`charter/models.py:562`) is unused. `approve_land_petition` carves at most one sector and never: seeds a `ResourceZone` for it (`resources.seed_resource_zones` runs once at boot, `bootstrap.py:308`), adds spawn points (`entry_point` will fall back to any walkable tile, `model.py:392-424`), joins the strip to the walkable graph (`open_walkable_corridors` is genesis-only), marks `TileOwnership.from_expansion`, paints anything but grass, or tells residents (no `known_tool_locations` entry, `known_places` only sees `world.movement.world_map`, GROW-05). Fix: `expand_land` takes a `purpose` (`farmland`, `forest`, `ore`, `residential`, `civic`, `industrial`) and (i) seeds matching `ResourceZone`s and gate aliases, (ii) runs `open_walkable_corridors` after growth, (iii) writes `tile_owners[...] = TileOwnership(GOVERNMENT, from_expansion=True)` for the strip (or records the strip rect so `require_tile` can derive it), (iv) emits `world.land.expanded` with the new sector's canonical id so perception can announce it. Effort M. Layer: world.

**GROW-14 (P1) The viewer never sees a grown map or a built structure.**
`game/world_client.py:191-212` loads the static genesis JSON (pre-genesis-strip base map); `:484` uses it for every snapshot; `game/server.py:708-726` serves it as `/api/map`; `town.js:709-716` refetches it on `world.land.expanded`. `world/agents_api.py:495-506` already serves `GET /map/tiled` from `MapService.tiled` but the game client never calls it (grep). `addBuildingMarker` needs `tile_x/tile_y` (`town.js:383-393`); the real events carry a footprint only in `Building`, not in payloads. Township-v2 live mode carries no buildings at all. Fix: (i) `HttpWorldClient.get_snapshot` fetches `/map/tiled` (fallback to static only when 503); (ii) `stamp_building` paints a wall/floor tile set onto `Wall`/`Interior Ground` visual layers (`tiled_json.VISUAL_LAYERS`) from a small per-purpose template so Phaser draws it; (iii) `world.building.mapped`/`completed` payloads carry the footprint's centre tile and the canonical location id; (iv) `township_zone_for` gets a `site:`/`building:` prefix rule (`position.py:250-256`). Effort M (frontend) + S (world). Layer: frontend + world.

**GROW-15 (P2) Built sectors are classified inactive on restore and unknown to `classify_sector`.**
`MapService.restore` calls `apply_genesis_config` (`service.py:349`); `classify_sector("smithy (n15)")` matches no `FALLBACK_KEYWORD_RULES` (`genesis.py:119-140`) -> empty categories -> `active=False`. `location_api.py:240,410` filters on `sector.active`, so a restored built place disappears from the location viewer. Fix: persist a `kind`/`purpose` per synthetic sector in `blocks` (or a sidecar dict in `MapService.snapshot`) and treat any stamped building as active. Effort S. Layer: world.

**GROW-16 (P2) `Building` records no location id, and `is_real_destination` looks for one that does not exist.**
`place_aliases.py:186-188` reads `building.location_id`; `tools/models.Building` has no such field. Dead branch. Fix with GROW-03. Effort S. Layer: world.

**GROW-17 (P2) `build_structure` has `location_gate=None`: a licensed builder could raise a building from across town.**
`construction.py:209`. DESIGN §2 requires every tool to be location-gated. Fix: gate on standing inside/adjacent to the footprint (resolve via `agent_tiles` and `MovementSystem.world_map`), or on the site place id once sites exist (section 5.3(c)). Effort S. Layer: world.

**GROW-18 (P2) Construction time is a countdown, not labour.**
`completion_tick = now + area*6` (`construction.py:115,133`); no crew, no hands, no `keep_doing`. `CONTINUABLE_TOOLS` (`state.py:278`) excludes any building verb. DESIGN §2: "construction occupies the crew for time proportional to size". Fix: section 5.3(c) `work_site` continuing action accumulating labour-hours. Effort M. Layer: world + registry.

**GROW-19 (P2) Trace tables `petitions` and `buildings` are declared and never written; land/building events land only in generic `events`.**
`supabase/sqlite_schema.sql:163-228`; `wiring.py:553-559` docstring; grep shows no writer. `buildings.building_type` check requires `permit_petition_id` for freeform buildings (`:222`), a link the world does not model. Fix: `TraceStore` subscribers for `politics.petition.*` -> `petitions`, `world.building.*` -> `buildings` (with `footprint`, `bill_of_materials` JSON), plus a new `tenders`/`contracts` table (5.3(b)). Effort S-M. Layer: world (`wiring.py`) + supabase schema.

**GROW-20 (P2) `TileOwnership.from_expansion` is never set; `auction_land` cannot tell expanded from genesis land.**
`tools/models.py:249-252`; no writer (grep). Fix with GROW-13. Effort S. Layer: world.

**GROW-21 (P2) `MapService.from_kasukabe_genesis` duplicates `build_kasukabe_genesis_map` step for step.**
`service.py:161-172` vs `map/genesis.py:224-238`. Two copies of "grow, join, build, classify"; a change to one (e.g. corridor `min_region`) silently diverges. Fix: `from_kasukabe_genesis` should call a variant of `build_kasukabe_genesis_map` that also returns `tiled`/`blocks`. Effort S. Layer: world.

**GROW-22 (P2) `loader.py` (CSV) and `parcel.compute_bill_of_materials` are dead in production; `save_tiled_map` and `append_columns` have no callers.**
Evidence in inventory. Not harmful, but the CSV loader's collision matrix is admittedly stale vs the JSON (`tiled_json.py:11-16`), so any future caller gets a different map. Fix: delete `loader.py` or mark test-only; wire `save_tiled_map` into the checkpoint sink (persisting the grown JSON to `data/` so the viewer and research can read it). Effort S. Layer: world.

**GROW-23 (P2) Processed goods have no sink and tools do not wear.**
`recipes.py` outputs `planks, furniture, cloth, {metal}_ingot, iron_tool, copper_wire, tin_ware, silver_jewelry, gold_jewelry`; only the crop->food chain has a consumer (`eat`). "Tool wear" is a sim fee (`action_costs.py:531-557`), not an item. The artisanry proficiency gained by Silas/Junia/Tobias in run 23 therefore converts into nothing the town needs. Fix: section 5.4 (materials economy): make `iron_tool` a consumable tool with durability that `harvest/mine/fell_timber/craft` require and wear; make buildings consume `planks`+`ingots` (GROW-08); add building maintenance (decay per day, `repair_site`). Effort M. Layer: world.

**GROW-24 (P2) `regenerate_zones` is called by `WorldServer.advance_tick` but not by `GenesisRunner.run_tick`.**
`wiring.py:1142`; `run_genesis.py:4255-4290` and `run_life_ticks` contain no call (grep `regenerate_zones` in `run_genesis.py` returned nothing). Finite farmland/ore never regenerate in a live run; with `DEFAULT_ZONE_YIELD=10_000` this has not bitten in a 6-hour run but will over 20 days. Fix: call it in `run_tick`. Effort S. Layer: world.

**GROW-25 (P2) Operator console cannot see or approve anything but land petitions and amendments on the growth path.**
`game/console/app.py:92-117` (petitions), amendments; no tender, permit, licence, or building views; `governance_api.py:742-753` exposes tenders/licences read-only under `/governance/active-policies` but the console `Town` tab does not render them. Fix: after 5.3, a `Public works` panel: open tenders, bids, awarded contracts, sites with progress, completed buildings. Effort M. Layer: frontend.

**GROW-26 (P3) `LandExpansion` event payload is a receipt, not a map delta.**
`expansion.py:101-118`: width/height/sector ids only. A subscriber (viewer, trace, perception) cannot learn where the new land is without re-fetching the whole Tiled JSON. Fix: include the strip rect, canonical id, purpose, and entry tile. Effort S. Layer: world.

**GROW-27 (P3) Map tests fail rather than skip when assets are absent; the construction full-chain test silently skips.**
`test_the_town_is_walkable.py:53,86` assumes `town.movement` exists (5 hard failures here); `test_construction.py:207-208` and the 47 `ville_dir` tests skip. In CI without `smallville/` the only end-to-end construction proof never runs and nothing reports that. Fix: make the walkability tests skip consistently, and make CI mount the assets so the chain test is not optional. Effort S. Layer: tests.

**GROW-28 (P3) `known_places` lists workshops that do not physically exist as "places you could go".**
`places.py:147-158` appends `mill/sawmill/smithy/carpentry/kitchen/loom_house/patent_office` as `kind="workshop"` with purpose text that says `craft` works there. They are opaque strings: a resident who `move`s there is teleported (`movement.py:502`), stands alone, and its journey is invisible on any map. This is the honest cause of run 23's `location:mill` refusals: the miller was at `business:b_mill` (a real shop) and the tool wanted the phantom `mill`. Fix: make the six workshops real genesis sectors (stamped on the genesis strip next to the temple) whose ids satisfy the gates, and map `b_mill`'s premises onto the mill sector so `business:b_mill` -> `mill`. Then remove the workshop shim from `known_places`. Effort M. Layer: world (`expansion.append_kasukabe_genesis_zones`, `place_aliases`, genesis).

**GROW-29 (P3) `WORKPLACE_BY_OCCUPATION` sends builders to a phantom and shop staff to buildings that are not their shops.**
`run_genesis.py:213-245`. `construction_worker`/`owner` -> `carpentry` (phantom); the grocer's shop is `business:b_grocery` but the clerk is sent to `Harvey Oak Supply Store`. Related to GROW-28; fix together by giving every genesis business a real sector.

**GROW-30 (P3) Stale docstrings claim the opposite of the code.**
`charter/land_petitions.py:21-24` and `charter/models.py:548-557` say map growth is "a separate, not-yet-built land/map module ... expected to consume the `politics.petition.approved` event" - in fact `admin_api.py` calls `expand_land` directly and nothing consumes the event. `admin_api.py:483-486` says `map_service` is absent "per run_genesis.py's own documented scope boundary", which contradicts ROADMAP T3. `wiring.py:553-559` says utterances/memories/skills are unpopulated, which SYSTEM_REVIEW 1.6 says is no longer true. Fix: correct the docstrings when the wiring changes. Effort S.

---

## 4. Gaps vs DESIGN.md

| DESIGN clause | State |
|---|---|
| §1 "government petitions the President ... on approval the app appends tiles ..., regenerates collision, and the town physically grows" | Built (`expansion.py`, `service.py`, `admin_api.py`) but unwired on the production host (GROW-06), invisible to movement/perception even on `main.py` (GROW-05), unreachable in-world because seats are vacant (GROW-12), and never drawn (GROW-14). |
| §1 "Deliberately absent: hospital, police station - the town must build its institutions" | Nothing can be built (GROW-01..04). `BuildingCategory` deliberately omits HOSPITAL/POLICE (`model.py:79-83`) but no path mints them. `life/constants.py:60-66` keeps chronic illness untreatable "until the town builds a hospital" with no hook that a built hospital would flip. |
| §2 "Freeform building: licensed builders use a design tool ... rooms, purpose. Bill of materials auto-computed ... Requires plot rights + government permit. Rendered procedurally from the Ville tileset. Construction occupies the crew for time proportional to size. Unlicensed building = crime." | Footprint + purpose: built. Rooms: not modelled (single arena, `service.py:279`). BoM: built twice with different numbers (GROW-08). Plot rights + permit: models exist, no grant path (GROW-01, GROW-02). Rendering: not built (GROW-14). Crew time: countdown only (GROW-18). Unlicensed = crime: refusal only, no `CrimeCase`. |
| §2 "every tool is location-gated" | `build_structure` is not (GROW-17). |
| §3 "Genesis seeds one construction company (owner + hands) that wins contracts from individuals/businesses and government tenders, hires employees, expands" | Company seeded as a plain business (GROW-10); tenders exist in charter with no tool, no bids, no contracts, no payment (GROW-11). |
| §7 "Government powers: ... permits, ... tenders, buildings, land petitions" | Only `petition` exists as a tool; permits/tenders/buildings have none (GROW-11). |
| §8 "President: land petitions (approve -> map grows)" | Console route exists; map does not grow on the live host (GROW-06). |
| §9 trace tables `petitions`, `buildings` | Declared, never written (GROW-19). |
| ROADMAP T3 "world/main.py becomes the only world server ... Every run mounts ... a real MapService" | Not done; the embedded app is still the host and mounts `map_service=None`. |
| ROADMAP T5/T6 (tool economy, rulesets) as they touch construction | An employment contract with milestones/penalties (T6 ruleset) is exactly what a construction contract needs; not started. |

---

## 5. Gaps vs the target

### 5.1 Thriving, organisations and government running
The government has a complete internal API for permits, licences, tenders and jobs (`charter/government.py`) and no way to exercise any of it except tax, police and courts. It also has no information: no view of housing, food stock, unemployment, or refusals feeds a seat holder's perception. The construction company cannot be a business in any economic sense (no licence, no plot, no demand). Wages exist but nothing forces a payroll (`labour.py:10-16`, by design) and no contract records what work was done for the wage.

### 5.2 Expansion and construction by the town's own builders
See section 0 (Q1/Q2) and GROW-01..GROW-14. Everything below in 5.3 is the mechanism to close it.

### 5.3 The complete mechanism (design)

Principles: every step is a tool (Law 2); no new physics beyond what `construction.py`, `expansion.py`, `service.py`, `government.py`, `labour.py`, `commerce.py` and `living.py` already do; every step emits an event and a trace row; sites are places so the existing location machinery (gates, `LocationIndex`, `keep_doing`, `store_item`, perception) does the work.

#### (a) Need signals the government can see
Sources that already exist:
- `world.tool_refusals` (`registry.py:672-694`) grouped by `unmet_requirement` - e.g. `location:mill` x4 in run 23 is literally "the town has no mill". `gap_feedback` (`state.py`, `GapFeedback`) already writes these back to agents.
- Household/food: `life/upkeep.py` hunger streaks, `family_api` at-risk views; `world.inventory` totals of food items (231 food units vs 20 hungry residents in run 23 is a distribution signal).
- Employment: `WagePromise` rows vs `world.agents` occupation; residents with no business and no continuing action for N days.
- Distance to work: `estimate_travel_ticks` between `home:*` entry tile and workplace (`core/movement.py`), averaged per occupation.
- Housing: `households` per house sector (`Sector.categories & {HOUSE}`) - occupancy per dwelling.
- Health: untreated MEDIUM/LONG ailments (`life/constants.py:60-66`) = "no hospital".
- Petitions and complaints already in the charter store.

Add: a **`town_needs`** read tool (PUBLIC, free, no gate; also `GET /governance/needs`) computed in a new `world/needs.py` (pure function over `WorldState`) returning ranked lines: `{"need": "mill", "evidence": "4 refusals by 3 residents in 2 days", "suggested": "public works: mill"}`, `{"need": "housing", "evidence": "h_quarry holds 5 adults in 1 dwelling"}`, `{"need":"hospital","evidence":"2 residents with untreated chronic ailments"}`. Render the top three lines into seat holders' perception (VOLATILE section, `retrieval_depth.render_perception_text`) so the government is told, never instructed. Trace: `needs_snapshots` (tick, need, score, evidence JSON) written daily by `TraceStore`.

Acceptance: a world with three `location:mill` refusals and no building of purpose `mill` returns `mill` as the top need; after a mill site completes, the need disappears on the next day's snapshot.

#### (b) Public works / tender flow
Extend `charter/government.py` and expose as tools (GOVERNMENT namespace unless noted), all gated at `government_complex`:
- `post_tender(title, purpose, plot_id | footprint, budget_sim, deadline_days, milestone_count)` -> existing `GovernmentActions.post_tender` plus new fields on `Tender`: `purpose`, `plot_id`, `deadline_tick`, `milestones: list[Milestone{share_bps, description}]`, `bids: dict[agent_or_business_id, Bid{price_sim, days, filed_tick}]`. Escrows `budget_sim` from `TREASURY_ACCOUNT_ID` into a per-tender account `tender:<id>` via `ledger.move` (so the invariant holds and the money is visibly committed). Event `politics.tender.posted` (exists) with the escrow tx id.
- `bid_on_tender(tender_id, price_sim, days)` PUBLIC, gated at `government_complex`, requires the bidder (or the business they own) to hold `builder_license`. Event `business.tender.bid`.
- `award_tender(tender_id, bidder_id)` -> existing `award_tender`; creates a **`Contract`** record (new pydantic model in `charter/models.py`): `contract_id, tender_id, contractor_id, client_id (TREASURY or a private agent), site_id, price_sim, milestones, penalty_bps_per_day, deadline_tick, status`. Creates the **construction site** (see (c)) on the tender's plot with the contractor as builder. Event `business.contract.won` (already named in CONVENTIONS' table).
- `inspect_site(site_id)` GOVERNMENT, gated **at the site**: reads `site.progress`; if the next milestone's threshold is met, pays `share_bps` of price from `tender:<id>` to the contractor (or their business account), event `business.contract.milestone_paid`; if past `deadline_tick`, deducts penalty into treasury.
- `cancel_tender` (exists) refunds escrow.
- Private clients use the same shape: `offer_contract(contractor_id, site_id, price_sim, milestones)` PUBLIC (co-located with the contractor), `accept_contract(contract_id)` PUBLIC; escrow from the client's own account. `inspect_site` for a private contract is the client's call, gated at the site.

Trace: new tables `tenders` (from `Tender`), `contracts` (from `Contract`), `contract_payments` (tx id, milestone index). Events: `politics.tender.posted/awarded/cancelled` (exist), `business.tender.bid`, `business.contract.won`, `business.contract.milestone_paid`, `business.contract.penalised`, `business.contract.completed`.

Acceptance: a seat holder posts a tender for a mill with budget 3,000; treasury falls by 3,000 and `tender:<id>` holds it; two builders bid; award creates a contract and a site; after the site reaches 50% an inspection pays 1,500 to the contractor's business; ledger invariant unchanged throughout; all rows present in `tenders`/`contracts`/`transactions`.

#### (c) The construction site as a place
New record `ConstructionSite` in `tools/models.py` (OWNED_STATE): `site_id, plot_id, footprint, purpose, owner_id (client), builder_id (contractor or private owner), contract_id | None, bill_of_materials: dict[item,int], delivered: dict[item,int], labour_hours_required: int (= area * HOURS_PER_TILE, from `parcel.compute_bill_of_materials` extended), labour_hours_done: int, status: PLANNED|ACTIVE|COMPLETE|ABANDONED, started_tick`. `progress = min(delivered/bom, labour_done/required)`.

Place: on `open_site` the site is **stamped as a sector immediately** (`MapService.stamp_building` with canonical id `site:<site_id>`, purpose `construction_site`) so it has tiles, an `entry_point`, and appears in `known_places` and on the map; collision stays open (a site is walkable ground with a fence). A `Storage` with id `site:<site_id>` is created at the same location so **material deliveries are `store_item`** (`commerce.py:365-388`) - no new delivery tool - and `withdraw_item` lets a builder recover materials if the site is abandoned. `theft` targets it like any unattended storage (`crime.py`), which is data.

Tools (registry, all PUBLIC unless noted):
- `open_site(plot_id, footprint, purpose)` - gated at the plot; requires the plot to be permitted (government) or fully owned in `tile_owners` by the caller (private, see (d)); replaces `build_structure`. Computes BoM and labour. Event `world.site.opened`.
- `work_site(site_id, hours: int = 1)` - LABOUR category, priced like `build_structure` (`action_costs.py:554-558`, skill `masonry`), gated **at the site**, requires the caller to be the builder, an employee of the builder's business (`Business.employee_agent_ids`), or the private owner; requires `delivered` to cover the next tranche of materials (materials are consumed pro rata as labour is applied, debiting the site storage). Adds `hours` to `labour_hours_done`. Added to `CONTINUABLE_TOOLS` so a crew does `keep_doing("work_site", until={"dusk": true})` and the whole day is one model call. Event `world.site.worked` (agent, hours, materials consumed). This is what makes builders a paid trade: the company owner hires hands (`hire_employee`), promises wages (`set_wage`), and pays from milestone receipts (`pay_wage`).
- `abandon_site(site_id)` - owner/builder; releases the sector, refunds nothing.
- Completion is tick-driven: `sites_due_for_completion(world)` (pure) + `complete_site(world, site_id, map_service)` called from both tick roots (fix GROW-04). It restamps the sector with the canonical id for its purpose (`mill:<site_id>` etc.), registers the purpose->gate alias so `craft` recipes gated on `mill` are satisfied there (GROW-03), creates a `Business` if the owner asked for one (`register_business` fee applies), mints a `Storage`, flips `Building` to COMPLETE, marks the tiles in `tile_owners` as the owner's, emits `world.building.completed` with `location_id`, `sector_id`, centre tile, and `purpose`. For institutional purposes (`hospital`, `police_station`) it flips the corresponding world flag that `life/health` and `crime` read (today `CHRONIC_UNTREATED_DAILY_HAZARD` has no such flag - add `world.institutions: set[str]`).

Roads and infrastructure as tile-level structures: `open_site` with `purpose in {"road","bridge","well","water_line"}` takes a footprint that may cross existing walkable ground; `complete_site` for `road` marks the tiles as street in a new `MapService.paint_road(tiles)` that sets `sector_grid` to 0 on those tiles and, if they were collision, clears collision - so `WorldMap.is_street` becomes true and both A*'s cost (`pathfinding.py:257-258`) and travel speed (`core/movement.py:97-100`, 40 vs 20 tiles/tick) improve. This is the one place walkability changes at runtime; `MovementSystem` must re-path in-flight journeys whose next tile became blocked (only demolition blocks; roads only open). `well`/`water_line` reduce household `UPKEEP_HEAT/FOOD` for households within N tiles (a real, measurable benefit of infrastructure).

Trace: `sites` table (one row per site, progress updated), `site_work` (agent, tick, hours, materials), `buildings` (finally written on completion, with `permit_petition_id` relaxed to nullable or pointing to the plot grant).

Acceptance (world-level, no model): a licensed builder opens a 6-tile mill site on a permitted plot; `store_item` of 30 planks and 12 iron ingots into `site:<id>` succeeds from the site tile and fails from elsewhere; two hands `keep_doing work_site until dusk` accumulate hours with one dispatch each per tick; when `labour_done >= required` and materials are consumed, the next tick completes it; `craft craft:wheat->flour` succeeds for an agent standing at the new mill sector and is refused for one at `business:b_mill` unless that premises was mapped onto the mill; the sector survives `MapService.snapshot/restore`; a `road` site across 20 tiles reduces `estimate_travel_ticks` between two given tiles.

#### (d) Private construction
Same site mechanism, different authorisation: `open_site` on land where every footprint tile is owned by the caller in `tile_owners` (bought by `auction_land`/`transfer_tile`) requires a **permit** (`issue_permit("build:<purpose>")` from the government, fee to treasury) but no tender. Without a permit: refusal with `unmet_requirement="permit:build"`, and repeated attempts are a `CrimeCase` (`DESIGN §2` "unlicensed building = crime"), which gives the complaints desk something to hear. The owner may build alone (`work_site` by the owner without a licence, slow - a licence halves `HOURS_PER_TILE` via the masonry proficiency discount) or contract the company via `offer_contract`. A finished house becomes a `home:<site_id>`-style dwelling the household can move into (`Household` gets an optional `dwelling_sector_id`), which lowers `UPKEEP_SHELTER` for renters vs owners - rents emerge as `transfer` with `reason="rent"` (loans already carry a reason field).

Acceptance: a resident with 4 owned tiles and a permit opens a house site, works it alone over N ticks, completes; `list_places` shows the house; the household's next upkeep bill shows no shelter component.

#### (e) How the map grows: parcels, allocation, zoning
- `petition` stays the government's act, but make it PUBLIC with a seat-holder `endorse_petition` step, or seed a caretaker government (GROW-12), or the pipeline has no entry.
- On approval, `expand_land(purpose, depth)` (GROW-13) records the strip rect and writes `tile_owners` for it as government `from_expansion=True`, seeds resource zones if purpose is farmland/forest/ore (new `ResourceZone` keyed `farmland:<crop>#2` with a gate alias so `harvest` works there), and emits the delta.
- **Parcel allocation**: new GOVERNMENT tools `grant_plot(owner_id, footprint, purpose, permitted: bool)` (creates `tools.models.Plot` after `PlotRegistry.validate_claim`; free grant = public works or charity) and `auction_plot(footprint, reserve_sim)` (opens a sealed-bid auction; `bid_plot` PUBLIC at the government complex; `close_auction` awards to the top bid, moves money to treasury, writes `tile_owners` for every tile and a `Plot`). This subsumes the 1x1 `auction_land`, which stays for edge cases.
- **Zoning as policy**: add `zoning: dict[sector_id, set[str]]` to `GovernmentPolicy` (`charter/models.py:240`) and a `set_zoning(sector, allowed_purposes)` GOVERNMENT tool; `open_site` refuses a purpose not allowed in the plot's sector with `unmet_requirement="zoning:<purpose>"`. Amendable like tax, so an ideology change (collective ownership, no private plots) is `set_zoning` + a charter amendment forbidding `auction_plot`, and the mechanics follow the politics.
- Zoned land is government revenue (auction) or public good (grant); both are trace rows.

Acceptance: petition -> approve(depth 20, purpose residential) -> strip owned by government -> `auction_plot` of a 4x3 parcel -> two bids -> close -> treasury up, 12 `tile_owners` rows -> `open_site` house -> refused when zoning says `industrial`, accepted after `set_zoning`.

#### (f) What the viewer needs
- World: `GET /map/tiled` consumed by `game/world_client` (GROW-14); `world.site.opened/worked/completed` and `world.building.completed` payloads carry `location_id`, `sector_id`, centre `tile_x/tile_y`, footprint bbox, `progress`.
- Phaser (`town.js`): draw a site as a hatched rectangle over its footprint with a progress bar; on completion, `rebuildMapInPlace` (already exists) after `stamp_building` paints visual layers from a per-purpose tile template (walls from `Room_Builder_32x32`, floor from interiors) - a 5-template set (house, workshop, civic, road, farm strip) is enough for "rendered procedurally".
- Township-v2 (the page in use): add `site:`/`building:` prefixes to `TOWNSHIP_ZONE_BY_PREFIX` mapped to the existing `construction` district for sites, and a small "new building" list in the Town drawer read from `/places`; it cannot draw engine geometry (HANDOVER §4.1), so the honest answer there is the place card, not tiles.
- Console: a Public works panel (GROW-25).

### 5.4 Materials economy (what makes builders a viable trade)
Demand must come from three sinks that do not exist today: (1) construction BoM in processed goods (GROW-08); (2) **tool durability**: `iron_tool` is required by `harvest/mine/fell_timber/craft` beyond a base yield (without a tool the per-unit sim fee doubles and yield halves), and each use decrements a `tool_wear` counter in a new `world.tool_condition[agent][item]`; at zero the tool is consumed - this makes the smithy's `iron_tool` a recurring purchase; (3) **building maintenance**: every completed building accrues `decay` per game-day; below a threshold its gate stops satisfying (a mill that "is broken") until `repair_site` (a small site on the same footprint) consumes planks/ingots and labour. Forest should get a finite-but-large yield with regen so timber has a price. All three are world-only changes and each has a one-line acceptance test (a building at decay 0 refuses `craft`; a tool at wear 0 disappears; a plank price appears on a board within N ticks of a tender).

### 5.5 Skills/tools that cut model calls (as it touches this scope)
`work_site` in `CONTINUABLE_TOOLS` is the lever: a crew day is one model call per hand. Masonry proficiency (`action_costs.py:434`) should discount labour-hours per repetition, not only sim, so mastery shortens construction (the compounding quantity ROADMAP asks for, measured in ticks-to-complete per site size).

### 5.6 Evolving goals, ideology, instrumentation (as it touches this scope)
Zoning and plot allocation as policy (5.3(e)) are the levers ideology pulls. Instrumentation: `sites`, `tenders`, `contracts`, `site_work`, `needs_snapshots` tables plus the existing `events`/`transactions`, so "the town built a mill because 3 residents were refused at the phantom mill on day 2, the government tendered on day 3, Holloway & Thorne Yard bid 2,400 and delivered on day 6 with 14 labour-hours from n03 and n09" is one SQL join.

---

## 6. Recommended work items (priority order)

**W1 (P0, S) Wire completion into both tick roots.** Call `buildings_due_for_completion`/`complete_construction` in `WorldServer.advance_tick` and `GenesisRunner.run_tick`; add a `test_track_wiring` AST test. Acceptance: a building created at tick t with `completion_tick=t+6` is COMPLETE after 6 ticks in a `GenesisRunner` mock run.

**W2 (P0, M) One WorldMap, one MapService, on the production host.** In `GenesisRunner.boot` build `MapService` first, pass `world_map=map_service.world` into `build_world`, register `checkpoints.register("map")`, hand it to `AdminDeps/AgentsDeps/LocationDeps`; in `wiring.build_world_server` pass `world_map=`. Make `MovementSystem.world_map` a property reading the service. Acceptance: approve a petition on the embedded app; `move` to the new sector succeeds and `list_places` lists it; resume from checkpoint keeps the grown map.

**W3 (P0, S) Genesis licences and a real yard.** Seed `builder_license` for the construction company owner and hands; stamp `b_yard` as a sector on the genesis strip whose id satisfies `carpentry`; map `b_mill` -> `mill`, `b_bakery` -> `kitchen` (or stamp the six workshops as real sectors, GROW-28). Acceptance: `craft craft:wheat->flour` by the miller at her premises succeeds; run-23's `location:mill` refusal class is empty in a mock run.

**W4 (P0, M) Plot grant + permit + licence tools.** `grant_plot`, `auction_plot/bid_plot/close_auction`, `issue_license/revoke_license`, `issue_permit`, `apply_for_license` as described; `Plot` creation validated by `PlotRegistry.validate_claim`. Acceptance: the tender/plot chain in 5.3(e).

**W5 (P0, M) Construction site.** `ConstructionSite`, `open_site`, `work_site` (continuable), `abandon_site`, `complete_site` with canonical gate ids and purpose->gate aliasing; BoM unified in `parcel.py` in processed goods. Acceptance: 5.3(c).

**W6 (P1, M) Tenders and contracts as tools.** 5.3(b). Acceptance: 5.3(b).

**W7 (P1, S) Caretaker government or public `petition`.** Acceptance: a resident can file a petition on tick 1 of a fresh run and the console lists it.

**W8 (P1, M) Expansion with purpose.** GROW-13/20/26. Acceptance: 5.3(e) first half.

**W9 (P1, M) Viewer map path.** `get_snapshot` reads `/map/tiled`; visual layers painted on stamp; site/building payloads carry tiles; township prefix rule. Acceptance: after approval and completion, `/api/map` height grows and a marker/tiles appear without a page reload.

**W10 (P1, S) Trace tables.** Write `petitions`, `buildings`, `sites`, `tenders`, `contracts`; fix the `permit_petition_id` check. Acceptance: rows exist after the chain in 5.3.

**W11 (P1, M) Need signals.** `world/needs.py` + `town_needs` tool + perception line for seat holders + `needs_snapshots`. Acceptance: 5.3(a).

**W12 (P2, M) Materials sinks.** Tool durability, building decay + `repair_site`, finite forest. Acceptance: 5.4.

**W13 (P2, S) `regenerate_zones` in `run_tick`; `build_structure` location gate; remove/merge `loader.py`, dead `PlotRegistry` methods, duplicate genesis path.**

**W14 (P3, S) Test hygiene.** Consistent skip when assets are absent; CI mounts `smallville/` so the full-chain construction test and the walkability tests actually run.

---

## 7. Open questions for the operator

1. **Who is the President for expansion in an autonomous run?** DESIGN §8 makes approval an operator act. The target says "when the government approves expansion the town builds it itself". Should approval move in-world (a 3/5 cabinet vote plus a size cap) with the operator retaining a veto, or stay a console click? The mechanism above works either way, but the trigger decides whether a 20-day unattended run can ever grow.
2. **Should the six workshops (mill, sawmill, smithy, carpentry, kitchen, loom house) exist at genesis as real buildings, or must the town build them?** Today they exist as phantom gate strings, which is the worst of both: recipes are gated on them, they cannot be walked to, and nobody needs to build them. DESIGN §1's genesis list has none of them; the 20-resident cast (a miller, a sawyer, a baker) assumes them.
3. **Materials in raw or processed goods?** GROW-08: DESIGN §2 says "timber/metal per tile"; `recipes.py` says planks. Processed goods give the sawmill and smithy a customer; raw goods make construction reachable sooner.
4. **Payment model for public works: escrow from treasury at posting (visible commitment, money locked) or pay-on-inspection from the live treasury (risk of an insolvent government defaulting, which is itself interesting data)?**
5. **Do you want roads to change travel speed?** It is the cheapest infrastructure with a measurable benefit (`STREET_TILES_PER_TICK` 40 vs 20), but it means walkability changes at runtime and in-flight journeys must be re-validated.
6. **Is the Phaser Ville page or township-v2 the viewer of record for construction?** Only the Phaser page can draw engine geometry; township-v2 can only show a place card. If township-v2 stays primary, "the viewer shows the new building" is a list entry, not a drawing.
7. **Ownership ideology:** should private plots exist at genesis (auction) or only by grant, and should a charter amendment be able to abolish private land (collective ownership)? Zoning-as-policy supports both; the default matters for what the free-market genesis means.
