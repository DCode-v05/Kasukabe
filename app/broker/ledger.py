"""World's ledger, as seen from broker - wallet reads and debits only.

app/CONVENTIONS.md is explicit that only world/ (the ledger) and broker/
(burning money on inference) ever mutate a wallet, and that world/ owns
ALL world state including `transactions`. Broker therefore never touches
a wallet balance directly (in mock mode or otherwise) - every read and
every debit goes through this module's LedgerClient interface, which in
production is an HTTP client against world's API and in tests/mock mode
is an in-memory fake standing in for it.

world/ does not have an HTTP ledger API wired up yet (this repo's build
order, DESIGN.md section 12, is "world+tools -> broker+ledger -> ..." and
broker is being built against the world/ scaffold). world/ledger/core.py
DOES already exist as an in-process Ledger class, and its own docstrings
(resolve_usd_payer, burn_for_inference) confirm the exact shape this
module assumes: parent-debit resolution lives server-side, and
burn_for_inference's docstring literally says "broker/ is the real
caller of this in production... reached through world's HTTP API". What
does NOT exist yet is the FastAPI route wrapping burn_for_inference /
resolve_usd_payer / usd_balance for broker to call - HttpLedgerClient
below codifies the HTTP contract that route needs to expose. See the two
method docstrings for the exact request/response shapes; this is the
integration point world/'s owner needs to implement, flagged as an open
question in this module's build report.

Two ledger operations, matching the TASK spec's two-phase design:
  - get_payer_balance: pre-dispatch read. Resolves DESIGN.md section 4's
    "minors debit parents" server-side (world owns the family graph, not
    broker; world/ledger/core.py's resolve_usd_payer is exactly this),
    returning whichever wallet is the effective payer's.
  - debit: post-response write, for the EXACT metered cost of a call
    that has already happened. World's own Ledger enforces a strict
    non-negative balance floor (world/ledger/core.py's apply_movement
    raises when the payer can't cover the amount) - broker's pre-dispatch
    check (max_tokens as a worst-case output ceiling, see service.py)
    makes this the rare case, not the common one, but it is a real
    possible outcome of debit(), not a hypothetical: two of one payer's
    concurrent calls can race between their own pre-checks and their own
    debits. broker/service.py is the caller that decides what to do when
    debit() fails after the Bedrock spend already happened (it does NOT
    retry the refusal on the agent - the inference already occurred; see
    service.py's settle() for how it keeps that failure from discarding
    an already-good response).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from broker.errors import (
    InsufficientFundsError,
    LedgerHaltedError,
    LedgerUnavailableError,
    UnknownAgentError,
)


@dataclass(frozen=True)
class PayerBalance:
    agent_id: str
    payer_agent_id: str
    balance_cents: int
    is_minor: bool
    tick: "int | None" = None


@dataclass(frozen=True)
class DebitResult:
    payer_agent_id: str
    balance_cents_after: int


class LedgerClient(Protocol):
    async def get_payer_balance(
        self, agent_id: str, *, purpose: str = "", amount_cents: int = 0
    ) -> PayerBalance:
        """Resolve who actually pays for `agent_id`'s calls (themself, or
        - DESIGN.md section 4 - their parent if `agent_id` is a minor) and
        return that payer's current balance. Raises UnknownAgentError if
        world has no record of `agent_id`."""
        ...

    async def debit(
        self, agent_id: str, amount_cents: int, purpose: str, metadata: dict[str, object]
    ) -> DebitResult:
        """Debit the effective payer for `agent_id` by `amount_cents`
        (already resolved server-side exactly like get_payer_balance).
        CAN raise InsufficientFundsError - world's real Ledger enforces a
        non-negative balance floor (world/ledger/core.py's
        apply_movement) - see module docstring for why this is a rare
        race rather than the common case, and broker/service.py for how
        a failure here is handled without discarding an already-good
        inference response."""
        ...


class HttpLedgerClient:
    """Real implementation: calls world's HTTP API.

    ASSUMED CONTRACT (the FastAPI route wrapping world/ledger/core.py's
    Ledger does not exist yet - see module docstring):

      GET {world_base_url}/ledger/payer/{agent_id}
        200 -> {"agent_id": str, "payer_agent_id": str,
                "balance_cents": int, "is_minor": bool}
        404 -> unknown agent (world/ledger/errors.py's AccountNotFoundError)

      POST {world_base_url}/ledger/debit
        body: {"agent_id": str, "amount_cents": int, "purpose": str,
               "metadata": dict}
        200 -> {"payer_agent_id": str, "balance_cents_after": int}
        402 -> insufficient funds (world/ledger/errors.py's
               InsufficientFundsError from apply_movement's balance
               floor - a real, not hypothetical, outcome; see the
               `debit` docstring above)
        404 -> unknown agent
    """

    def __init__(
        self,
        world_base_url: str,
        timeout_s: float = 10.0,
        client: "httpx.AsyncClient | None" = None,
    ) -> None:
        self.client = client or httpx.AsyncClient(base_url=world_base_url, timeout=timeout_s)

    async def close(self) -> None:
        await self.client.aclose()

    async def get_payer_balance(
        self, agent_id: str, *, purpose: str = "", amount_cents: int = 0
    ) -> PayerBalance:
        params = {"purpose": purpose, "amount_cents": amount_cents} if purpose else None
        resp = await self.client.get(f"/ledger/payer/{agent_id}", params=params)
        if resp.status_code == 404:
            raise UnknownAgentError(f"agent {agent_id!r} is not known to world's ledger")
        _require_usable_ledger_response(resp, f"balance for {agent_id!r}")
        body = resp.json()
        return PayerBalance(
            agent_id=body["agent_id"],
            payer_agent_id=body["payer_agent_id"],
            balance_cents=body["balance_cents"],
            is_minor=body["is_minor"],
            tick=body.get("tick"),
        )

    async def debit(
        self, agent_id: str, amount_cents: int, purpose: str, metadata: dict[str, object]
    ) -> DebitResult:
        resp = await self.client.post(
            "/ledger/debit",
            json={
                "agent_id": agent_id,
                "amount_cents": amount_cents,
                "purpose": purpose,
                "metadata": metadata,
            },
        )
        if resp.status_code == 404:
            raise UnknownAgentError(f"agent {agent_id!r} is not known to world's ledger")
        if resp.status_code == 402:
            raise InsufficientFundsError(
                f"ledger debit of {amount_cents} cents for agent {agent_id!r} was rejected: "
                f"{resp.text}"
            )
        _require_usable_ledger_response(resp, f"debit of {amount_cents} cents for {agent_id!r}")
        body = resp.json()
        return DebitResult(
            payer_agent_id=body["payer_agent_id"],
            balance_cents_after=body["balance_cents_after"],
        )


@dataclass
class InMemoryAgentAccount:
    balance_cents: int
    is_minor: bool = False
    parent_agent_id: str | None = None


@dataclass
class InMemoryLedgerClient:
    """Test/mock-mode fake. Not a stub of convenience - it implements the
    real minor-debits-parent resolution DESIGN.md section 4 requires, so
    tests exercising that behavior (broker/tests/test_service.py) don't
    need a real world process running.

    One asyncio.Lock per payer serializes concurrent debits against the
    same wallet (see pricing.py's CostAccountant docstring for why this
    matters: two of one parent's children debiting concurrently is a
    real race, not a hypothetical one, once a household has more than
    one minor).
    """

    accounts: dict[str, InMemoryAgentAccount] = field(default_factory=dict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    autoseed_balance_cents: int | None = None

    def seed(
        self,
        agent_id: str,
        balance_cents: int,
        is_minor: bool = False,
        parent_agent_id: str | None = None,
    ) -> None:
        """Test setup helper: create or overwrite an account."""
        self.accounts[agent_id] = InMemoryAgentAccount(
            balance_cents=balance_cents, is_minor=is_minor, parent_agent_id=parent_agent_id
        )

    def ensure_account(self, agent_id: str) -> InMemoryAgentAccount:
        """The account, provisioning it first if autoseed is enabled.

        Raises UnknownAgentError when autoseed is off, which is the default
        and what every existing test expects.
        """
        account = self.accounts.get(agent_id)
        if account is None:
            if self.autoseed_balance_cents is None:
                raise UnknownAgentError(f"agent {agent_id!r} is not known to the ledger")
            account = InMemoryAgentAccount(balance_cents=self.autoseed_balance_cents)
            self.accounts[agent_id] = account
        return account

    def payer_id_for(self, agent_id: str) -> str:
        account = self.ensure_account(agent_id)
        if account.is_minor and account.parent_agent_id:
            return self.payer_id_for(account.parent_agent_id)
        return agent_id

    def lock_for(self, payer_agent_id: str) -> asyncio.Lock:
        lock = self.locks.get(payer_agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[payer_agent_id] = lock
        return lock

    async def get_payer_balance(
        self, agent_id: str, *, purpose: str = "", amount_cents: int = 0
    ) -> PayerBalance:
        account = self.ensure_account(agent_id)
        payer_id = self.payer_id_for(agent_id)
        payer_account = self.accounts[payer_id]
        return PayerBalance(
            agent_id=agent_id,
            payer_agent_id=payer_id,
            balance_cents=payer_account.balance_cents,
            is_minor=account.is_minor,
        )

    async def debit(
        self, agent_id: str, amount_cents: int, purpose: str, metadata: dict[str, object]
    ) -> DebitResult:
        self.ensure_account(agent_id)
        payer_id = self.payer_id_for(agent_id)
        async with self.lock_for(payer_id):
            payer_account = self.accounts[payer_id]
            if payer_account.balance_cents < amount_cents:
                raise InsufficientFundsError(
                    f"payer {payer_id!r} has {payer_account.balance_cents} cents, "
                    f"cannot cover a debit of {amount_cents} cents"
                )
            payer_account.balance_cents -= amount_cents
            return DebitResult(
                payer_agent_id=payer_id, balance_cents_after=payer_account.balance_cents
            )

def _require_usable_ledger_response(resp: httpx.Response, what: str) -> None:
    """`raise_for_status()`, but the failure keeps its identity.

    A bare httpx error here is invisible to everything downstream: it is not a
    BrokerError, so no handler recognises it, and it reaches the trace as a
    generic internal error that reads exactly like Bedrock falling over. This
    is the single edit that makes "the ledger is down" say so.
    """
    if resp.is_success:
        return
    if resp.status_code == 409:
        raise LedgerHaltedError(
            f"world's ledger has halted and refused {what}",
            detail={
                "hop": "ledger",
                "status": resp.status_code,
                "body": resp.text[:500],
            },
        )
    raise LedgerUnavailableError(
        f"world's ledger returned {resp.status_code} for {what}",
        detail={
            "hop": "ledger",
            "status_code": resp.status_code,
            "body": resp.text[:2000],
            "url": str(resp.request.url) if resp.request else None,
        },
    )
