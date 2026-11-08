"""M1 — passes_gates truth table + Gates defaults.

The per-stream gate filter the sink applies so a caller only receives the block
types it opted into.
"""

from src.engine_core.blocks import Block, Gates, passes_gates


def blk(t):
    return Block(type=t, payload={}, thread_id="t")


def test_always_types_pass_regardless_of_gates():
    g = Gates(tool=False, craft=False, telemetry="off")
    for t in ("text", "progress", "status", "error", "done"):
        assert passes_gates(blk(t), g) is True


def test_reasoning_requires_super_thinking():
    assert passes_gates(blk("reasoning"), Gates(super_thinking=False)) is False
    assert passes_gates(blk("reasoning"), Gates(super_thinking=True)) is True


def test_tool_gate():
    assert passes_gates(blk("tool"), Gates(tool=True)) is True
    assert passes_gates(blk("tool"), Gates(tool=False)) is False


def test_craft_gate():
    assert passes_gates(blk("craft"), Gates(craft=True)) is True
    assert passes_gates(blk("craft"), Gates(craft=False)) is False


def test_media_gates():
    for t in ("image", "voice", "audio", "video"):
        assert passes_gates(blk(t), Gates(**{t: True})) is True
        assert passes_gates(blk(t), Gates(**{t: False})) is False


def test_routing_is_always_on():
    # The dispatch says what the input became and which threads are running.
    # A client cannot read a multiplexed turn without it, so no gate hides it.
    for level in ("off", "trace", "full"):
        assert passes_gates(blk("routing"), Gates(telemetry=level)) is True


def test_telemetry_levels():
    trace_types = ("thread", "summary", "sub_agent", "replan")
    full_types = (
        "compaction",
        "budget",
        "model",
        "hook",
        "memory",
        "file",
        "subprocess",
        "session",
    )

    off = Gates(telemetry="off")
    trace = Gates(telemetry="trace")
    full = Gates(telemetry="full")

    for t in trace_types:
        assert passes_gates(blk(t), off) is False
        assert passes_gates(blk(t), trace) is True
        assert passes_gates(blk(t), full) is True
    for t in full_types:
        assert passes_gates(blk(t), off) is False
        assert passes_gates(blk(t), trace) is False  # trace does NOT show full
        assert passes_gates(blk(t), full) is True


def test_asset_gate():
    # The whole asset channel rides the dedicated asset gate, not telemetry:
    # default on, gateable off, and independent of the telemetry level.
    for t in ("asset",):
        assert passes_gates(blk(t), Gates()) is True  # default on
        assert passes_gates(blk(t), Gates(assets=True)) is True
        assert passes_gates(blk(t), Gates(assets=False)) is False
        assert passes_gates(blk(t), Gates(telemetry="off")) is True


def test_unknown_type_fails_open():
    assert passes_gates(blk("some_future_type"), Gates()) is True


def test_gates_defaults():
    g = Gates()
    assert g.tool is True and g.telemetry == "off"
    assert g.craft is False and g.craft_wait is True
    assert g.image is False and g.super_thinking is False
