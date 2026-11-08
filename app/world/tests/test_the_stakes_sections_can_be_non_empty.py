"""The report's Prices, Stakes and Wages sections can report something.

`CORE-03`. All three read the `events` table, and in a genesis run they
could never have been non-empty, for four separate reasons at once:

  (a) the embedded runner did not write world `events` rows at all -
      `attach_persistence` skipped `record_event` because an earlier
      version had grown the store to 21 GB;
  (b) the price event was read as `business.price.posted` and is emitted
      as `economy.price.posted`;
  (c) `charge_the_day_tax` passed `event_sink=None`, so
      `economy.head_tax_debt.accrued` was never published at all;
  (d) the section text then asserted "**Nobody posted a price** ... every
      run before this one produced the same zero" over a run with twelve
      posts.

(a) is closed by EVAL-02/INFRA-16's selective recording and (b) by
EVAL-01's names-read-off-the-emitters. This file covers (c) and the
docstring half of (d): the footer told readers the trace store "does not
carry the conversation - the world's event bus is not bound to it in a
genesis run", which stopped being true and stayed on the page. Advice
that is right for the wrong reason is worse than advice that is wrong,
because nobody re-examines it.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from world import run_genesis, run_report

APP = Path(__file__).resolve().parents[2]


def test_the_head_tax_publishes_what_the_report_reads() -> None:
    """(c). `event_sink=None` meant the accrual event was never emitted,
    so the Stakes section read a stream nothing wrote to and printed a
    zero that read as a finding about the town."""
    source = inspect.getsource(run_genesis.GenesisRunner.charge_the_day_tax)
    assert "event_sink=None" not in source, (
        "CORE-03: the head tax is charged with no event sink, so "
        "`economy.head_tax_debt.accrued` is never published and the "
        "report's arrears figure is structurally zero"
    )
    assert "event_sink=self.ledger.event_sink" in source, (
        "the sink must be the ledger's own - the same route every other "
        "economy event takes, rather than a second one invented here"
    )


def test_the_report_reads_the_name_that_is_emitted() -> None:
    """(b), pinned here as well as in EVAL-01's own test, because this is
    the section that reported 'Nobody posted a price' over twelve."""
    report = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    assert '_events("economy.price.posted")' in report
    assert "business.price.posted" not in report, (
        "CORE-03: the report reads an event name nothing emits"
    )


def test_the_arrears_name_matches_the_emitter() -> None:
    emitter = (APP / "world" / "ledger" / "head_tax.py").read_text(encoding="utf-8")
    report = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    assert 'type="economy.head_tax_debt.accrued"' in emitter
    assert '_events("economy.head_tax_debt.accrued")' in report


# ── (d): the docstring that stayed true-sounding ───────────────────────


def test_the_module_no_longer_says_the_bus_is_unbound() -> None:
    """`attach_persistence` binds the bus and has for some time. A reader
    following the old paragraph never wondered whether the trace store was
    worth reading."""
    doc = run_report.__doc__ or ""
    unbound = "event bus is not bound to it in a genesis run"
    assert unbound not in doc or "NO LONGER TRUE" in doc, (
        "CORE-03: the module still tells readers the world's events do not "
        "reach the trace store"
    )


def test_the_rendered_footer_says_the_real_reason() -> None:
    """The advice - generate before stopping - is still right. What
    changed is why: not "the trace has nothing", but "this generator mixes
    live and stored reads, so a regenerated report blanks its live rows"."""
    report = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    footer = report[report.index("Written by `world/run_report.py`"):][:600]
    # "before stopping", not the whole sentence: this reads the SOURCE,
    # where the string is split across two lines for width, so a longer
    # phrase would fail on the concatenation rather than on the content.
    assert "before stopping" in footer, "the advice was dropped"
    assert "does not carry the conversation" not in footer, (
        "CORE-03: the footer still gives a reason that is no longer true"
    )
    assert "LIVE" in footer or "live row" in footer


def test_the_footer_names_the_evidence() -> None:
    """A caveat with a concrete example gets believed; one without gets
    skimmed. `run-20-report.md` is the example."""
    report = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    footer = report[report.index("Written by `world/run_report.py`"):][:600]
    assert "run-20-report.md" in footer
