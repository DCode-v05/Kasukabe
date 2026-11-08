"""EVAL-01: the report must not read event types nothing emits.

Run 23's report said "Nobody posted a price" over twelve real posts,
because `run_report` read `business.price.posted` and the world emits
`economy.price.posted`. Nothing failed: a missing event type is an empty
list, and an empty list is a confident sentence about a quiet town.

This is not a "the function name appears in the source" test. It builds
two sets from two different places - the types `run_report` READS, and
the types `world/` EMITS - and fails when the first is not contained in
the second. A rename on either side breaks it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WORLD = Path(__file__).resolve().parents[1]

# Read off the emitters: every `type="domain.thing.happened"` in world/,
# plus the LifeEvent/WorldEvent constructions that use the same keyword.
EMIT = re.compile(r'type=[\'"]([a-z_]+\.[a-z_]+\.[a-z_]+)[\'"]')
# Read off the report: the argument of every _events("...") call.
READ = re.compile(r'_events\(\s*[\'"]([a-z_]+\.[a-z_]+\.[a-z_]+)[\'"]\s*\)')


def _emitted() -> set[str]:
    found: set[str] = set()
    for path in WORLD.rglob("*.py"):
        if "/tests/" in str(path):
            continue
        found |= set(EMIT.findall(path.read_text(encoding="utf-8")))
    return found


def _read_by_report() -> set[str]:
    src = (WORLD / "run_report.py").read_text(encoding="utf-8")
    return set(READ.findall(src))


def test_every_event_the_report_reads_is_emitted_somewhere():
    read, emitted = _read_by_report(), _emitted()
    assert read, "the report reads no events at all - the regex or the file moved"
    orphans = sorted(read - emitted)
    assert not orphans, (
        "run_report reads event types nothing in world/ emits, so these "
        f"sections are false by construction: {orphans}. Either fix the name "
        "or emit the event."
    )


@pytest.mark.parametrize(
    "reader,emitter",
    [
        ("economy.price.posted", "world/tools/primitives/commerce.py"),
        ("business.wage.paid", "world/"),
    ],
)
def test_the_names_that_were_wrong_stay_right(reader, emitter):
    assert reader in _emitted(), f"{reader} is no longer emitted (was in {emitter})"
