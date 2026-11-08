"""The three words this instrument is allowed to say, and the row it says
them in.

PROVEN, UNPROVEN, FAILED. The middle one is the one that matters and the
one most instruments do not have. `run_checklist.py` already knows this -
it answers `no data` rather than passing - and that habit is the single
most valuable thing in the project's evidence culture. This module makes
it the default rather than a special case.

  PROVEN   the claim happened, and here is the row it happened in.
  UNPROVEN no evidence either way. The mechanism may be perfect. Nothing
           in the corpus exercised it, so no score is given.
  FAILED   the claim was exercised and did not hold.

Only FAILED is an error. UNPROVEN is a question, and a run that answers a
question is worth buying; a gate that turns questions into green ticks is
not.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PROVEN = "proven"
UNPROVEN = "unproven"
FAILED = "failed"

_ORDER = {FAILED: 0, UNPROVEN: 1, PROVEN: 2}
_MARK = {PROVEN: "+", UNPROVEN: "?", FAILED: "!"}


@dataclass(frozen=True)
class Verdict:
    """One judged claim.

    `evidence` is not decoration. A PROVEN row without a row reference is
    an assertion, and this package does not make assertions - it cites.
    An UNPROVEN row says where it looked, so the next reader knows what
    corpus would settle it.
    """

    area: str
    item: str
    claim: str
    verdict: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in _ORDER:
            raise ValueError(
                f"{self.verdict!r} is not one of {sorted(_ORDER)} - the "
                "instrument may not invent a fourth word to soften a result"
            )

    @property
    def mark(self) -> str:
        return _MARK[self.verdict]


@dataclass
class Findings:
    """Every verdict from one run of the instrument."""

    rows: list[Verdict] = field(default_factory=list)

    def add(self, *rows: Verdict) -> None:
        self.rows.extend(rows)

    def of(self, area: str) -> list[Verdict]:
        return [r for r in self.rows if r.area == area]

    @property
    def areas(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.rows:
            seen.setdefault(r.area, None)
        return list(seen)

    def tally(self, area: str | None = None) -> Counter[str]:
        rows = self.rows if area is None else self.of(area)
        return Counter(r.verdict for r in rows)

    @property
    def failed(self) -> list[Verdict]:
        return [r for r in self.rows if r.verdict == FAILED]

    @property
    def unproven(self) -> list[Verdict]:
        return [r for r in self.rows if r.verdict == UNPROVEN]

    def exit_code(self) -> int:
        """FAILED is an error. UNPROVEN is not.

        A gate that failed on UNPROVEN would be unrunnable on day one and
        would teach the team to silence it, which is how the 46 Ville
        skips and the seven Postgres skips became invisible.
        """
        return 1 if self.failed else 0


    def matrix(self, *, show: str = "all") -> str:
        """The human-readable table. `show` is 'all', 'unproven' or
        'failed' - the last two are what a reader wants after the first
        time."""
        keep = {
            "all": {PROVEN, UNPROVEN, FAILED},
            "unproven": {UNPROVEN, FAILED},
            "failed": {FAILED},
        }[show]
        out: list[str] = []
        for area in self.areas:
            rows = [r for r in self.of(area) if r.verdict in keep]
            counts = self.tally(area)
            head = (
                f"{area}  "
                f"{counts[PROVEN]} proven, {counts[UNPROVEN]} unproven, "
                f"{counts[FAILED]} failed"
            )
            out.append(head)
            out.append("-" * len(head))
            if not rows:
                out.append("  (nothing to show at this filter)")
            width = max((len(r.item) for r in rows), default=0)
            for r in sorted(rows, key=lambda r: (_ORDER[r.verdict], r.item)):
                out.append(f"  {r.mark} {r.item:<{width}}  {r.claim}")
                if r.evidence:
                    out.append(f"    {' ' * width}  {r.evidence}")
            out.append("")
        return "\n".join(out)

    def summary(self) -> str:
        c = self.tally()
        return (
            f"{len(self.rows)} claims judged: "
            f"{c[PROVEN]} proven, {c[UNPROVEN]} unproven, {c[FAILED]} failed"
        )

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "area": r.area,
                        "item": r.item,
                        "claim": r.claim,
                        "verdict": r.verdict,
                        "evidence": r.evidence,
                    }
                    for r in self.rows
                ],
                indent=2,
            ),
            encoding="utf-8",
        )


def load_baseline(path: Path) -> dict[tuple[str, str], str]:
    """A locked baseline: `(area, item) -> verdict` as it stood when the
    gate was last accepted.

    The gate's job is not "everything is proven" - that day is far off.
    It is "nothing that was proven has stopped being proven", which is
    the only bar a growing system can actually hold.
    """
    if not path.exists():
        return {}
    return {
        (r["area"], r["item"]): r["verdict"]
        for r in json.loads(path.read_text(encoding="utf-8"))
    }


def regressions(
    findings: Findings, baseline: dict[tuple[str, str], str]
) -> list[Verdict]:
    """Rows that went backwards against the lock."""
    worse: list[Verdict] = []
    for r in findings.rows:
        was = baseline.get((r.area, r.item))
        if was is not None and _ORDER[r.verdict] < _ORDER[was]:
            worse.append(r)
    return worse
