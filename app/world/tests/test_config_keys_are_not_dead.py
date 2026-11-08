"""§4.2 — a knob that changes nothing is worse than no knob.

`compaction_threshold` was written into every agent's database on every
boot, loaded into a config object, and read by nothing that decides
anything. It was described in this project as the supported way to tune
the frozen engine, and it was tuned, repeatedly, for weeks. Prompt bodies
reached 357 KB throughout.

The number that actually decides is the model's registered context
window; compaction fires at 80% of it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[3] / "engine" / "src"


def _decision_sites(key: str) -> int:
    """How many places read this key to branch, compare or compute -
    excluding the line that assigns it from configuration."""
    hits = 0
    for path in (ENGINE / "engine_core").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != key:
                continue
            # config.key = ... is the write, not a read
            parent_is_assign_target = isinstance(getattr(node, "ctx", None), ast.Store)
            if not parent_is_assign_target:
                hits += 1
    return hits


def test_the_dead_knob_is_not_written_at_all() -> None:
    """ENG-20 deleted the writer, which is better than documenting it.

    This test used to require the string "DEAD KNOB" in `pinned_boot.py`:
    the knob was written, read by nothing, and left in place with a note
    so that nobody would tune it believing it worked. ENG-20 removed
    `_tighten_compaction`, its call, `SIM_COMPACTION_THRESHOLD` and the
    `KASUKABE_COMPACTION_THRESHOLD` env var outright, so there is no
    longer a write to annotate - and a knob that does not exist cannot be
    mistuned, which is what the note was protecting against.

    The assertion is therefore inverted and strictly stronger: nothing
    writes it. Restoring the writer fails here even if the writer carries
    a perfectly honest "DEAD KNOB" comment.

    Read as text, not imported: the engine lives in its own virtualenv and
    importing it from the app suite couples two dependency sets that are
    deliberately separate.
    """
    source = (ENGINE / "sim" / "pinned_boot.py").read_text(encoding="utf-8")
    assert "compaction_threshold" not in source, (
        "the compaction_threshold writer is back in pinned_boot.py; it "
        "decides nothing (see the test below) and ENG-20 removed it"
    )


def test_the_knob_that_does_decide_is_the_context_window() -> None:
    """needs_compaction(used, window) fires at window * COMPACTION_THRESHOLD.
    Reproduced here from the frozen source rather than imported, for the
    same virtualenv reason."""
    source = (ENGINE / "engine_core" / "context_engineering.py").read_text(encoding="utf-8")
    assert "def needs_compaction" in source
    assert "context_window * COMPACTION_THRESHOLD" in source

    threshold = float(
        source.split("COMPACTION_THRESHOLD = ")[1].split()[0]
    )
    tokens = 90_000
    assert tokens >= int(40_000 * threshold)       # budgeted: compacts
    assert tokens < int(256_000 * threshold)       # advertised: never would


def test_compaction_threshold_still_reads_nothing() -> None:
    """If this ever starts failing, the knob became live - and the test
    above, which now requires it to be absent entirely, is wrong. Update
    both together."""
    assert _decision_sites("compaction_threshold") == 0
