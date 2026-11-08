"""M4 no-reemit rule — a route:engine data change must not re-author the craft.

A route:engine action mutates an asset and stops; the craft's polling surfaces the
change on the next tick. Re-emitting the craft would be a version bump, reserved
for deliberate edits (§9-§10). Two deterministic guards:

  1. The engine's turn input for a route:engine action never fabricates a <craft>
     tag (resume_block composes a reasoning input, not a craft).
  2. The authoring guide instructs the model not to re-emit for a data change.

The full behavioral proof (the model acts without emitting a craft) is the live
e2e in test_live_craft_e2e.
"""

from __future__ import annotations

from src.engine_core.coordinator import Coordinator
from src.engine_core.thread import compose_craft_guidance


def test_route_engine_turn_input_contains_no_craft_tag() -> None:
    msg = Coordinator.compose_block_input_message(
        "rebalance the fleet", {"stopped_trucks": ["t1", "t2"], "ratio": 0.3}
    )
    assert "<craft" not in msg  # the engine input never re-emits a craft
    assert "stopped_trucks" in msg  # but it does carry the component's state


def test_authoring_guide_states_the_no_reemit_rule() -> None:
    guide = compose_craft_guidance().lower()
    assert "re-emit" in guide  # the rule is taught
    assert "polling" in guide  # "its polling updates it"
    assert "route:engine" in guide  # the state-in-value rule is taught
