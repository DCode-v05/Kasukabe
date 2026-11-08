"""Process-wide spend governor.

TASK spec (broker-lanes track): "add the SPEND GOVERNOR: process-wide
hard USD cap (default 400.00) computed from real response usage never
estimates; warn at 80%; at 100% stop dispatch and signal the world to
checkpoint. Expose spend, burn-rate $/hr, and projected exhaustion over
the admin API." This mirrors DESIGN.md's operator-facing budget dial
($1000 total system budget, $500 of it genesis wallets) with a broker-
owned hard stop on the $400 inference sub-budget the TASK's HARD BUDGET
CAP line calls out directly.

"Computed from real response usage never estimates" is a real constraint
on WHICH number feeds this module, not a style preference:
broker/pricing.py has two cost functions -
`estimate_cost_cents_ceiling` (a worst-case guess used ONLY for the
pre-dispatch wallet refusal check, deliberately rounded up so it never
under-refuses) and `exact_cost_nano_dollars` (the exact integer cost of
tokens Bedrock actually reports having used). Only the second number ever
reaches `record_spend` below - see broker/service.py's settle(), the one
call site, which computes it from the SAME `input_tokens`/`output_tokens`
that already feed the ledger debit for this call.

A consequence of "real usage only": the cap can only be confirmed crossed
AFTER the call that crosses it has already been billed by Bedrock in real
AWS dollars - there is no way to refuse that specific call in advance
without either estimating (which the TASK spec explicitly rules out for
this governor) or refusing calls with room still under the cap (also
wrong). `should_block_dispatch()` therefore stops every call AFTER the
one that tipped the total over, which is the only version of "stop
dispatch at 100%" that a real-usage-only governor can implement.

This class is a pure state machine plus a snapshot generator - it never
emits an event or writes anywhere itself. broker/service.py owns that
(comparing the state returned by `record_spend` against the state before
the call, in exactly one place) because "signal the world to checkpoint"
needs to happen exactly once per threshold crossing, and putting that
one-shot bookkeeping here would duplicate state this module doesn't
otherwise need to track.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

NANO_PER_DOLLAR = 1_000_000_000

Clock = Callable[[], float]


class SpendState(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    HALTED = "halted"


logger = logging.getLogger("kasukabe.broker.spend")


@dataclass(frozen=True)
class SpendSnapshot:
    """Admin-API-shaped view of the governor's current state - TASK spec:
    "Expose spend, burn-rate $/hr, and projected exhaustion over the
    admin API." See broker/app.py's `GET /admin/spend`."""

    spent_usd: float
    cap_usd: float
    fraction_used: float
    state: SpendState
    burn_rate_usd_per_hr: float
    projected_exhaustion_s: "float | None"
    # INFRA-14. `spent_usd` is now the RUN's total, seeded from the trace
    # store at boot, so a restarted broker cannot spend the cap twice. That
    # makes "what has this process burned" a separate question, and an
    # operator watching a live run needs both: the first says how close the
    # cap is, the second says whether anything is happening right now.
    spent_since_boot_usd: float = 0.0


@dataclass
class SpendGovernor:
    cap_usd: float = 400.0
    warn_fraction: float = 0.8
    clock: Clock = field(default=time.monotonic)

    spent_nano_dollars: int = field(default=0, init=False)
    # INFRA-14: what THIS process has added, kept apart from the seeded
    # run total so the burn rate stays a rate and not a step function.
    spent_since_boot_nano_dollars: int = field(default=0, init=False)
    started_at: float = field(default=0.0, init=False)
    state: SpendState = field(default=SpendState.NORMAL, init=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    @property
    def cap_nano_dollars(self) -> int:
        return round(self.cap_usd * NANO_PER_DOLLAR)

    def should_block_dispatch(self) -> bool:
        """Checked by broker/service.py's prepare_call BEFORE the wallet
        check, before lane selection, before any Bedrock dispatch - the
        earliest possible point once the process-wide cap has tripped."""
        return self.state == SpendState.HALTED

    def record_spend(self, cost_nano_dollars: int) -> SpendState:
        """Fold one call's REAL cost into the running total and recompute
        state. Returns the state AFTER this call. A no-op (state
        unchanged, nothing added) for `cost_nano_dollars <= 0` - a
        rejected-before-dispatch call or a mid-stream failure with no
        usable partial usage costs nothing, and must not be able to
        somehow move the governor's state either."""
        if cost_nano_dollars <= 0:
            return self.state
        self.spent_nano_dollars += cost_nano_dollars
        self.spent_since_boot_nano_dollars += cost_nano_dollars
        self.state = self.compute_state()
        return self.state

    def compute_state(self) -> SpendState:
        cap = self.cap_nano_dollars
        if self.spent_nano_dollars >= cap:
            return SpendState.HALTED
        if self.spent_nano_dollars >= cap * self.warn_fraction:
            return SpendState.WARNING
        return SpendState.NORMAL

    def snapshot(self) -> SpendSnapshot:
        spent_usd = self.spent_nano_dollars / NANO_PER_DOLLAR
        since_boot_usd = self.spent_since_boot_nano_dollars / NANO_PER_DOLLAR
        elapsed_s = max(self.clock() - self.started_at, 1e-9)
        # Rate off THIS process's spend: seeding the run total into the
        # numerator would report an enormous burn rate in the first second
        # after every restart and project exhaustion immediately.
        burn_rate_usd_per_hr = since_boot_usd / elapsed_s * 3600.0
        remaining_usd = max(self.cap_usd - spent_usd, 0.0)
        projected_exhaustion_s: "float | None" = None
        if self.state != SpendState.HALTED and burn_rate_usd_per_hr > 0.0:
            projected_exhaustion_s = remaining_usd / (burn_rate_usd_per_hr / 3600.0)
        fraction_used = spent_usd / self.cap_usd if self.cap_usd > 0 else 1.0
        return SpendSnapshot(
            spent_usd=spent_usd,
            cap_usd=self.cap_usd,
            fraction_used=min(fraction_used, 1.0),
            state=self.state,
            burn_rate_usd_per_hr=burn_rate_usd_per_hr,
            spent_since_boot_usd=since_boot_usd,
            projected_exhaustion_s=projected_exhaustion_s,
        )


def spend_already_recorded(client: Any) -> int:
    """Nano-dollars this run has already spent, per the trace store.

    INFRA-14. The governor's counter starts at zero on every boot, so a
    broker restarted mid-run - a crash, an OOM, a deploy, a `docker compose
    restart` - forgot everything the run had spent and would spend the
    whole cap again. The cap is the only thing between a bug and an
    unbounded Bedrock bill, and it was being reset by the most ordinary
    event in an operator's day.

    Every nano-dollar is already in `llm_calls.cost_nano_dollars` on the
    store the service has just opened, so this is one query at boot.

    Returns 0 rather than raising. A broker that will not start because it
    cannot read a historical sum is worse than one that starts with a
    conservative zero and says so - and the runner's own checkpointed
    governor still covers the same line.
    """
    try:
        # `client.conn` is SqliteClient's own single connection (it holds
        # exactly one, deliberately - see its comment about two writers on
        # one WAL). Read-only and synchronous, at boot, before any request.
        row = client.conn.execute(
            "SELECT COALESCE(SUM(cost_nano_dollars), 0) FROM llm_calls"
        ).fetchone()
        return int(row[0] or 0)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("could not read prior spend; the cap starts from zero")
        return 0
