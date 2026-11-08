"""Engine Core - unified execution engine."""

from src.engine_core.planner import (
    build_capability_context,
    prepare_sub_agent_config,
    parse_charter_response,
)


def get_coordinator():
    """Lazy import to avoid circular dependency."""
    from src.engine_core.coordinator import Coordinator

    return Coordinator


__all__ = [
    "build_capability_context",
    "prepare_sub_agent_config",
    "parse_charter_response",
    "get_coordinator",
]
