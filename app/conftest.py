"""Repo-wide test hooks.

Two, and both exist for the same reason: a run that could not check
something must say so, rather than reporting a number that reads as
coverage.

`GROW-27` is the Ville tileset - see `ville_assets.py` for the gate and
why it lives in its own module.

`INFRA-30` is the general case. "534 passed" required three undocumented
preconditions: a `/data` directory, network access at first use for nine
research tests, and a checked-out `smallville/` for the geography chain.
None of those is a defect in the code under test; together they meant the
pass count was a claim about the host as much as about the repo, and
nobody could tell which parts of it were which.

So every skip is summarised at the end of a run, grouped by reason. A
suite that skipped a hundred tests and one that skipped none both print
"passed", and only one of them has been checked.
"""
from __future__ import annotations

import collections

from ville_assets import SKIPPED_FOR_ASSETS, VILLE_DIR

# Skips that say nothing about coverage - a parametrised case that does
# not apply, a platform branch that is genuinely irrelevant here. Matched
# on a fragment of the reason, and kept short on purpose: the point of the
# summary is that a reader sees what went unchecked, and a long ignore
# list is how that stops happening.
UNINTERESTING_SKIPS = (
    "this host has process groups",
)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    _report_missing_assets(terminalreporter)
    _report_what_was_skipped(terminalreporter)


def _report_missing_assets(terminalreporter) -> None:
    """GROW-27: the specific case, with the specific claims it cost."""
    if not SKIPPED_FOR_ASSETS:
        return
    terminalreporter.write_sep("=", "NOT PROVEN IN THIS RUN", yellow=True, bold=True)
    terminalreporter.write_line(
        f"The Ville tileset is not mounted at {VILLE_DIR}, so these went unchecked:"
    )
    for what in sorted(SKIPPED_FOR_ASSETS):
        terminalreporter.write_line(f"  - {what}")
    terminalreporter.write_line(
        "GROW-27: a green run does NOT mean the above hold. Mount the assets "
        "(see world/tools/bootstrap.py DEFAULT_VILLE_DIR) and run again."
    )


def _report_what_was_skipped(terminalreporter) -> None:
    """INFRA-30: everything else, grouped by why.

    pytest already prints skips with `-rs`, and nobody passes `-rs`. This
    is the same information in the place a reader actually looks - the
    bottom of the run - and grouped, so twelve skips for one missing
    prerequisite read as one prerequisite rather than twelve mysteries.
    """
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return

    by_reason: collections.Counter = collections.Counter()
    for report in skipped:
        reason = ""
        if isinstance(getattr(report, "longrepr", None), tuple) and len(report.longrepr) == 3:
            reason = str(report.longrepr[2])
        reason = reason.replace("Skipped: ", "").strip() or "no reason given"
        if any(fragment in reason for fragment in UNINTERESTING_SKIPS):
            continue
        by_reason[reason[:150]] += 1

    if not by_reason:
        return

    terminalreporter.write_sep("=", "SKIPPED, AND WHY", yellow=True)
    for reason, count in by_reason.most_common():
        terminalreporter.write_line(f"  {count:>4}  {reason}")
    terminalreporter.write_line(
        "INFRA-30: these are host preconditions, not results. The pass count "
        "above is a claim about this machine as much as about the code."
    )
