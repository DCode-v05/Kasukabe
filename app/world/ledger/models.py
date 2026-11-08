"""Ledger data model: accounts and transactions.

Account is internal, mutable ledger state, a plain dataclass, per
app/CONVENTIONS.md ("internal-only data can be a plain dataclass").
Transaction and LedgerEvent are pydantic models: every Transaction
becomes a row in Supabase's `transactions` table and every LedgerEvent
becomes an `economy.*` event on the `kasukabe:economy` Realtime channel
(app/CONVENTIONS.md, "Event naming scheme") once world's HTTP/Supabase
seam is wired up, so both cross a module boundary even though this
package does not talk to Supabase directly yet.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Currency(str, enum.Enum):
    USD = "usd"
    SIM = "sim"


class AccountKind(str, enum.Enum):
    AGENT = "agent"
    TREASURY = "treasury"
    BURNED = "burned"
    BUSINESS = "business"


class TransactionReason(str, enum.Enum):
    GENESIS = "genesis"
    EXCHANGE = "exchange"
    TAX = "tax"
    BUSINESS_REGISTRATION_FEE = "business_registration_fee"
    WAGE = "wage"
    PENSION = "pension"
    INHERITANCE = "inheritance"
    INFERENCE_BURN = "inference_burn"
    BLESSING = "blessing"
    TRANSFER = "transfer"
    # A two-sided swap between two residents standing together, distinct
    # from TRANSFER (money with nothing coming back) and from a shop's
    # buy/sell. Its own reason so a settlement - a price agreed in speech
    # and then actually paid - is queryable in the trace, which is the
    # clearest evidence there is that talking changed the world.
    TRADE = "trade"
    # Money across a shop counter, in each direction. Both used to be
    # TRANSFER, which maps to the same schema value as `transfer` itself -
    # the tool for giving money away for nothing - so the record could not
    # tell a purchase from a handout. Measured on final1: two successful
    # `sell` calls and a trace holding only `burn` and `transfer`.
    #
    # This is the defect that already cost this project six invisible
    # trades. Same argument as TRADE above: a bargain settled at the
    # bakery counter is the clearest evidence there is that talking
    # changed the world, and it cannot be counted while it looks identical
    # to somebody handing over money for nothing.
    PURCHASE = "purchase"
    SALE = "sale"
    # An owner moving money between their own hand and their own till
    # (TOOL-03). Its own reason for the same argument PURCHASE/SALE make
    # above: a shopkeeper banking the day's takings and a shopkeeper
    # handing a stranger money for nothing are not the same act, and a
    # trace that files both as TRANSFER cannot tell a business that
    # trades from one that is being emptied.
    TILL = "till"
    # GOV-15. A fine was booked as TAX, so `treasury_view.
    # tax_collected_sim` counted every penalty the courts levied as tax
    # revenue and no query could separate what the town RAISED from what
    # it PUNISHED. The trace schema already has a "fine" tx_type; only the
    # ledger's own vocabulary was missing it.
    FINE = "fine"
    # ECON-03. Seven kinds of money movement that were all filed as
    # TRANSFER with a memo, so the trace could not tell a BILL from a
    # HANDOUT.
    #
    # The consequence was not theoretical. `build_transaction_row` writes
    # `reason = transaction.reason.value`, and `run_report._transactions`
    # queries `where reason = 'daily_upkeep'` - a string no row ever held,
    # because upkeep was booked as `transfer`. So the report asked for the
    # upkeep and got nothing back, and run 23 printed "no upkeep charged"
    # over ninety sim per household per day of real money.
    #
    # `EXCHANGE` already exists for the sim/USD swap itself; ENERGY_EXCHANGE
    # is the resident-facing `buy_energy` act that wraps one, which the
    # research counts separately because it is a decision rather than a
    # mechanism.
    UPKEEP = "daily_upkeep"
    ACTION_COST = "action_cost"
    REFUND = "refund"
    FEE = "fee"
    DONATION = "donation"
    REVIVAL = "revival"
    ENERGY_EXCHANGE = "energy_exchange"


@dataclass
class Account:
    """Mutable ledger state for one account.

    kind decides which balances are meaningful: AGENT, TREASURY, and
    BURNED carry both usd_cents and sim_units; BUSINESS carries only
    sim_units, DESIGN.md section 3 scopes USD to agents, the treasury,
    and the burned sink, a business never holds inference budget.
    Ledger.apply_movement() enforces this at the operation level rather
    than the type level, so a violation names the offending account
    instead of failing a generic type check.

    is_minor and parent_id back the parent-debit rule (DESIGN.md
    section 4): a minor's USD spends resolve to the parent's account,
    see Ledger.resolve_usd_payer().
    """

    account_id: str
    kind: AccountKind
    usd_cents: int = 0
    sim_units: int = 0
    is_minor: bool = False
    parent_id: str | None = None
    closed: bool = False


class Transaction(BaseModel):
    """One double-entry movement: an amount of one currency from one
    account to another.

    app/CONVENTIONS.md: "Every movement is a Transaction row with tick,
    reason, counterparties." from_account and to_account are the two
    counterparties. There is no multi-way transaction row, an operation
    that touches more than two accounts (an inheritance split across
    several heirs, an exchange that moves both currencies) emits one
    Transaction per single-currency, two-party leg instead of trying to
    represent a fan-out in one row.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tick: int
    reason: TransactionReason
    currency: Currency
    amount: int
    from_account: str
    to_account: str
    # Set when a debit was resolved away from the named agent (parent-debit
    # for a minor's USD spend): from_account is who actually paid,
    # on_behalf_of is whose activity caused the spend.
    on_behalf_of: str | None = None
    memo: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LedgerEvent(BaseModel):
    """One `economy.*` event, shaped for world's Realtime channel.

    type follows app/CONVENTIONS.md's <domain>.<subject>.<past_tense_verb>
    scheme (e.g. "economy.wallet.debited", "economy.ledger.violated") and
    is always published on the "kasukabe:economy" channel once world
    wires this to Supabase Realtime.
    """

    type: str
    tick: int
    payload: dict[str, Any]
