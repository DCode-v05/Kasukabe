"""Utility Directory — registry for agents, skills, and tools.

Four internal interfaces for three callers:
- UD-Meta (search.py reads): lightweight metadata for capability context
- UD-Full (Threads read): full definitions for execution hydration
- UD-Write (LC writes): user state management
- Tool Runtime (Threads receive): ToolDef, ToolRegistry, hydrate_tools
"""

from src.utility_directory.platform_registry import PlatformRegistry
from src.utility_directory.ud_meta import UDMeta, VERSION_USER
from src.utility_directory.ud_full import UDFull
from src.utility_directory.ud_write import UDWrite
from src.utility_directory.tool_runtime import (
    ToolDef,
    ToolBatch,
    ToolRegistry,
    hydrate_tools,
    partition_tool_calls,
)

__all__ = [
    "PlatformRegistry",
    "UDMeta",
    "UDFull",
    "UDWrite",
    "VERSION_USER",
    "ToolDef",
    "ToolBatch",
    "ToolRegistry",
    "hydrate_tools",
    "partition_tool_calls",
]
