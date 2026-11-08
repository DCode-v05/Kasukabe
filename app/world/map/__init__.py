"""world/map: the Ville tilemap, Kasukabe's genesis config on top of it,
land expansion, and freeform-building plot ownership.

DESIGN.md section 1: "Town: Kasukabe. Base map lifted from the Smallville
Ville tilemap ..., extended with new districts" plus "Land expansion:
government petitions the President... On approval the app appends tiles
to the Tiled JSON, regenerates collision, and the town physically grows."
This package is the map half of world state (app/CONVENTIONS.md: "world/
owns ALL world state. Map, ticks, tool registry, ledger, charter, health,
crime, genesis"); ticks, the tool registry, the ledger, and the charter
are sibling packages under world/, not this one.

Typical usage for a process booting the real Kasukabe map:

    from pathlib import Path
    from world.map import MapService

    ville_dir = Path("smallville/environment/frontend_server/static_dirs/assets/the_ville")
    service, zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    service.world.find_sector("Hobbs Cafe").active  # True
    service.world.find_sector("The Rose and Crown Pub").active  # False, not genesis-active

Module boundary (app/CONVENTIONS.md): part of world/, so it owns world
state and must never import boto3.
"""

from __future__ import annotations

from world.map.blocks import SpecialBlockTables, load_special_blocks
from world.map.expansion import (
    DEFAULT_GENESIS_STRIP_DEPTH,
    FARMLAND_SECTOR_NAME,
    FOREST_SECTOR_NAME,
    GOVERNMENT_SECTOR_NAME,
    ORE_OUTCROP_SECTOR_NAME,
    TEMPLE_SECTOR_NAME,
    ExpansionError,
    GenesisZoneIds,
    LandExpansion,
    append_kasukabe_genesis_zones,
    approve_land_petition,
)
from world.map.genesis import (
    GENESIS_ACTIVE_CATEGORIES,
    apply_genesis_config,
    build_kasukabe_base_map,
    build_kasukabe_genesis_map,
    classify_sector,
)
from world.map.loader import load_ville_map
from world.map.model import (
    CROP_VARIETIES,
    FOREST_RESOURCE,
    ORE_METALS,
    Arena,
    BuildingCategory,
    GameObjectKind,
    NamedLocation,
    ResourceKind,
    Sector,
    SpawningLocation,
    TileCoord,
    WorldMap,
    build_world_map,
)
from world.map.parcel import BillOfMaterials, Plot, PlotError, PlotRegistry, compute_bill_of_materials
from world.map.pathfinding import PathfindingError, find_path
from world.map.service import EventSink, MapService
from world.map.tiled_json import (
    Edge,
    IdAllocator,
    TiledJsonError,
    build_world_map_from_tiled,
    load_tiled_map,
    regenerate_collision_layer,
    save_tiled_map,
)

__all__ = [
    "SpecialBlockTables",
    "load_special_blocks",
    "DEFAULT_GENESIS_STRIP_DEPTH",
    "FARMLAND_SECTOR_NAME",
    "FOREST_SECTOR_NAME",
    "GOVERNMENT_SECTOR_NAME",
    "ORE_OUTCROP_SECTOR_NAME",
    "TEMPLE_SECTOR_NAME",
    "ExpansionError",
    "GenesisZoneIds",
    "LandExpansion",
    "append_kasukabe_genesis_zones",
    "approve_land_petition",
    "GENESIS_ACTIVE_CATEGORIES",
    "apply_genesis_config",
    "build_kasukabe_base_map",
    "build_kasukabe_genesis_map",
    "classify_sector",
    "load_ville_map",
    "CROP_VARIETIES",
    "FOREST_RESOURCE",
    "ORE_METALS",
    "Arena",
    "BuildingCategory",
    "GameObjectKind",
    "NamedLocation",
    "ResourceKind",
    "Sector",
    "SpawningLocation",
    "TileCoord",
    "WorldMap",
    "build_world_map",
    "BillOfMaterials",
    "Plot",
    "PlotError",
    "PlotRegistry",
    "compute_bill_of_materials",
    "PathfindingError",
    "find_path",
    "EventSink",
    "MapService",
    "Edge",
    "IdAllocator",
    "TiledJsonError",
    "build_world_map_from_tiled",
    "load_tiled_map",
    "regenerate_collision_layer",
    "save_tiled_map",
]
