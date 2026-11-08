"""Failures grouped by which link in the chain actually broke.

Reads `llm_calls.failed_at`, the column added in Part C.1. Before it existed
this query was impossible: every failure looked the same from the database, so
a broker that could not reach world's ledger was indistinguishable from
Bedrock being down, and a run was spent looking at the wrong system.

Grouping by hop rather than by HTTP status is the whole point. The status is
what the caller was told; the hop is what a person has to go and fix.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Ordered by who you would wake up, not alphabetically.
KNOWN_HOPS = ("bedrock", "ledger", "world", "engine", "broker-internal")


@dataclass(frozen=True)
class HopFailures:
    hop: str
    failures: int
    agents: int
    example_error: str
    example_message: str


def failures_by_hop(conn: sqlite3.Connection, limit: int = 20) -> list[HopFailures]:
    rows = conn.execute(
        """
        SELECT COALESCE(failed_at, 'untagged')  AS hop,
               COUNT(*)                          AS failures,
               COUNT(DISTINCT agent_id)          AS agents,
               COALESCE(MAX(error_type), '')     AS example_error,
               COALESCE(MAX(error_message), '')  AS example_message
        FROM llm_calls
        WHERE success = 0
        GROUP BY hop
        ORDER BY failures DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [HopFailures(*row) for row in rows]


def untagged_share(conn: sqlite3.Connection) -> float:
    """What fraction of failures predate the hop column.

    Surfaced rather than hidden: a page reporting "0 bedrock failures" off a
    table whose rows are all untagged would be confidently wrong, and the
    reader has no way to know unless it says so.
    """
    row = conn.execute(
        "SELECT COUNT(*), SUM(failed_at IS NULL) FROM llm_calls WHERE success = 0"
    ).fetchone()
    total, untagged = (row[0] or 0), (row[1] or 0)
    return (untagged / total) if total else 0.0
