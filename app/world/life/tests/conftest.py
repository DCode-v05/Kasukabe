"""Shared fixtures for life/ tests.

InMemoryEconomy is a minimal EconomyPort implementation (ports.py) for
tests only - it is not exported from world.life and must never be
imported by production code. The real implementation belongs to world's
ledger module (see ports.py's module docstring); this fake exists so
health.py/death.py/birth.py/household.py can be tested without one,
per app/CONVENTIONS.md's "never call a real LLM API in a test" and this
task's "stub/mock inference" instruction extended to the ledger seam
that doesn't exist yet either.
"""

from __future__ import annotations

import pytest

from world.life.ports import InsufficientFundsError


class InMemoryGuardianship:
    """A minimal GuardianshipPort (ports.py) for tests. Records who stopped
    being billed to a parent, and is idempotent the way the real one is."""

    def __init__(self) -> None:
        self.ended: list[str] = []

    async def end_parent_billing(self, agent_id: str) -> None:
        if agent_id not in self.ended:
            self.ended.append(agent_id)


class InMemoryEconomy:
    def __init__(self, sim_balances: dict[str, int] | None = None) -> None:
        self.sim_balances: dict[str, int] = dict(sim_balances or {})
        self.usd_cents_balances: dict[str, int] = {}
        self.debits: list[tuple[str, int, str]] = []
        self.credits: list[tuple[str, int, str]] = []
        self.usd_credits: list[tuple[str, int, str]] = []

    async def debit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        balance = self.sim_balances.get(agent_id, 0)
        if balance < amount:
            raise InsufficientFundsError(
                f"{agent_id} has {balance} sim, cannot debit {amount} for {reason!r}"
            )
        self.sim_balances[agent_id] = balance - amount
        self.debits.append((agent_id, amount, reason))

    async def credit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        self.sim_balances[agent_id] = self.sim_balances.get(agent_id, 0) + amount
        self.credits.append((agent_id, amount, reason))

    async def credit_usd_cents(self, agent_id: str, amount_cents: int, reason: str) -> None:
        self.usd_cents_balances[agent_id] = self.usd_cents_balances.get(agent_id, 0) + amount_cents
        self.usd_credits.append((agent_id, amount_cents, reason))

    async def get_sim_balance(self, agent_id: str) -> int:
        return self.sim_balances.get(agent_id, 0)


@pytest.fixture
def economy() -> InMemoryEconomy:
    return InMemoryEconomy()


@pytest.fixture
def guardianship() -> InMemoryGuardianship:
    return InMemoryGuardianship()
