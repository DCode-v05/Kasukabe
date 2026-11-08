"""Plot/parcel ownership over freeform tile footprints.

DESIGN.md section 2: "Freeform building: licensed builders use a design
tool - arbitrary footprint on the tile grid, rooms, purpose. Bill of
materials auto-computed from geometry (timber/metal per tile)... Requires
plot rights + government permit." This module is the "plot rights" half:
given a set of tiles, is a claim on them valid (in bounds, unclaimed,
vacant, one connected shape), and who holds it. It does not decide who is
ALLOWED to build (permits/licenses/government approval is a government
tool call this module has no notion of) or what a completed building's
rooms/purpose mean at runtime (the tool registry's job once construction
finishes and the footprint becomes a real Sector) - it answers exactly
one narrower question and leaves those to whichever module owns permits
and construction.

Plot is a plain mutable dataclass, matching ledger/models.py's Account:
"internal, mutable ledger state, a plain dataclass" is the same shape a
Plot has here - PlotRegistry is this package's equivalent of Ledger,
holding the live dict of claims and mutating them directly rather than
replacing immutable snapshots on every ownership change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from world.map.model import TileCoord, WorldMap


class PlotError(ValueError):
    """Aggregated plot-claim failure, one report per attempt (matches
    engine/src/config.py's ConfigError convention) - a builder
    resubmitting a rejected footprint wants every problem with it at
    once, not one failure per resubmission."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid plot claim ({len(self.errors)} error(s)):\n  - {joined}")


@dataclass(frozen=True, slots=True)
class BillOfMaterials:
    timber: int
    metal: int


TIMBER_PER_FLOOR_TILE = 2
METAL_PER_PERIMETER_TILE = 1


def perimeter_length(footprint: frozenset[TileCoord]) -> int:
    """Counts footprint-edge segments exposed to a tile NOT in the
    footprint (including the map edge itself, for a footprint that
    touches x=0/y=0) - the ordinary rectilinear perimeter definition,
    generalized to an arbitrary (not necessarily rectangular) footprint.
    """
    exposed = 0
    for tile in footprint:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = tile.x + dx, tile.y + dy
            neighbor = TileCoord(nx, ny) if nx >= 0 and ny >= 0 else None
            if neighbor not in footprint:
                exposed += 1
    return exposed


def compute_bill_of_materials(footprint: frozenset[TileCoord]) -> BillOfMaterials:
    """Raw material for a footprint, by geometry.

    GROW-22 / GROW-08: NO PRODUCTION CALLER, and this one is BLOCKED, not
    merely unused. `tools/primitives/construction.py` computes its own
    bill at different rates (5 timber and 2 metal per tile, against 2 and 1
    here) and never imports this. That is two bills of materials for one
    town.

    They are not simply duplicates: the module comment above says these are
    RAW counts and that "whichever module ends up pricing construction
    contracts is free to apply its own markup or waste factor on top". So
    which one is right is operator decision 8 - bill of materials in
    processed goods, or raw - which is open. Unifying them either way picks
    that decision and changes what a building costs.

    Verified by AST on 2026-09-05: zero non-test call sites.
    """
    if not footprint:
        raise ValueError("cannot compute a bill of materials for an empty footprint")
    return BillOfMaterials(
        timber=len(footprint) * TIMBER_PER_FLOOR_TILE,
        metal=perimeter_length(footprint) * METAL_PER_PERIMETER_TILE,
    )


def is_contiguous(footprint: frozenset[TileCoord]) -> bool:
    """4-connected flood fill: every tile must be reachable from every
    other tile through the footprint itself. A licensed builder's design
    tool produces one structure per plot, not several disconnected
    islands sharing a plot id."""
    if not footprint:
        return False
    start = next(iter(footprint))
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = current.x + dx, current.y + dy
            if nx < 0 or ny < 0:
                continue
            neighbor = TileCoord(nx, ny)
            if neighbor in footprint and neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen == footprint


@dataclass
class Plot:
    """One claim on a tile footprint. owner is None for government-held
    or not-yet-assigned land (still a valid, registered plot - it just
    has no private holder yet); purpose is free text (see this module's
    docstring for why it is not a closed enum: a plot can predate the
    building that eventually goes up on it, including institutions like
    a hospital or police station DESIGN.md section 1 says the town must
    build for itself, which have no BuildingCategory member to name
    them with).
    """

    id: str
    footprint: frozenset[TileCoord]
    purpose: str
    owner: str | None
    permit_id: str | None = None
    registered_at_tick: int = 0

    @property
    def area(self) -> int:
        return len(self.footprint)


@dataclass
class PlotRegistry:
    """The live set of plot claims against one WorldMap.

    Every method is synchronous and pure data-structure manipulation, no
    I/O - matching ledger/core.py's Ledger.apply_movement (mutation) vs
    Ledger.move (async, the actual Supabase-adjacent boundary): a future
    tool-registry handler for the `build`/`register` primitives
    (DESIGN.md section 2) is the async boundary that calls into this
    class and then persists the mutation and emits a
    `world.plot.claimed`/`world.plot.transferred` event, the same
    two-layer split app/CONVENTIONS.md's "How modules talk" describes for
    world's internal async API - see service.py for that wrapper.
    """

    world: WorldMap
    plots: dict[str, Plot] = field(default_factory=dict)
    claimed_by_tile: dict[TileCoord, str] = field(default_factory=dict)

    def validate_claim(
        self, footprint: frozenset[TileCoord], *, exclude_plot_id: str | None = None
    ) -> list[str]:
        """Every problem with a proposed footprint, aggregated (empty
        list means the claim would succeed). exclude_plot_id lets a plot
        re-validate its own existing footprint (e.g. before a resize)
        without tripping over its own prior claim."""
        errors: list[str] = []
        if not footprint:
            errors.append("footprint must contain at least one tile")
            return errors

        for tile in footprint:
            if not self.world.in_bounds(tile):
                errors.append(f"tile {tile} is outside the map ({self.world.width}x{self.world.height})")
                continue
            if not self.world.is_walkable(tile):
                errors.append(f"tile {tile} is not walkable (occupied by existing terrain)")
            if self.world.sector_at(tile) is not None:
                errors.append(f"tile {tile} already belongs to an existing sector")
            holder = self.claimed_by_tile.get(tile)
            if holder is not None and holder != exclude_plot_id:
                errors.append(f"tile {tile} is already claimed by plot {holder!r}")

        if not is_contiguous(footprint):
            errors.append("footprint tiles must form one 4-connected shape")

        return errors

    def register(self, plot: Plot) -> None:
        errors = self.validate_claim(plot.footprint)
        if plot.id in self.plots:
            errors.append(f"plot id {plot.id!r} is already registered")
        if errors:
            raise PlotError(errors)
        self.plots[plot.id] = plot
        for tile in plot.footprint:
            self.claimed_by_tile[tile] = plot.id

    def transfer(self, plot_id: str, new_owner: str | None) -> Plot:
        plot = self.plots.get(plot_id)
        if plot is None:
            raise PlotError([f"no such plot {plot_id!r}"])
        plot.owner = new_owner
        return plot

    def release(self, plot_id: str) -> Plot:
        plot = self.plots.pop(plot_id, None)
        if plot is None:
            raise PlotError([f"no such plot {plot_id!r}"])
        for tile in plot.footprint:
            self.claimed_by_tile.pop(tile, None)
        return plot

    def owner_of(self, tile: TileCoord) -> str | None:
        plot = self.plot_at(tile)
        return plot.owner if plot is not None else None

    def plot_at(self, tile: TileCoord) -> Plot | None:
        plot_id = self.claimed_by_tile.get(tile)
        return self.plots.get(plot_id) if plot_id is not None else None

    def plots_owned_by(self, owner: str) -> list[Plot]:
        return [plot for plot in self.plots.values() if plot.owner == owner]
