"""Ledger exceptions.

LedgerInvariantViolation is the tripwire (app/CONVENTIONS.md, "The
ledger invariant"): raising it is how the ledger halts the simulation.
Every other exception here is an ordinary rejected-operation error, the
caller's request was invalid or unaffordable, nothing is wrong with the
ledger itself, and the ledger stays open for the next call.
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every exception this package raises."""


class AccountNotFoundError(LedgerError):
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"no ledger account for {account_id!r}")


class DuplicateAccountError(LedgerError):
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"ledger account {account_id!r} already exists")


class AccountClosedError(LedgerError):
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"ledger account {account_id!r} is closed (estate already settled)")


class InsufficientFundsError(LedgerError):
    def __init__(self, account_id: str, requested: int, available: int, currency: str) -> None:
        self.account_id = account_id
        self.requested = requested
        self.available = available
        self.currency = currency
        super().__init__(
            f"account {account_id!r} has {available} {currency} available, "
            f"cannot cover {requested} {currency}"
        )


class InvalidAmountError(LedgerError):
    def __init__(self, amount: int) -> None:
        self.amount = amount
        super().__init__(f"transaction amount must be a positive integer, got {amount}")


class SameAccountTransferError(LedgerError):
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"cannot move money from account {account_id!r} to itself")


class CurrencyNotSupportedError(LedgerError):
    def __init__(self, account_id: str, kind_label: str, currency_label: str) -> None:
        self.account_id = account_id
        self.kind_label = kind_label
        self.currency_label = currency_label
        super().__init__(
            f"account {account_id!r} (kind={kind_label}) does not support {currency_label}"
        )


class ParentNotConfiguredError(LedgerError):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(
            f"agent {agent_id!r} is a minor with no parent_id on file, cannot resolve "
            f"who pays for a USD spend (DESIGN.md section 4: 'broker auto-debits the "
            f"PARENT'S wallet for a minor's calls')"
        )


class PensionIneligibleError(LedgerError):
    def __init__(self, agent_id: str, age: int, minimum_age: int) -> None:
        self.agent_id = agent_id
        self.age = age
        self.minimum_age = minimum_age
        super().__init__(
            f"agent {agent_id!r} is age {age}, pensions start at age {minimum_age} "
            f"(DESIGN.md section 4)"
        )


class LedgerHaltedError(LedgerError):
    """Raised by every public operation once the ledger has halted.

    A halted ledger accepts no further mutation. The only way out is
    operator intervention outside this package (inspect the transaction
    log, fix whatever produced the violation, and rebuild the Ledger);
    this package deliberately offers no un-halt method.
    """

    def __init__(self, reason: str | None) -> None:
        self.reason = reason
        super().__init__(
            "ledger is halted after an invariant violation and accepts no further "
            f"operations: {reason or 'no reason recorded'}"
        )


class LedgerInvariantViolation(LedgerError):
    """The tripwire. Raising this halts the ledger (app/CONVENTIONS.md).

    Carries the expected and actual totals so the halt event, and
    whoever investigates it, can see exactly how far off the books are
    without re-deriving the numbers from the transaction log.
    """

    def __init__(self, expected_cents: int, actual_cents: int, detail: str = "") -> None:
        self.expected_cents = expected_cents
        self.actual_cents = actual_cents
        self.detail = detail
        diff = actual_cents - expected_cents
        message = (
            f"USD invariant violated: expected {expected_cents} cents "
            f"(wallets + treasury + burned), got {actual_cents} cents (diff {diff:+d})"
        )
        if detail:
            message = f"{message}. {detail}"
        super().__init__(message)
