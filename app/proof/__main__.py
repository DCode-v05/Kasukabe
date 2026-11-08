"""One command: `python -m proof`.

WHAT THE GATE ACTUALLY ASSERTS, and why it is not "everything is proven".

Fifty-four of the town's ninety-seven verbs have never been called. A gate
demanding all-green would be red on the day it was written, and a gate
that is red on day one teaches a team to pass `--no-verify`. The 46 Ville
skips and the seven Postgres skips in the existing suite are what that
looks like after a few months.

So the lock is a ratchet, not a target:

    nothing that was proven may stop being proven,
    and nothing may newly fail.

`UNPROVEN -> PROVEN` is progress and is recorded. `PROVEN -> UNPROVEN` is a
regression and fails the gate even though nothing threw. That second case
is the one this repository keeps being bitten by: a mechanism that quietly
stops being reachable while every unit test stays green.

  --update-baseline   accept the current state as the new lock
  --show failed       only the rows that failed
  --skip agility      the agility area boots a server per hazard; skip it
                      for a fast local loop
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from proof.verdicts import Findings, load_baseline, regressions

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"
REPO = HERE.parents[1]
REPORT = REPO / "docs" / "proof" / "findings.json"

AREAS = ("reach", "agility", "stories", "replay")


async def collect(skip: set[str]) -> Findings:
    findings = Findings()
    if "reach" not in skip:
        from proof import reach

        started = time.monotonic()
        await reach.audit(findings)
        print(f"  reach      {time.monotonic() - started:5.1f}s")
    if "agility" not in skip:
        from proof import agility

        started = time.monotonic()
        await agility.audit(findings)
        print(f"  agility    {time.monotonic() - started:5.1f}s")
    if "stories" not in skip:
        from proof import stories

        started = time.monotonic()
        await stories.audit(findings)
        print(f"  stories    {time.monotonic() - started:5.1f}s")
    if "replay" not in skip:
        from proof import replay

        started = time.monotonic()
        await replay.audit(findings)
        print(f"  replay     {time.monotonic() - started:5.1f}s")
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="proof", description=__doc__)
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--show", choices=("all", "unproven", "failed"), default="all")
    ap.add_argument("--skip", action="append", default=[], choices=AREAS)
    args = ap.parse_args(argv)

    print("proof: running")
    findings = asyncio.run(collect(set(args.skip)))
    print()
    print(findings.matrix(show=args.show))
    print(findings.summary())

    findings.to_json(REPORT)

    if args.update_baseline:
        findings.to_json(BASELINE)
        print(f"\nbaseline updated: {BASELINE}")
        return 0

    baseline = load_baseline(BASELINE)
    if not baseline:
        print(
            f"\nno baseline at {BASELINE}; run with --update-baseline to lock "
            "the current state. Exiting on failures only."
        )
        return findings.exit_code()

    went_backwards = regressions(findings, baseline)
    fresh_failures = [
        r for r in findings.failed if (r.area, r.item) not in baseline
    ]

    # The gate is a ratchet, not a target. A failure that is already in the
    # lock is a known, recorded debt - it is in `docs/proof/FINDINGS.md`
    # with a name, and holding the build red for it would only teach the
    # team to bypass the build. What must never pass is a row going
    # backwards, or a new failure appearing.
    for r in went_backwards:
        print(
            f"  REGRESSED {r.area}/{r.item}: "
            f"{baseline[(r.area, r.item)]} -> {r.verdict}  ({r.claim})"
        )
    for r in fresh_failures:
        print(f"  NEW FAILURE {r.area}/{r.item}: {r.claim}")

    known = len(findings.failed) - len(fresh_failures)
    if known:
        print(f"\n{known} known failure(s) held at the baseline.")
    gained = [
        r
        for r in findings.rows
        if (r.area, r.item) not in baseline and r.verdict == "proven"
    ]
    if gained:
        print(f"{len(gained)} newly proven since the baseline.")

    if went_backwards or fresh_failures:
        print(
            f"\nFAILED: {len(went_backwards)} regressed, "
            f"{len(fresh_failures)} new."
        )
        return 1
    print("\nOK: nothing regressed, nothing newly failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
