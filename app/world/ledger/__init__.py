"""World ledger package: the dual-currency double-entry accounting core.

DESIGN.md section 3 fixes the whole simulation's real-money footprint at
exactly $1000.00 (100_000 cents): $500 genesis across 250 agent wallets
plus a $500 government treasury. Every dollar that ever exists starts in
one of those two pools and, once spent on inference, moves to a third
pool (burned) and never leaves it. app/CONVENTIONS.md calls this the
ledger invariant and treats it as a law, not a guideline: checked after
every USD-moving transaction, and a violation halts the whole simulation
rather than logging a warning, because a silently-wrong ledger is worse
than a stopped one.

Sim-currency is the separate, non-conserved in-world economy currency
(wages, prices, taxes, business fees). It is not part of the $1000
invariant, blessings mint it from nothing (DESIGN.md section 3), but
every sim movement is still recorded double-entry for auditability.

This package is pure accounting: it owns Account and Transaction state
and the operations that move money between accounts (genesis, exchange,
tax, wage, pension, business registration, inheritance, inference burn,
parent-debit for minors). It does not know about ticks beyond the
integer it is handed, agents' full demographic state, or Supabase;
callers (world's tick loop, world's HTTP API, broker's burn calls) pass
in the facts (tick number, agent age, last salary) a fiscal rule needs,
and this module enforces the arithmetic and the invariant.
"""

from __future__ import annotations

from world.ledger.core import Ledger
from world.ledger.errors import (
    AccountClosedError,
    AccountNotFoundError,
    CurrencyNotSupportedError,
    DuplicateAccountError,
    InsufficientFundsError,
    InvalidAmountError,
    LedgerError,
    LedgerHaltedError,
    LedgerInvariantViolation,
    ParentNotConfiguredError,
    PensionIneligibleError,
    SameAccountTransferError,
)
from world.ledger.models import (
    Account,
    AccountKind,
    Currency,
    LedgerEvent,
    Transaction,
    TransactionReason,
)
from world.ledger.money import (
    BURNED_ACCOUNT_ID,
    CENTS_PER_USD,
    GENESIS_AGENT_COUNT,
    GENESIS_AGENT_USD_CENTS,
    GENESIS_TREASURY_USD_CENTS,
    MINT_ACCOUNT_ID,
    PENSION_GOVERNMENT_PCT,
    PENSION_MINIMUM_AGE,
    PENSION_PRIVATE_PCT,
    SIM_PER_USD,
    SIM_PER_USD_CENT,
    TOTAL_USD_CENTS,
    TREASURY_ACCOUNT_ID,
    sim_to_usd_cents,
)

__all__ = [
    "Ledger",
    "Account",
    "AccountKind",
    "Currency",
    "LedgerEvent",
    "Transaction",
    "TransactionReason",
    "LedgerError",
    "AccountClosedError",
    "AccountNotFoundError",
    "CurrencyNotSupportedError",
    "DuplicateAccountError",
    "InsufficientFundsError",
    "InvalidAmountError",
    "LedgerHaltedError",
    "LedgerInvariantViolation",
    "ParentNotConfiguredError",
    "PensionIneligibleError",
    "SameAccountTransferError",
    "BURNED_ACCOUNT_ID",
    "CENTS_PER_USD",
    "GENESIS_AGENT_COUNT",
    "GENESIS_AGENT_USD_CENTS",
    "GENESIS_TREASURY_USD_CENTS",
    "MINT_ACCOUNT_ID",
    "PENSION_GOVERNMENT_PCT",
    "PENSION_MINIMUM_AGE",
    "PENSION_PRIVATE_PCT",
    "SIM_PER_USD",
    "SIM_PER_USD_CENT",
    "TOTAL_USD_CENTS",
    "TREASURY_ACCOUNT_ID",
    "sim_to_usd_cents",
]
