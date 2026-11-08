"""The "special_blocks" side of the Ville data format: id -> name tables.

The maze CSVs (collision/sector/arena/game_object/spawning_location) carry
only integers per tile - a sector value of 32135 means nothing on its own.
The matrix/special_blocks/*.csv files are the other half: one row per id,
giving it a human name (and, for arenas/spawn points, the parent names
that place it in the hierarchy). loader.py reads these straight from CSV;
tiled_json.py's synthetic zones (expansion.py) build the same
SpecialBlockTables shape by hand for ids it mints itself - either way,
model.build_world_map only ever sees this one shape.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpecialBlockTables:
    """id -> name lookups for every special_blocks file, keyed exactly as
    model.build_world_map's parameters expect them.

    world: (id, world_name) - a single row in the source data (there is
    only ever one world per map), kept as a pair rather than a bare
    string so a caller can still ask "what id does the world block use"
    if a future expansion ever needs to mint a second one (it hasn't).
    """

    world: tuple[int, str]
    sectors: dict[int, tuple[str, str]] = field(default_factory=dict)
    arenas: dict[int, tuple[str, str, str]] = field(default_factory=dict)
    game_objects: dict[int, tuple[str, str]] = field(default_factory=dict)
    spawning_locations: dict[int, tuple[str, str, str, str]] = field(default_factory=dict)

    def add_sector(self, block_id: int, world_name: str, sector_name: str) -> None:
        self.sectors[block_id] = (world_name, sector_name)

    def add_arena(self, block_id: int, world_name: str, sector_name: str, arena_name: str) -> None:
        self.arenas[block_id] = (world_name, sector_name, arena_name)

    def add_game_object(self, block_id: int, world_name: str, object_name: str) -> None:
        self.game_objects[block_id] = (world_name, object_name)

    def add_spawning_location(
        self, block_id: int, world_name: str, sector_name: str, arena_name: str, label: str
    ) -> None:
        self.spawning_locations[block_id] = (world_name, sector_name, arena_name, label)


def read_csv_rows(path: Path) -> list[list[str]]:
    """Every field trimmed of whitespace (the source files are
    comma-space separated, e.g. "32135, the Ville, artist's co-living
    space", and at least one row has a trailing space on its last field -
    see sector_blocks.csv's "Ryan Park's apartment "). Blank rows (a
    trailing newline in the file) are dropped."""
    with path.open(newline="", encoding="utf-8") as f:
        rows = []
        for row in csv.reader(f):
            trimmed = [cell.strip() for cell in row]
            if any(trimmed):
                rows.append(trimmed)
        return rows


def load_special_blocks(ville_dir: Path) -> SpecialBlockTables:
    """Reads matrix/special_blocks/*.csv under ville_dir into one
    SpecialBlockTables. ville_dir is the "the_ville" asset root (the
    directory containing matrix/ and visuals/), matching loader.py's
    ville_dir parameter.
    """
    special_dir = ville_dir / "matrix" / "special_blocks"

    world_rows = read_csv_rows(special_dir / "world_blocks.csv")
    if not world_rows:
        raise ValueError(f"world_blocks.csv at {special_dir} has no rows")
    world_id, world_name = int(world_rows[0][0]), world_rows[0][1]

    tables = SpecialBlockTables(world=(world_id, world_name))

    for row in read_csv_rows(special_dir / "sector_blocks.csv"):
        block_id, wname, sname = int(row[0]), row[1], row[2]
        tables.add_sector(block_id, wname, sname)

    for row in read_csv_rows(special_dir / "arena_blocks.csv"):
        block_id, wname, sname, aname = int(row[0]), row[1], row[2], row[3]
        tables.add_arena(block_id, wname, sname, aname)

    for row in read_csv_rows(special_dir / "game_object_blocks.csv"):
        # Column 2 is "<all>" for every row in the upstream data (the
        # object kind is not arena-specific - see GameObjectKind's
        # docstring) - not modeled as a field, only the name is kept.
        block_id, wname, oname = int(row[0]), row[1], row[3]
        tables.add_game_object(block_id, wname, oname)

    for row in read_csv_rows(special_dir / "spawning_location_blocks.csv"):
        block_id, wname, sname, aname, label = int(row[0]), row[1], row[2], row[3], row[4]
        tables.add_spawning_location(block_id, wname, sname, aname, label)

    return tables
