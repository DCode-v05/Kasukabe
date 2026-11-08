"""Shared test fixtures for the crime/ test suite.

Fakes for every Protocol in `world.crime.ports`, following the same
pattern `world/life/tests/conftest.py`'s `InMemoryEconomy` and
`world/charter/tests/conftest.py`'s `EventRecorder` already established:
minimal, in-memory, deterministic implementations that satisfy the
Protocol structurally, used for tests only and never imported by
production code. app/CONVENTIONS.md: "Never call a real LLM API in a
test" - none of this needs an LLM, but the same spirit applies to the
ledger/health/charter/location seams this package doesn't own: no real
Bedrock, no real Supabase, no real sibling-package wiring in a unit test.
"""

from __future__ import annotations

from typing import Any

import pytest

from world.crime.models import InjurySeverity


class InsufficientFundsError(RuntimeError):
    """Raised by FakeEconomy when a transfer or fine can't be covered."""


class FakeEconomy:
    def __init__(self, sim_balances: dict[str, int] | None = None) -> None:
        self.sim_balances: dict[str, int] = dict(sim_balances or {})
        self.treasury_sim: int = 0
        self.transfers: list[tuple[str, str, int, int, str]] = []
        self.fines: list[tuple[str, int, int, str]] = []

    async def transfer_sim(self, from_agent_id: str, to_agent_id: str, amount: int, tick: int, memo: str = "") -> None:
        balance = self.sim_balances.get(from_agent_id, 0)
        if balance < amount:
            raise InsufficientFundsError(f"{from_agent_id} has {balance}, cannot send {amount}")
        self.sim_balances[from_agent_id] = balance - amount
        self.sim_balances[to_agent_id] = self.sim_balances.get(to_agent_id, 0) + amount
        self.transfers.append((from_agent_id, to_agent_id, amount, tick, memo))

    async def collect_fine(self, convict_agent_id: str, amount_sim: int, tick: int, memo: str = "") -> None:
        balance = self.sim_balances.get(convict_agent_id, 0)
        if balance < amount_sim:
            raise InsufficientFundsError(f"{convict_agent_id} has {balance}, cannot pay fine {amount_sim}")
        self.sim_balances[convict_agent_id] = balance - amount_sim
        self.treasury_sim += amount_sim
        self.fines.append((convict_agent_id, amount_sim, tick, memo))


class FakeHealth:
    def __init__(self) -> None:
        self.injuries: list[tuple[str, InjurySeverity, int]] = []

    async def apply_injury(self, agent_id: str, severity: InjurySeverity, tick: int) -> None:
        self.injuries.append((agent_id, severity, tick))


class FakeGovernment:
    def __init__(self, minister_ids: set[str] | None = None, police_ids: set[str] | None = None) -> None:
        self.minister_ids = set(minister_ids or set())
        self.police_ids = set(police_ids or set())

    async def is_minister(self, agent_id: str) -> bool:
        return agent_id in self.minister_ids

    async def is_police_officer(self, agent_id: str) -> bool:
        return agent_id in self.police_ids


class FakeLocation:
    def __init__(self, positions: dict[str, str] | None = None) -> None:
        self.positions: dict[str, str] = dict(positions or {})

    def location_of(self, agent_id: str) -> str | None:
        return self.positions.get(agent_id)

    def place(self, agent_id: str, location_id: str) -> None:
        self.positions[agent_id] = location_id


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def sink(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


@pytest.fixture
def economy() -> FakeEconomy:
    return FakeEconomy()


@pytest.fixture
def health() -> FakeHealth:
    return FakeHealth()


@pytest.fixture
def government() -> FakeGovernment:
    return FakeGovernment()


@pytest.fixture
def location() -> FakeLocation:
    return FakeLocation()


@pytest.fixture
def recorder() -> EventRecorder:
    return EventRecorder()
