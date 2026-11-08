"""The seam between life/ and world's ledger.

app/CONVENTIONS.md is explicit that only world/'s ledger (and broker/,
for inference burns) may ever mutate a wallet, the treasury, or the
burned total, and that the $1000 USD invariant is checked "after every
transaction that moves money." The ledger implementation does not exist
yet (see the module-level docstring in world/app.py: it's a health-check
scaffold). Rather than let life/'s health, birth, and death mechanics
either block on that or reach into a ledger module that isn't there,
every place in this package that needs to move money calls through
EconomyPort instead.

Once world's ledger module exists, it implements this Protocol (no
inheritance needed, Protocol is structural) and life/'s callers pass the
real implementation in. Until then, tests pass a small in-memory fake
(see life/tests/conftest.py) that enforces the same integer-money rule
without needing a real ledger. This is not a permanent abstraction layer
for its own sake - it is a placeholder for a same-package dependency
that hasn't been written yet, and it should collapse to a direct call
into ledger.py once that module lands.
"""

from __future__ import annotations

from typing import Protocol


class InsufficientFundsError(RuntimeError):
    """Raised by an EconomyPort implementation when a debit can't be covered."""


class EconomyPort(Protocol):
    """What life/ needs from the ledger. Amounts are always integers."""

    async def debit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        """Remove sim-currency from an agent's wallet. Raises InsufficientFundsError."""
        ...

    async def credit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        """Add sim-currency to an agent's wallet."""
        ...

    async def credit_usd_cents(self, agent_id: str, amount_cents: int, reason: str) -> None:
        """Add USD cents to an agent's wallet (e.g. a revival buying $1 of runway)."""
        ...

    async def get_sim_balance(self, agent_id: str) -> int:
        """Current sim-currency balance, for callers that need to check before debiting."""
        ...


class GuardianshipPort(Protocol):
    """What life/ needs from the ledger about who PAYS for an agent.

    Deliberately separate from EconomyPort rather than another method on
    it: this moves no money, and every existing EconomyPort implementer
    (world.tools.economy_bridge.LedgerEconomyPort, life/tests' fake, and
    crime/'s own port) would have had to grow a method it has no use for.
    A caller that needs both passes both, which is the same
    "pass in whatever facts a module needs" idiom this module already
    exists to serve.
    """

    async def end_parent_billing(self, agent_id: str) -> None:
        """Stop billing this agent's own spends to a parent.

        Called once, when a minor crosses MINOR_AGE_LIMIT_YEARS (The 250
        section 26). Must be idempotent: a tick loop that re-sees the same
        transition may call it more than once.
        """
        ...
