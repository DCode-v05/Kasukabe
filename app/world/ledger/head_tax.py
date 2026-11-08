"""The basic head tax (DESIGN.md section 3: "Everything costs: living is
not free" / "Basic tax - you are taxed for existing").

"A per-capita levy debited every game-day to the treasury, counted per
head including children and the retired, owed whether or not anyone did
anything. Resting does not exempt you, and you owe it for every
dependent. The rate is government policy, amendable like any other - so
taxation is a live political question, and failure to pay is a matter
for the complaints desk and the courts."

This module is the sole owner of that mechanism. It deliberately does
NOT own three things a naive reading of "own the tax" might suggest it
should, because each already has a real owner elsewhere in this repo and
DESIGN.md's "never a parallel invention" line (section 6) applies just as
much to money as it does to justice:

- **The rate itself.** `world.charter.models.GovernmentPolicy.tax_rate_bps`
  is already the sitting government's one tax lever
  (`world.charter.government.GovernmentActions.set_tax_rate`, gated to a
  seat holder at the government complex, versioned and amendable exactly
  like every other policy field). This module only reads it
  (`read_tax_rate_bps`) and converts it to a sim amount
  (`head_tax_sim_per_head`); it never sets it and never imports charter's
  write surface.
- **Who counts as a head, and who the household's payer is.** Household
  membership (`world.life.household`), minority, and retirement are
  demographic facts life/ owns. Rather than import life/'s models and
  assume their shape (the exact failure mode this project's postmortems
  keep naming: two modules built against an assumed contract), this
  module takes `head_agent_ids` and `payer_agent_id` as plain strings the
  caller already resolved, the same "caller passes in the facts a fiscal
  rule needs" shape `world/ledger/core.py`'s own module docstring
  describes for pension age and salary. See the report accompanying this
  file for the exact one-line call life/'s daily-tick hook needs to make.
- **What happens to an unpaid debt.** `world.crime` already has a real,
  tested complaints desk, warrant, and sentencing pipeline. This module
  gets a delinquency into that pipeline
  (`file_head_tax_delinquency`, `CrimeKind.OTHER`, exactly the generic
  fallback crime/models.py already provides for a case that is not theft
  or assault) rather than inventing its own fine/jail logic.

Money mechanics live entirely inside `world.ledger.core.Ledger`: every
successful head's tax is one `Ledger.move()` call (`Currency.SIM`,
`TransactionReason.TAX`, `on_behalf_of=<the head being taxed for>`), so
it is a normal double-entry Transaction indistinguishable in the ledger
from any other tax, and it inherits `move()`'s own
`economy.wallet.debited` / `economy.wallet.credited` events for free.
Nothing here mutates a balance directly, and nothing here can make a
balance negative: `Ledger.apply_movement()` already refuses to debit more
than an account holds (`InsufficientFundsError`), which is the
enforcement mechanism behind this module's "balances may never go
negative" guarantee. This module's only new state is what happens on
that refusal: `HeadTaxDebtStore` records the shortfall as a debt owed to
the government instead of silently dropping it, one line per head per
day, mirroring `Ledger`'s own "mutable running balance plus immutable
event log" shape (`Account.usd_cents` mutated by an append-only
`Transaction` list) rather than inventing a different bookkeeping style.

Per-head attribution: `Ledger.Transaction.on_behalf_of` already exists
for exactly this (today it backs minor parent-debit,
`Ledger.resolve_usd_payer`'s docstring). This module charges one
`Ledger.move()` per head instead of one lump sum per household
specifically so the trace shows, for every head, whether that head's
portion was paid and by whom, or went to debt and for whom, DESIGN.md's
"the trace shows exactly who was taxed for whom" - not just the
household's total bill.

ASSUMPTION flagged inline where it lives: `HEAD_TAX_BASE_SIM`,
`world.life.constants`'s own module docstring already establishes the
DESIGN-PINNED-vs-ASSUMPTION convention this module follows for the same
kind of gap ("DESIGN.md describes the mechanic but not the number").
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.charter.store import CharterStore
from world.core.clock import timestamp_for
from world.crime.models import CaseFile, ComplaintRecord, CrimeKind
from world.ledger.core import EventSink, Ledger
from world.ledger.errors import InsufficientFundsError
from world.ledger.models import Currency, LedgerEvent, Transaction, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID

HEAD_TAX_BASE_SIM = 1_000
BPS_DENOMINATOR = 10_000

DEFAULT_TAX_COMPLAINANT_ID = TREASURY_ACCOUNT_ID


class HeadTaxError(Exception):
    """Base class for every exception this module raises."""


class InvalidTaxRateError(HeadTaxError):
    def __init__(self, tax_rate_bps: int) -> None:
        self.tax_rate_bps = tax_rate_bps
        super().__init__(
            f"tax_rate_bps={tax_rate_bps} must be within 0..{BPS_DENOMINATOR} "
            "(0%..100%), matching world.charter.models.GovernmentPolicy's own bound"
        )


class NoOutstandingDebtError(HeadTaxError):
    def __init__(self, debtor_agent_id: str) -> None:
        self.debtor_agent_id = debtor_agent_id
        super().__init__(f"agent {debtor_agent_id!r} owes no outstanding head tax debt")


class DebtOverpaymentError(HeadTaxError):
    def __init__(self, debtor_agent_id: str, amount_sim: int, outstanding_sim: int) -> None:
        self.debtor_agent_id = debtor_agent_id
        self.amount_sim = amount_sim
        self.outstanding_sim = outstanding_sim
        super().__init__(
            f"agent {debtor_agent_id!r} owes {outstanding_sim} sim in head tax debt, "
            f"cannot repay {amount_sim}"
        )


def head_tax_sim_per_head(tax_rate_bps: int) -> int:
    """Pure conversion from the government's tax_rate_bps policy lever to
    a flat sim-currency amount owed per head per game-day. Synchronous:
    no I/O, matches app/CONVENTIONS.md's "plain sync functions are fine
    for pure computation (ledger math ...)". tax_rate_bps=0 (genesis's
    default) charges 0, matching "no tax ... at the start."
    """
    if not (0 <= tax_rate_bps <= BPS_DENOMINATOR):
        raise InvalidTaxRateError(tax_rate_bps)
    return HEAD_TAX_BASE_SIM * tax_rate_bps // BPS_DENOMINATOR


async def read_tax_rate_bps(charter_store: CharterStore) -> int:
    """The current basic-tax rate, read through world.charter's own store
    (task instruction: "Read world/charter/ to find how policy values are
    stored and read them through that, but do NOT edit those files"). Not
    cached: every call reflects whatever GovernmentActions.set_tax_rate()
    (or a future charter amendment) most recently pushed, so a rate
    change by the sitting government takes effect on the very next call,
    with no separate propagation step this module needs to run.
    """
    policy = await charter_store.get_policy()
    return policy.tax_rate_bps


class HeadTaxDebtEventKind(str, Enum):
    ACCRUED = "accrued"
    REPAID = "repaid"


class HeadTaxDebtEvent(BaseModel):
    """One append-only line in a debtor's head-tax debt history.

    Mirrors `world.ledger.models.Transaction`'s own shape (an immutable
    row plus a mutable running balance the store keeps separately,
    `HeadTaxDebtStore.balances`) rather than modelling debt as a single
    mutable "amount owed" record, so the same per-head, per-day
    attribution the successful-payment path gets via `Transaction.
    on_behalf_of` survives on the unpaid side too: `head_agent_id` names
    exactly which dependent (or the payer itself) a given ACCRUED line was
    for. `head_agent_id` is None on a REPAID line: a repayment clears the
    debtor's running balance, not one specific day's shortfall.
    """

    event_id: str = Field(default_factory=lambda: f"head-tax-debt-{uuid.uuid4().hex[:12]}")
    kind: HeadTaxDebtEventKind
    debtor_agent_id: str
    head_agent_id: str | None = None
    household_id: str | None = None
    amount_sim: int
    tick: int
    game_day: int
    memo: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HeadTaxResult(BaseModel):
    """Everything one `charge_daily_head_tax()` call produced, for a
    caller that wants to log it, display it, or decide whether any of
    `new_debts` is worth `file_head_tax_delinquency()`-ing."""

    tick: int
    game_day: int
    payer_agent_id: str
    household_id: str | None
    sim_per_head: int
    heads_billed: list[str]
    paid: list[Transaction]
    new_debts: list[HeadTaxDebtEvent]
    total_paid_sim: int
    total_new_debt_sim: int


class HeadTaxView(BaseModel):
    """What one payer can see about basic tax right now (DESIGN.md
    section 3: "Agents must be able to see all of this ... today's
    upkeep and tax ... visible in context every tick and queryable
    before acting"). Discloses the rule (`tax_rate_bps`) and the numbers
    (`sim_per_head`, `amount_owed_today_sim`, `outstanding_debt_sim`),
    nothing about strategy, per that same section's "told the prices and
    the rules, never the strategy."
    """

    tax_rate_bps: int
    sim_per_head: int
    head_count: int
    amount_owed_today_sim: int
    outstanding_debt_sim: int


class HeadTaxDebtStore:
    """The per-agent running balance of unpaid basic head tax, plus the
    immutable event log it is derived from.

    One package-local `asyncio.Lock`, same rationale as
    `world.charter.store.CharterStore`'s own: up to 250 agents' daily tax
    can be charged inside one running world-server process, and
    read-then-write against `self.balances` must not interleave.

    `snapshot()`/`restore()` match `world.core.checkpoint.Checkpointable`
    structurally (no inheritance needed, exactly how that Protocol is
    meant to be satisfied) so a day-boundary checkpoint can cover
    outstanding tax debt the same way it already covers the clock,
    scheduler, and locations; see this module's accompanying report for
    the one-line `checkpoints.register(...)` call that plugs it in.
    """

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.balances: dict[str, int] = {}
        self.events: list[HeadTaxDebtEvent] = []

    async def accrue(
        self,
        *,
        debtor_agent_id: str,
        head_agent_id: str,
        amount_sim: int,
        tick: int,
        game_day: int,
        household_id: str | None = None,
        memo: str = "",
    ) -> HeadTaxDebtEvent:
        if amount_sim <= 0:
            raise ValueError(f"amount_sim must be positive, got {amount_sim}")
        async with self.lock:
            self.balances[debtor_agent_id] = self.balances.get(debtor_agent_id, 0) + amount_sim
            event = HeadTaxDebtEvent(
                kind=HeadTaxDebtEventKind.ACCRUED,
                debtor_agent_id=debtor_agent_id,
                head_agent_id=head_agent_id,
                household_id=household_id,
                amount_sim=amount_sim,
                tick=tick,
                game_day=game_day,
                memo=memo,
            )
            self.events.append(event)
            return event

    async def repay(
        self,
        *,
        debtor_agent_id: str,
        amount_sim: int,
        tick: int,
        game_day: int,
        memo: str = "",
    ) -> HeadTaxDebtEvent:
        if amount_sim <= 0:
            raise ValueError(f"amount_sim must be positive, got {amount_sim}")
        async with self.lock:
            outstanding = self.balances.get(debtor_agent_id, 0)
            if outstanding <= 0:
                raise NoOutstandingDebtError(debtor_agent_id)
            if amount_sim > outstanding:
                raise DebtOverpaymentError(debtor_agent_id, amount_sim, outstanding)
            self.balances[debtor_agent_id] = outstanding - amount_sim
            event = HeadTaxDebtEvent(
                kind=HeadTaxDebtEventKind.REPAID,
                debtor_agent_id=debtor_agent_id,
                head_agent_id=None,
                household_id=None,
                amount_sim=amount_sim,
                tick=tick,
                game_day=game_day,
                memo=memo,
            )
            self.events.append(event)
            return event

    async def outstanding(self, debtor_agent_id: str) -> int:
        async with self.lock:
            return self.balances.get(debtor_agent_id, 0)

    async def list_events(self, debtor_agent_id: str | None = None) -> list[HeadTaxDebtEvent]:
        async with self.lock:
            if debtor_agent_id is None:
                return list(self.events)
            return [e for e in self.events if e.debtor_agent_id == debtor_agent_id]

    async def all_outstanding(self) -> dict[str, int]:
        """Every debtor with a nonzero balance, for a courts/collections
        sweep that needs to know who is delinquent without checking each
        agent one at a time."""
        async with self.lock:
            return {agent_id: amount for agent_id, amount in self.balances.items() if amount > 0}


    def snapshot(self) -> dict[str, Any]:
        return {
            "balances": dict(self.balances),
            "events": [e.model_dump(mode="json") for e in self.events],
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.balances = dict(data["balances"])
        self.events = [HeadTaxDebtEvent.model_validate(e) for e in data["events"]]


async def charge_daily_head_tax(
    ledger: Ledger,
    debts: HeadTaxDebtStore,
    *,
    payer_agent_id: str,
    head_agent_ids: Sequence[str],
    tax_rate_bps: int,
    tick: int,
    household_id: str | None = None,
    event_sink: EventSink | None = None,
) -> HeadTaxResult:
    """Charge one household's basic head tax for the game-day `tick`
    falls in, DESIGN.md section 3's "per-capita levy debited every
    game-day ... counted per head including children and the retired."

    One `Ledger.move()` per head in `head_agent_ids` (which should
    include the payer's own id if the payer is themselves a head, "an
    agent who rests all day still owes it, and still owes it for each of
    its dependents"), charged in the order given, `payer_agent_id` ->
    treasury, `on_behalf_of` naming the head. A head whose portion the
    payer can no longer afford (`Ledger.apply_movement`'s own
    `InsufficientFundsError`, raised before any mutation for that head)
    accrues a `HeadTaxDebtEvent` instead: the household pays for as many
    heads as it can, in list order, and the rest become recorded debt,
    never a negative balance. `LedgerHaltedError` is not caught here and
    propagates: a halted ledger is a system-wide tripwire
    (app/CONVENTIONS.md), not a delinquent-taxpayer condition, and
    conflating the two would hide the former behind the latter.

    Call this once per household per completed game-day
    (`world.core.clock.is_day_boundary(tick)`), the same cadence
    `world/life/tick.py`'s own `run_daily_health_tick` already documents
    for its sibling per-agent daily hook. Idempotency across repeated
    calls for the same tick is the caller's responsibility (call it
    exactly once per household per day), the same way nothing in this
    package de-duplicates a repeated `pay_wage()` call either.
    """
    sim_per_head = head_tax_sim_per_head(tax_rate_bps)
    game_day = timestamp_for(tick).game_day
    heads_billed = list(head_agent_ids)

    paid: list[Transaction] = []
    new_debts: list[HeadTaxDebtEvent] = []

    if sim_per_head > 0:
        for head_id in heads_billed:
            on_behalf_of = head_id if head_id != payer_agent_id else None
            try:
                transaction = await ledger.move(
                    from_account_id=payer_agent_id,
                    to_account_id=TREASURY_ACCOUNT_ID,
                    amount=sim_per_head,
                    currency=Currency.SIM,
                    reason=TransactionReason.TAX,
                    tick=tick,
                    on_behalf_of=on_behalf_of,
                    memo=f"basic head tax, game day {game_day}, for {head_id}",
                )
                paid.append(transaction)
            except InsufficientFundsError:
                debt_event = await debts.accrue(
                    debtor_agent_id=payer_agent_id,
                    head_agent_id=head_id,
                    household_id=household_id,
                    amount_sim=sim_per_head,
                    tick=tick,
                    game_day=game_day,
                    memo=f"unpaid basic head tax, game day {game_day}, for {head_id}",
                )
                new_debts.append(debt_event)
                if event_sink is not None:
                    await event_sink(
                        LedgerEvent(
                            type="economy.head_tax_debt.accrued",
                            tick=tick,
                            payload={
                                "debtor_agent_id": payer_agent_id,
                                "head_agent_id": head_id,
                                "household_id": household_id,
                                "amount_sim": sim_per_head,
                                "game_day": game_day,
                            },
                        )
                    )

    return HeadTaxResult(
        tick=tick,
        game_day=game_day,
        payer_agent_id=payer_agent_id,
        household_id=household_id,
        sim_per_head=sim_per_head,
        heads_billed=heads_billed,
        paid=paid,
        new_debts=new_debts,
        total_paid_sim=sum(t.amount for t in paid),
        total_new_debt_sim=sum(d.amount_sim for d in new_debts),
    )


async def repay_head_tax_debt(
    ledger: Ledger,
    debts: HeadTaxDebtStore,
    *,
    debtor_agent_id: str,
    amount_sim: int,
    tick: int,
    event_sink: EventSink | None = None,
    memo: str = "",
) -> HeadTaxDebtEvent:
    """Pay down a debtor's outstanding head-tax debt by `amount_sim`,
    DESIGN.md section 3's obligation is owed "to the treasury," not
    written off, so a debt this module records must also be payable back
    through the same ledger.

    Validated against the recorded debt (`NoOutstandingDebtError`,
    `DebtOverpaymentError`) before any money moves; `Ledger.move()`'s own
    `InsufficientFundsError` still applies if the debtor cannot actually
    afford `amount_sim` right now. Either the money moves and the debt
    shrinks, or neither happens, matching `Ledger.settle_inheritance`'s
    own "either completes in full or leaves no partial trace" standard.
    """
    game_day = timestamp_for(tick).game_day
    outstanding = await debts.outstanding(debtor_agent_id)
    if outstanding <= 0:
        raise NoOutstandingDebtError(debtor_agent_id)
    if amount_sim > outstanding:
        raise DebtOverpaymentError(debtor_agent_id, amount_sim, outstanding)

    repayment_memo = memo or f"head tax debt repayment, game day {game_day}"
    await ledger.move(
        from_account_id=debtor_agent_id,
        to_account_id=TREASURY_ACCOUNT_ID,
        amount=amount_sim,
        currency=Currency.SIM,
        reason=TransactionReason.TAX,
        tick=tick,
        memo=repayment_memo,
    )
    event = await debts.repay(
        debtor_agent_id=debtor_agent_id,
        amount_sim=amount_sim,
        tick=tick,
        game_day=game_day,
        memo=repayment_memo,
    )
    if event_sink is not None:
        await event_sink(
            LedgerEvent(
                type="economy.head_tax_debt.repaid",
                tick=tick,
                payload={
                    "debtor_agent_id": debtor_agent_id,
                    "amount_sim": amount_sim,
                    "game_day": game_day,
                },
            )
        )
    return event


async def head_tax_view(
    debts: HeadTaxDebtStore,
    *,
    debtor_agent_id: str,
    head_agent_ids: Sequence[str],
    tax_rate_bps: int,
) -> HeadTaxView:
    """Read-only snapshot of what one payer can see about basic tax right
    now: the rate, today's per-head amount and household total, and their
    running debt. No side effects, safe to call as often as an agent's
    context needs it (DESIGN.md: "queryable before acting")."""
    sim_per_head = head_tax_sim_per_head(tax_rate_bps)
    outstanding = await debts.outstanding(debtor_agent_id)
    return HeadTaxView(
        tax_rate_bps=tax_rate_bps,
        sim_per_head=sim_per_head,
        head_count=len(head_agent_ids),
        amount_owed_today_sim=sim_per_head * len(head_agent_ids),
        outstanding_debt_sim=outstanding,
    )


class ComplaintDeskPort(Protocol):
    """The one crime/ method this module needs, shaped exactly like
    `world.crime.desk.ComplaintDesk.file_complaint` (structural, matching
    how `world.life.ports.EconomyPort` already keeps life/ decoupled from
    ledger's concrete class even though the concrete class exists). A
    real `ComplaintDesk` satisfies this with no adapter; tests can pass
    the real thing (see this module's accompanying test suite) instead of
    a hand-rolled fake, per this project's standing lesson against
    building against an assumed contract.
    """

    async def file_complaint(
        self,
        complainant_agent_id: str,
        kind: CrimeKind,
        description: str,
        location: str,
        now_tick: int,
        accused_agent_id: str | None = None,
        witness_event_id: str | None = None,
        case_id: str | None = None,
    ) -> tuple[ComplaintRecord, CaseFile]: ...


async def file_head_tax_delinquency(
    desk: ComplaintDeskPort,
    *,
    debtor_agent_id: str,
    head_agent_id: str,
    amount_sim: int,
    tick: int,
    complainant_agent_id: str = DEFAULT_TAX_COMPLAINANT_ID,
    location: str = LOCATION_GOVERNMENT_COMPLEX,
    case_id: str | None = None,
) -> tuple[ComplaintRecord, CaseFile]:
    """Opens (or attaches to) a real crime-desk case over an unpaid
    head-tax debt, DESIGN.md section 3: "failure to pay is a matter for
    the complaints desk and the courts." Uses `CrimeKind.OTHER`, the
    generic fallback `world.crime.models.CrimeKind` already provides;
    there is no dedicated tax-debt kind and adding one is out of this
    module's ownership (`world/crime/` is not this module's file).

    Not called automatically by `charge_daily_head_tax()`: filing a
    complaint every single day a balance stays nonzero would flood the
    desk with duplicates for one ongoing debt. The caller decides when a
    debt is worth a case (on first accrual, after N delinquent days,
    above some threshold, ...) and, for a debtor who already has an open
    case, should pass that case's `case_id` here to attach this filing
    to it instead of opening a second case for the same standing debt,
    exactly like a second witness's complaint attaches to an existing
    theft case (`ComplaintDesk.file_complaint`'s own docstring).
    """
    description = (
        f"unpaid basic head tax: {debtor_agent_id} owes {amount_sim} sim for {head_agent_id}"
    )
    return await desk.file_complaint(
        complainant_agent_id,
        CrimeKind.OTHER,
        description,
        location,
        tick,
        accused_agent_id=debtor_agent_id,
        case_id=case_id,
    )
