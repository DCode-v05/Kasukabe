"""The 27th block: a SourceDiscovered event folds to a "source" block with a
turn-scoped id, a display-only payload, gated by Gates.sources."""

from src.engine_core.blocks import BLOCK_TYPES, BlockSerializer, Gates, passes_gates
from src.engine_core.events import SourceDiscovered, TextDelta
from src.engine_core.blocks import source_block_id


def test_source_is_a_known_type():
    assert "source" in BLOCK_TYPES
    assert len(BLOCK_TYPES) == 27


def test_source_discovered_folds_to_source_block():
    ser = BlockSerializer(scope_id="scope")
    block = ser.fold(
        SourceDiscovered(
            thread_id="th1",
            handle="m1",
            kind="memory",
            data={"points": ["launch date set to Aug 14"]},
            turn_id="turn_8a1",
        )
    )
    assert block is not None
    assert block.type == "source"
    assert block.id == "source:turn_8a1:m1" == source_block_id("turn_8a1", "m1")
    assert block.thread_id == "th1"


def test_source_payload_is_display_only_flattened():
    block = BlockSerializer("scope").fold(
        SourceDiscovered(
            thread_id="th1",
            handle="a1",
            kind="asset",
            data={"name": "Stripe", "info": "account active"},
            turn_id="t",
        )
    )
    assert block.payload == {"kind": "asset", "name": "Stripe", "info": "account active"}
    assert "handle" not in block.payload
    assert "turn_id" not in block.payload
    assert "data" not in block.payload


def test_web_source_payload_carries_links():
    block = BlockSerializer("scope").fold(
        SourceDiscovered(
            thread_id="th",
            handle="w1",
            kind="web",
            data={"links": [{"title": "Going live", "url": "stripe.com/docs"}]},
            turn_id="t",
        )
    )
    assert block.payload["kind"] == "web"
    assert block.payload["links"][0]["url"] == "stripe.com/docs"
    assert "content" not in block.payload


def test_source_block_gated_by_sources_flag():
    block = BlockSerializer("s").fold(
        SourceDiscovered(
            thread_id="th",
            handle="w1",
            kind="web",
            data={"links": [{"title": "x", "url": "u"}]},
            turn_id="t",
        )
    )
    assert passes_gates(block, Gates(sources=True)) is True
    assert passes_gates(block, Gates(sources=False)) is False


def test_text_without_citations_is_byte_identical():
    block = BlockSerializer("s").fold(TextDelta(thread_id="th", text="hello"))
    assert block.type == "text"
    assert block.payload == {"text": "hello"}
    assert "citations" not in block.payload


def test_text_with_citations_carries_spans():
    span = {"start": 12, "end": 31, "sources": ["source:rid_8a1:m1"]}
    block = BlockSerializer("s").fold(TextDelta(thread_id="th", text="…Aug 14", citations=(span,)))
    assert block.payload["text"] == "…Aug 14"
    assert block.payload["citations"][0]["sources"] == ["source:rid_8a1:m1"]
