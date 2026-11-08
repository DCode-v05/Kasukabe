"""Is this run believable at all?

First panel on the page, and first module a reader should trust. A run at 39%
dispatch success is not a finding about society - it is a broken instrument,
and every conclusion drawn from it is worthless. That has to be visible before
anything else on the page is read, not discovered afterwards.

Everything here is a ratio with a stated denominator. A bare "412 failures" is
unreadable; "412 of 5,900, 7%" is a judgement anyone can make.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class RunHealth:
    calls: int
    successes: int
    failures: int
    spend_usd: float
    agents_seen: int
    dormant_agents: int
    engines_alive: int
    engines_expected: int

    @property
    def dispatch_success(self) -> float:
        return (self.successes / self.calls) if self.calls else 0.0

    @property
    def believable(self) -> bool:
        """The threshold below which the page should stop reporting findings
        and start reporting that it is broken.

        0.85 rather than something stricter: real runs lose calls to genuine
        Bedrock throttling and that is not a broken instrument. What 39% meant
        was a stale process holding port 8100, and nothing that severe sits
        anywhere near this line.
        """
        return self.dispatch_success >= 0.85

    @property
    def verdict(self) -> str:
        if not self.calls:
            return "no calls yet - nothing to judge"
        if self.believable:
            return "believable - read on"
        return (
            f"NOT believable at {self.dispatch_success:.0%} dispatch success. "
            "Fix the instrument before reading anything else on this page."
        )


def read_health(
    conn: sqlite3.Connection, *, engines_alive: int = 0, engines_expected: int = 0,
    dormant_agents: int = 0,
) -> RunHealth:
    row = conn.execute(
        """
        SELECT COUNT(*),
               COALESCE(SUM(success), 0),
               COALESCE(SUM(cost_nano_dollars), 0),
               COUNT(DISTINCT agent_id)
        FROM llm_calls
        """
    ).fetchone()
    calls, successes, cost_nano, agents = row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0
    return RunHealth(
        calls=calls,
        successes=successes,
        failures=calls - successes,
        spend_usd=cost_nano / 1_000_000_000,
        agents_seen=agents,
        dormant_agents=dormant_agents,
        engines_alive=engines_alive,
        engines_expected=engines_expected,
    )
