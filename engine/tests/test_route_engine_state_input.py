"""M4 route:engine — the component's state snapshot reaches the engine's turn.

A route:engine action is a block_input on a completed thread (no live waiter), so
resume_block runs a fresh turn. The dict ``value`` (the component's state) must be
carried into that turn — previously it was dropped (only a string value survived).
The craft gate must also thread through so the wake may re-author a craft.

resume_block uses only self.pending_inputs + self.handle_input + the static
helpers, so a bare instance with a stubbed handle_input exercises the real path.
"""

from __future__ import annotations

import pytest

from src.engine_core.coordinator import Coordinator, craft_edit_var


def make_coord() -> Coordinator:
    coord = object.__new__(Coordinator)
    coord.pending_inputs = {}
    return coord


def capture(coord: Coordinator) -> dict:
    captured: dict = {}

    async def fake_handle_input(
        message,
        media=None,
        super_thinking=False,
        thread_id=None,
        options=None,
        thread_id_box=None,
        craft=False,
        **gates,
    ):
        captured.update(
            message=message,
            thread_id=thread_id,
            craft=craft,
            craft_edit=craft_edit_var.get(),
            gates=dict(gates),
        )
        return "thread-x"

    coord.handle_input = fake_handle_input
    return captured


@pytest.mark.asyncio
async def test_dict_value_snapshot_reaches_the_turn() -> None:
    coord = make_coord()
    captured = capture(coord)
    snapshot = {"stopped_trucks": ["t1", "t2"], "stopped_ratio": 0.3}

    await coord.resume_block(
        "thr_8f2",
        "craft-map",
        value=snapshot,
        text="rebalance fleet",
        craft=True,
    )

    msg = captured["message"]
    assert "rebalance fleet" in msg
    assert "stopped_trucks" in msg and "t1" in msg
    assert captured["thread_id"] == "thr_8f2"
    assert captured["craft"] is True
    assert captured["craft_edit"] is None


@pytest.mark.asyncio
async def test_string_value_keeps_plain_text_behavior() -> None:
    coord = make_coord()
    captured = capture(coord)
    await coord.resume_block("thr", "b", value="yes", text="", craft=False)
    assert captured["message"] == "yes"
    assert captured["craft"] is False


def test_compose_block_input_serializes_dict_after_command() -> None:
    msg = Coordinator.compose_block_input_message("rebalance", {"a": 1})
    assert msg.startswith("rebalance")
    assert '"a": 1' in msg


def test_detect_craft_edit_ignores_plain_snapshot() -> None:
    assert Coordinator.detect_craft_edit({"objective": "fuel"}) is None
    assert Coordinator.detect_craft_edit("hello") is None
    assert Coordinator.detect_craft_edit({"edit": "x"}) is None
