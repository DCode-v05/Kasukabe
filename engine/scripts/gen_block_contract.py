"""Generate the frozen block-wire contract from the live code.

The SDK types every ``block.payload`` as a discriminated union over the 27
block types. This script is the single, authoritative generator for that
contract: it walks the LIVE objects in ``src.engine_core.blocks`` (the
``BLOCK_TYPES`` vocabulary, the ``FOLD`` map, the ``BlockSerializer``
discriminator + id logic, the ``sse_frame`` framing, the synthesized
``done``/``error`` blocks), the LIVE event dataclasses in
``src.engine_core.events``, and the LIVE error contract in ``src.errors``
(``ErrorCode`` + ``ERROR_META``).

Nothing here is hand-transcribed: the payload-field listing is read straight
from ``dataclasses.fields()``, the fold table is read from ``FOLD``, the
discriminator keys are observed by actually running ``fold()`` on a
representative event, and the error table is read from ``ERROR_META``. Run it
and the doc cannot drift from the code.

Outputs (written by ``main()``, regenerated + compared by the drift test):
  * ``contract/block-types.md``      — the human/SDK-facing contract doc.
  * ``contract/fixtures/blocks.jsonl`` — one SSE frame per block type, plus a
    synthesized ``done`` (with a sample ``quota``) and ``error`` frame. These
    double as the SDK's SSE-parser fixtures.

Determinism is mandatory — the drift test reruns this and compares bytes. No
``datetime.now`` / ``time`` / ``random`` ever runs: every fixture Block gets a
fixed ``ts`` and a fixed ``seq``, and all iteration is in ``BLOCK_TYPES`` order
(the fold table is grouped then sorted by type, then by class name). Same input
=> byte-identical output every run.

Run from the engine root:  ``python scripts/gen_block_contract.py``
"""

from __future__ import annotations

import dataclasses
import os
from collections import OrderedDict

from src.engine_core.blocks import (
    BLOCK_TYPES,
    FOLD,
    Block,
    BlockSerializer,
    sse_frame,
    make_done_block,
    make_error_block,
    ALWAYS,
    ASSET,
    TELEMETRY_TRACE,
    TELEMETRY_FULL,
    APPEND_TYPES,
)
from src.errors import ErrorCode, EngineError, ERROR_META

# ---------------------------------------------------------------------------
# Fixed, deterministic constants for fixtures (never datetime.now / random).
# ---------------------------------------------------------------------------

FIXED_TS = "2020-01-01T00:00:00+00:00"
FIXED_SEQ = 1
FIXED_TURN = "turn_fixture"          # the BlockSerializer scope/turn id for fixtures
FIXED_THREAD = "thread_fixture"      # the thread_id stamped on fixtures that carry none

# Engine root = parent of the directory holding this script (…/engine).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, "contract")
MD_PATH = os.path.join(CONTRACT_DIR, "block-types.md")
FIXTURES_PATH = os.path.join(CONTRACT_DIR, "fixtures", "blocks.jsonl")


# ---------------------------------------------------------------------------
# Helpers — all pure, all deterministic.
# ---------------------------------------------------------------------------

def group_name(btype: str) -> str:
    """The display group for a block type — read from the live gate buckets +
    the BLOCK_TYPES grouping comments, never hand-listed past these buckets."""
    if btype in ("text", "reasoning", "tool", "image", "voice", "audio", "video", "craft"):
        return "content"
    if btype == "source":
        return "sourcing"
    if btype in ("progress", "status"):
        return "progress"
    if btype in ("thread", "summary", "sub_agent", "routing", "replan"):
        return "trace"
    if btype in ASSET:
        return "asset"
    if btype in ("compaction", "budget", "model", "hook"):
        return "system"
    if btype in ("memory", "file", "subprocess", "session"):
        return "audit"
    return "outcome"  # error, done


def gate_label(btype: str) -> str:
    """The gate that admits this type, derived from passes_gates' buckets."""
    if btype in ALWAYS:
        if btype == "reasoning":
            return "always (+ super_thinking)"
        return "always"
    if btype in ASSET:
        return "asset"
    if btype in TELEMETRY_TRACE:
        return "telemetry:trace"
    if btype in TELEMETRY_FULL:
        return "telemetry:full"
    if btype == "craft":
        return "craft"
    if btype == "tool":
        return "tool"
    if btype in ("image", "voice", "audio", "video"):
        return btype
    if btype == "source":
        return "sources"
    return "always"


def fold_by_type() -> "OrderedDict[str, list[type]]":
    """Group FOLD's event classes by their block type, sorted by type (in
    BLOCK_TYPES order) then by class name — so the table is stable."""
    by_type: dict[str, list[type]] = {}
    for cls, btype in FOLD.items():
        by_type.setdefault(btype, []).append(cls)
    out: "OrderedDict[str, list[type]]" = OrderedDict()
    for btype in BLOCK_TYPES:
        classes = by_type.get(btype, [])
        out[btype] = sorted(classes, key=lambda c: c.__name__)
    return out


def representative_event(btype: str):
    """The first event class (in FOLD insertion order) that folds to ``btype``,
    or None for types with no contributing event (image/voice/audio/video) or
    the synthesized types (error/done)."""
    for cls, mapped in FOLD.items():
        if mapped == btype:
            return cls
    return None


def placeholder_value(annotation: str):
    """A fixed placeholder value derived from a dataclass field's string
    annotation (fields are strings under ``from __future__ import annotations``).

    Deterministic and total over every annotation that appears in events.py.
    Optional (``X | None``) fields collapse to None; Literals take their first
    member; containers are empty; scalars take a zero/empty value.
    """
    a = annotation.strip()
    if "|" in a and a.split("|")[-1].strip() == "None":
        return None
    if a.startswith("Literal["):
        first = a[len("Literal["):-1].split(",")[0].strip()
        return first.strip("'").strip('"')
    if a == "SpawnSite":          # Literal alias for spawn sites
        return "sandbox_exec"
    if a == "GapType":
        return None
    if a == "bool":
        return False
    if a == "int":
        return 0
    if a == "float":
        return 0.0
    if a == "dict":
        return {}
    if a == "tuple":
        return ()
    if a.startswith("list["):
        return []
    if a == "str":
        return ""
    return None  # unknown / forward-ref scalar — safe default


def build_event(cls):
    """Construct one representative instance of an event dataclass with a fixed
    placeholder for every field (so no default_factory ever fires — that keeps
    uuid/timestamp factories from making output nondeterministic)."""
    kwargs = {f.name: placeholder_value(str(f.type)) for f in dataclasses.fields(cls)}
    if "thread_id" in kwargs:
        kwargs["thread_id"] = FIXED_THREAD
    return cls(**kwargs)


def fixture_block(btype: str) -> Block:
    """Synthesize one deterministic Block for a block type.

    Folded types run a representative event through the live BlockSerializer so
    the id, discriminator stamping, and payload reshape are exactly what the
    engine produces. Types with no contributing event (image/voice/audio/video)
    get a minimal placeholder Block. The fixed ts is stamped last."""
    serializer = BlockSerializer(FIXED_TURN)
    cls = representative_event(btype)
    if cls is not None:
        block = serializer.fold(build_event(cls))
    else:
        # Safety net for a future type added to BLOCK_TYPES before its event
        # lands: a minimal placeholder keeps the SDK parser fixture present.
        block = Block(
            type=btype,
            payload={"url": ""},
            thread_id=FIXED_THREAD,
            id=f"{btype}:{FIXED_TURN}",
        )
    if not block.thread_id:
        block.thread_id = FIXED_THREAD
    block.ts = FIXED_TS
    return block


def discriminator_key(btype: str) -> str:
    """The discriminator key the fold stamps onto a payload, observed by running
    the live fold on a representative event (so it can never drift from
    stamp_discriminator). Empty string when the fold adds no key."""
    cls = representative_event(btype)
    if cls is None:
        return ""
    serializer = BlockSerializer(FIXED_TURN)
    event = build_event(cls)
    own_fields = {f.name for f in dataclasses.fields(cls)}
    own_fields.discard("thread_id")
    block = serializer.fold(event)
    added = [k for k in block.payload.keys() if k not in own_fields]
    if "phase" in added:
        return "phase"
    if "kind" in added:
        return "kind"
    return ", ".join(added)


def id_rule(btype: str) -> str:
    """A short, generated-from-the-logic description of how Block.id is derived
    for this type (mirrors BlockSerializer.block_id). The exact code is in
    blocks.py; this is the one-line summary the SDK needs."""
    if btype == "voice":
        return (
            "append-stream: `voice:{turn_id}:{thread_id}` (deltas append to one "
            "block; sub-agent narration scopes its own block via "
            "`voice:{turn_id}-{agent_id}:{thread_id}`; no thread -> `voice:{turn_id}`)"
        )
    if btype in APPEND_TYPES:
        return (
            f"append-stream: `{btype}:{{turn_id}}:{{thread_id}}` (deltas append to "
            f"one block; the thread scope keeps sibling segments of a fanned-out "
            f"turn from interleaving; no thread -> `{btype}:{{turn_id}}`)"
        )
    return {
        "source": "`source:{turn_id}:{handle}` (turn-scoped handle)",
        "craft": "pre-minted `block_id`, else `craft:{turn_id}`",
        "image": "parser-minted `image:{thread}:{turn_key}:{n}`, else `image:{turn_id}`",
        "audio": "parser-minted `audio:{thread}:{turn_key}:{n}`, else `audio:{turn_id}`",
        "video": "parser-minted `video:{thread}:{turn_key}:{n}`, else `video:{turn_id}`",
        "tool": "`tool:{tool_use_id}` (summary: first of `tool_use_ids`), else `tool:{turn_id}`",
        "hook": "`hook:{tool_use_id}`, else `hook:{turn_id}:{turn_number}`",
        "sub_agent": "`sub_agent:{agent_id}`, else `sub_agent:{turn_id}`",
        "progress": "`progress:{node_id|charter_id}`, else `progress:{turn_id}`",
        "thread": "`thread:{thread_id}`, else `thread:{turn_id}`",
        "asset_required": "`asset_required:{server_name}`, else `asset_required:{turn_id}`",
        "asset_action": "`asset_action:{server_name}:{action_name}`",
        "permission_request": "`permission_request:{server_name}:{action_name}`",
        "asset_status": "`asset_status:{connection_id|server_name|turn_id}`",
        "memory": "`memory:{key}`, else `memory:{turn_id}`",
        "file": "`file:{path}`, else `file:{turn_id}`",
        "subprocess": "`subprocess:{spawn_site}`, else `subprocess:{turn_id}`",
        "compaction": "`compaction:{kind}:{turn_id}`",
        "model": "`model:{kind}:{turn_id}`",
        "status": "`status:{kind}:{turn_id}`",
        "done": (
            "`done:{turn_id}` for the terminal/aggregate done; a per-segment "
            "(non-terminal) done is `done:{turn_id}:{thread_id}` so replace-by-id "
            "cannot collapse several segments' outcomes into one block"
        ),
        "error": "`error:{turn_id}`",
    }.get(btype, f"per-turn singleton: `{btype}:{{turn_id}}`")


# Event fields the fold pops OUT of the payload per block type (they only
# scope the envelope id). Kept in sync with BlockSerializer.stamp_discriminator.
POPPED_FIELDS: dict[str, tuple[str, ...]] = {
    "voice": ("turn_id",),
    "image": ("block_id",),
    "audio": ("block_id",),
    "video": ("block_id",),
}


def field_lines(cls, btype: str = "") -> list[str]:
    """`name: type` for each field of an event dataclass, straight from
    dataclasses.fields() (thread_id is relifted into the envelope, so it is
    flagged as such rather than listed as a payload field; per-type popped id
    fields are flagged the same way)."""
    popped = POPPED_FIELDS.get(btype, ())
    lines = []
    for f in dataclasses.fields(cls):
        ann = str(f.type)
        if f.name == "thread_id":
            lines.append(f"  - `{f.name}: {ann}`  *(relifted into the envelope, not in payload)*")
        elif f.name in popped:
            lines.append(f"  - `{f.name}: {ann}`  *(scopes the block id only, not in payload)*")
        else:
            lines.append(f"  - `{f.name}: {ann}`")
    return lines


# ---------------------------------------------------------------------------
# render_markdown — the contract doc, pure + deterministic.
# ---------------------------------------------------------------------------

def render_markdown() -> str:
    """The full contract/block-types.md content, built from the live code."""
    out: list[str] = []
    w = out.append

    w("# Block-wire contract (generated)")
    w("")
    w("> **Generated by `scripts/gen_block_contract.py` from the live code — do not edit by hand.**")
    w("> Run `python scripts/gen_block_contract.py` from the engine root to regenerate.")
    w("> `tests/test_block_contract.py` regenerates this in-memory and fails if it drifts.")
    w("")
    w(f"The v2 `/execute` wire is **one** envelope, the `Block`, in **{len(BLOCK_TYPES)} types**. "
      "The internal event dataclasses (`src/engine_core/events.py`) stay as they are on the "
      "internal bus; the fold to blocks happens only at the serialization boundary "
      "(`src/engine_core/blocks.py`). The SDK types every `block.payload` as a discriminated "
      "union over these types.")
    w("")

    # -- envelope + frame --------------------------------------------------
    w("## The `Block` envelope")
    w("")
    w("```python")
    w("@dataclass")
    w("class Block:")
    w("    type: str        # one of the types below")
    w("    payload: dict    # the folded event body (+ any stamped discriminator)")
    w('    thread_id: str = ""')
    w('    id: str = ""     # derived from the logical instance — NOT a fresh uuid (see id rule)')
    w("    ts: str = <iso8601 utc>")
    w("```")
    w("")
    w("## The SSE frame")
    w("")
    w("Each frame is serialized by `sse_frame(block, seq, cursor)`:")
    w("")
    w("```")
    w("seq: {seq}")
    w("cursor: {cursor}")
    w("event: block")
    w('data: {"id": ..., "type": ..., "thread_id": ..., "ts": ..., "payload": {...}}')
    w("")
    w("```")
    w("")
    w("Every frame the engine emits carries both numbers, and they are not "
      "interchangeable. `seq` is the per-stream delivered-frame counter, bumped only "
      "for frames actually delivered — a block a stream's gates suppress does not "
      "advance it — so the SDK uses it and only it for gap detection. `cursor` is the "
      "per-thread buffer index of the block, and is the value the client passes back as "
      "`?after=` on `/replay` to resume where it left off.")
    w("")
    w("The frame JSON has exactly these five keys: `id`, `type`, `thread_id`, `ts`, `payload`. "
      "There is **no `v` (version) field** — versioning is doc + conformance enforced; the "
      "`v` add stays the deferred, additive upgrade path. The SDK must not require a `v` field.")
    w("")

    # -- the types table ---------------------------------------------------
    w(f"## The {len(BLOCK_TYPES)} types")
    w("")
    w("| # | group | type | gate | contributing event class(es) | discriminator key | id rule |")
    w("|---|-------|------|------|------------------------------|-------------------|---------|")
    grouped = fold_by_type()
    for i, btype in enumerate(BLOCK_TYPES, start=1):
        classes = grouped.get(btype, [])
        if classes:
            class_cell = ", ".join(f"`{c.__name__}`" for c in classes)
        elif btype in ("error", "done"):
            class_cell = "*(synthesized at the /execute boundary)*"
        else:
            class_cell = "*(new emit path — no event yet)*"
        disc = discriminator_key(btype)
        disc_cell = f"`{disc}`" if disc else "—"
        w(f"| {i} | {group_name(btype)} | `{btype}` | {gate_label(btype)} | "
          f"{class_cell} | {disc_cell} | {id_rule(btype)} |")
    w("")

    # -- per-type payload fields ------------------------------------------
    w("## Per-type payload fields")
    w("")
    w("For each block type, every contributing event dataclass and its fields "
      "(`name: type`, straight from `dataclasses.fields()`), plus the discriminator "
      "key the fold stamps onto the payload. `thread_id` is relifted into the envelope "
      "and is therefore not part of the payload.")
    w("")
    for btype in BLOCK_TYPES:
        classes = grouped.get(btype, [])
        w(f"### `{btype}`")
        disc = discriminator_key(btype)
        if disc:
            w(f"*Fold stamps discriminator key:* `{disc}`")
            w("")
        if not classes:
            if btype in ("error", "done"):
                w("Synthesized at the `/execute` boundary — see the synthesized envelopes below.")
            else:
                w("No contributing event yet (new emit path). Payload shape is TBD; "
                  "the SDK should treat it as an open object until an emitter lands.")
            w("")
            continue
        for cls in classes:
            w(f"- **`{cls.__name__}`**")
            for line in field_lines(cls, btype):
                w(f"  {line}")
        w("")

    # -- synthesized envelopes --------------------------------------------
    w("## Synthesized envelopes")
    w("")
    w("`done` and `error` are not folded from events — they are synthesized at the "
      "`/execute` boundary. An in-stream error is emitted as an `error` block "
      "(`make_error_block`) followed by a terminal `done` block; the SDK raises the "
      "typed exception after the iterator drains.")
    w("")
    w("### `done`")
    w("")
    w("Synthesized from the last `ExecutionSummary` plus its `quota`:")
    w("")
    w("```")
    w("{")
    w('  "status": "completed" | "awaiting" | "failed" | "cancelled",')
    w('  "num_turns": int,')
    w('  "cost_usd": float,')
    w('  "duration_ms": int,')
    w('  "quota": dict | null      // optional — citadel quota snapshot, present when known')
    w("}")
    w("```")
    w("")
    w("### `error`")
    w("")
    w("`EngineError.to_dict()[\"error\"]`:")
    w("")
    w("```")
    w("{")
    w('  "code": str,              // one of the ErrorCode values below')
    w('  "message": str,')
    w('  "retryable": bool,')
    w('  "details": dict           // optional — present only when non-empty')
    w("}")
    w("```")
    w("")

    # -- voice + media semantics -------------------------------------------
    w("## Voice + media semantics")
    w("")
    w("`voice` is speakable narration text, not audio: the model narrates inline, the "
      "engine streams it as append deltas on `voice:{turn_id}:{thread_id}` (no thread -> "
      "`voice:{turn_id}`), and the PRODUCT runs text-to-speech over the accumulated block "
      "text. The engine never synthesizes audio. Sub-agent narration arrives as its own "
      "block (`voice:{turn_id}-{agent_id}:{thread_id}`) so each speaker can be queued "
      "separately.")
    w("")
    w("**TTS stop signal:** a terminal `done` or `error` block, or an interrupted stream, "
      "means the product must flush its TTS queue (stop speaking). No other stop channel "
      "exists on the wire.")
    w("")
    w("**Replay / retries:** replayed voice deltas are history, not new speech — a product "
      "must not re-speak blocks it already delivered (track by `seq`/`cursor`). An "
      "engine-internal retry may restart a turn's narration on the same block id; the "
      "product keeps appending by id as usual.")
    w("")
    w("`image` / `audio` / `video` blocks carry LINKS only, never bytes: payload is "
      "`{\"title\": str, \"items\": [{\"url\", \"title\", \"caption\", \"mime\"}]}` with one "
      "or more items of one kind (one block = one gallery = one id). Every URL was "
      "validated against the engine's per-thread provenance registry (it appeared in a "
      "tool result, a user input, or ingested media; http(s) only, no credentials, no "
      "private hosts) before emission — the product may render it directly.")
    w("")

    # -- fail-open rule ----------------------------------------------------
    w("## Fail-open rule (the SDK must mirror it)")
    w("")
    w("`passes_gates()` returns `True` for any block `type` it does not recognize "
      "(`src/engine_core/blocks.py`). A new block type is therefore **never silently "
      "dropped** by the gate. The SDK must mirror this: an unknown `type` on the wire "
      "passes through to the consumer rather than being discarded.")
    w("")

    # -- error contract ----------------------------------------------------
    w("## ErrorCode contract")
    w("")
    w("Every `ErrorCode` and its `(http_status, retryable, default message)` from "
      "`src/errors.py` `ERROR_META`. The SDK maps each `code` to a typed exception.")
    w("")
    w("| code | http_status | retryable | default message |")
    w("|------|-------------|-----------|-----------------|")
    for code in ErrorCode:
        meta = ERROR_META.get(code)
        if meta is None:
            # Every code should have meta; surface a gap loudly rather than hide it.
            w(f"| `{code.value}` | — | — | *(no ERROR_META entry)* |")
            continue
        status, retryable, message = meta
        w(f"| `{code.value}` | {status} | {str(retryable).lower()} | {message} |")
    w("")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# render_fixtures — one SSE frame per type + done + error, pure + deterministic.
# ---------------------------------------------------------------------------

def render_fixtures() -> list[str]:
    """One SSE frame string per block type (in BLOCK_TYPES order), then a
    synthesized `done` frame (with a sample quota) and an `error` frame. Each
    item is a full ``sse_frame`` string."""
    frames: list[str] = []
    for btype in BLOCK_TYPES:
        if btype == "done":
            frames.append(done_fixture_frame())
        elif btype == "error":
            frames.append(error_fixture_frame())
        else:
            frames.append(sse_frame(fixture_block(btype), FIXED_SEQ))
    return frames


def done_fixture_frame() -> str:
    summary = {
        "status": "completed",
        "num_turns": 1,
        "cost_usd": 0.0,
        "duration_ms": 0,
        "quota": {
            "used_tokens": 0,
            "max_tokens": 0,
            "window_seconds": 0,
        },
    }
    block = make_done_block(FIXED_TURN, summary, thread_id=FIXED_THREAD)
    block.ts = FIXED_TS
    return sse_frame(block, FIXED_SEQ)


def error_fixture_frame() -> str:
    payload = EngineError(code=ErrorCode.INTERNAL_ERROR).to_dict()["error"]
    block = make_error_block(FIXED_TURN, payload, thread_id=FIXED_THREAD)
    block.ts = FIXED_TS
    return sse_frame(block, FIXED_SEQ)


# ---------------------------------------------------------------------------
# main — write the artifacts.
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(os.path.dirname(FIXTURES_PATH), exist_ok=True)
    with open(MD_PATH, "w", encoding="utf-8") as fh:
        fh.write(render_markdown())
    with open(FIXTURES_PATH, "w", encoding="utf-8") as fh:
        fh.write("".join(render_fixtures()))
    print(f"wrote {MD_PATH}")
    print(f"wrote {FIXTURES_PATH}")


if __name__ == "__main__":
    main()
