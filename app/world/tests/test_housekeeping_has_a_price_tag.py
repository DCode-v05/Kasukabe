"""An open decision about money is shown what it costs.

`INFRA-19`. DESIGN section 4 says the learning centre is "metered on the
agent's own wallet, scheduled during sleep ticks". The awake-interval
batch runs it MID-MORNING, every 18 ticks, and run 23 shows single calls
at 107,289 input tokens for $0.0405 - against a $6 wallet, 0.7% of a life
per batch. Build Spec 3.7 asks whether housekeeping should be billed to
the resident at all.

That is a decision, and it is not this report's to make. What the report
can do is stop it being theoretical: the section this file guards is what
the current choice costs, per resident, on the run in front of you.

Second half of the finding is documentation rather than code:
`MODEL_ID` is pinned to Kimi in the seam, so `requested_model=kimi` with
`model=deepseek` is the EXPECTED signature of a correctly-rerouted
housekeeping call. A reader who finds that and assumes a bug goes looking
in the broker's routing - the one part working as designed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from world.run_report import housekeeping_section

APP = Path(__file__).resolve().parents[2]


def _calls(*rows) -> list[dict]:
    return [
        {"payer_agent_id": who, "call_class": kind, "cents_debited": cents}
        for who, kind, cents in rows
    ]


def test_it_reports_the_share_of_spend() -> None:
    """The one number the decision turns on."""
    lines = "".join(housekeeping_section(_calls(
        ("a1", "cognition", 70), ("a1", "background", 30),
    )))
    assert "30.0%" in lines, lines


def test_it_names_who_paid_the_most() -> None:
    """A town-wide share hides the case that actually matters: one
    resident whose morning was spent on somebody else's bookkeeping."""
    lines = "".join(housekeeping_section(_calls(
        ("quiet", "cognition", 50), ("quiet", "background", 1),
        ("busy", "cognition", 10), ("busy", "background", 40),
    )))
    table = lines[lines.index("| resident"):]
    assert table.index("busy") < table.index("quiet"), (
        "INFRA-19: the resident paying most for housekeeping is not shown "
        "first, so the case the decision is about is buried"
    )


def test_no_background_is_reported_as_unknown_not_as_free() -> None:
    """A zero here has two causes - nothing ran, or nothing was
    classified - and they are not the same fact. Calling it "free" would
    settle the design question with a measurement error."""
    lines = "".join(housekeeping_section(_calls(("a1", "cognition", 10))))
    assert "not evidence that the learning centre is free" in lines
    assert "purpose_source" in lines, (
        "the reader is not told how to find out which of the two it was"
    )


def test_an_empty_trace_says_the_trace_was_empty() -> None:
    lines = "".join(housekeeping_section([]))
    assert "was not reached" in lines
    assert "not that housekeeping was free" in lines


def test_the_open_decisions_are_named_not_resolved() -> None:
    """The report must not quietly take a side. Both questions are the
    operator's; the table is the evidence they need."""
    lines = "".join(housekeeping_section(_calls(
        ("a1", "cognition", 10), ("a1", "background", 5),
    )))
    assert "sleep ticks" in lines
    assert "Build Spec 3.7" in lines
    assert "open decisions" in lines


def test_the_report_calls_it() -> None:
    source = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    assert "for line in housekeeping_section(" in source, (
        "INFRA-19: the section is written and nothing calls it, so the "
        "decision stays theoretical"
    )


def test_the_model_mismatch_is_documented_where_it_is_seen() -> None:
    """`requested_model=kimi, model=deepseek` on every background row is
    expected. Documented at the COLUMNS, because that is where a reader
    meets it - not in a design doc they have no reason to open."""
    schema = (APP / "supabase" / "sqlite_schema.sql").read_text(encoding="utf-8")
    block = schema[schema.index("requested_model text not null") - 1600:]
    block = block[: block.index("requested_model text not null") + 40]
    assert "INFRA-19" in block, (
        "the expected model mismatch is not explained beside the columns "
        "that show it, so a reader finding it assumes a routing bug"
    )
    assert "deepseek" in block and "MODEL_ID" in block
