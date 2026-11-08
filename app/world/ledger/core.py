"""The ledger core: accounts, transactions, and the $1000 invariant.

Every public method that moves money funnels through move() (async) and
its synchronous inner primitive apply_movement(): the single place that
mutates a balance and appends the Transaction. That is deliberate,
app/CONVENTIONS.md requires the invariant check "after every transaction
that moves money", and the surest way to honor "every" is to make it
structurally impossible to move money any other way. move() itself
awaits the invariant check for USD legs and emits the wallet-debited /
wallet-credited events, so a caller cannot forget either step.

Public methods are async because this is the shape world's in-process
API takes (app/CONVENTIONS.md, "How modules talk"): the eventual
Supabase write of each Transaction and LedgerEvent is real I/O, even
though the arithmetic and the invariant check themselves are
synchronous, pure computation, deliberately factored into their own sync
helpers (apply_movement, check_invariant, usd_total) so they are
directly unit-testable without an event loop, matching app/CONVENTIONS's
"plain sync functions are fine for pure computation (ledger math...)".

This package does not know about ticks beyond the integer it is handed,
agents' full demographic state, or Supabase. Callers (world's tick loop,
world's HTTP API, broker's burn calls reaching in through that HTTP
API) pass in the facts a fiscal rule needs (tick number, agent age, last
salary, employment type) and this module enforces the arithmetic and the
invariant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

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
    GENESIS_AGENT_USD_CENTS,
    GENESIS_TREASURY_USD_CENTS,
    MINT_ACCOUNT_ID,
    PENSION_GOVERNMENT_PCT,
    PENSION_MINIMUM_AGE,
    PENSION_PRIVATE_PCT,
    TOTAL_USD_CENTS,
    TREASURY_ACCOUNT_ID,
    sim_to_usd_cents,
)

import os

EventSink = Callable[[LedgerEvent], Awaitable[None]]

# The purposes the broker routes to its background lanes
# (broker/lanes.py's BACKGROUND_PURPOSE_PREFIXES): the town's own
# bookkeeping over a morning it has already paid for, as opposed to a
# resident deciding what to do next.
HOUSEKEEPING_PURPOSES = ("learning_centre", "summarizer", "importance_scoring", "dream_probe")

# Who pays for that bookkeeping when the resident's wallet is empty.
# "treasury_fallback" (default) charges the town's reserve rather than
# losing the consolidation; "resident" restores the refusals the
# 50-resident run recorded 58 of. Thinking is never affected either way.
HOUSEKEEPING_PAYER = os.environ.get("KASUKABE_HOUSEKEEPING_PAYER", "treasury_fallback").strip().lower()

# Accounts whose usd_cents participates in the $1000 invariant. Business
# accounts are sim-only by design (see Account's docstring in models.py)
# and are deliberately excluded.
USD_CAPABLE_KINDS = frozenset({AccountKind.AGENT, AccountKind.TREASURY, AccountKind.BURNED})


class Ledger:
    """In-memory dual-currency double-entry ledger for one simulation run."""

    def __init__(self, event_sink: EventSink | None = None) -> None:
        # Treasury and burned always exist, even before genesis funds
        # them, so every other operation can assume they are present.
        self.accounts: dict[str, Account] = {
            TREASURY_ACCOUNT_ID: Account(TREASURY_ACCOUNT_ID, AccountKind.TREASURY),
            BURNED_ACCOUNT_ID: Account(BURNED_ACCOUNT_ID, AccountKind.BURNED),
        }
        self.transactions: list[Transaction] = []
        self.event_sink = event_sink
        self.halted = False
        self.halt_reason: str | None = None
        # The invariant total is whatever genesis() actually seeded, not a
        # hardcoded $1000. money.py's constants describe the DEFAULT run
        # (250 agents x $2 + $500 treasury); a smaller cohort - say 50
        # agents at $1 for a short experiment - is still a perfectly valid
        # closed economy, and hardcoding the total would halt the ledger on
        # its very first check for no reason other than the population
        # being different. genesis() overwrites this with the real sum.
        self.expected_total_usd_cents = TOTAL_USD_CENTS

    # -- account lookup and creation --------------------------------

    def get_account(self, account_id: str) -> Account:
        account = self.accounts.get(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return account

    def usd_balance(self, account_id: str) -> int:
        return self.get_account(account_id).usd_cents

    def sim_balance(self, account_id: str) -> int:
        return self.get_account(account_id).sim_units

    def open_agent(
        self,
        agent_id: str,
        *,
        starting_usd_cents: int = 0,
        starting_sim: int = 0,
        is_minor: bool = False,
        parent_id: str | None = None,
    ) -> Account:
        """Register a new agent account.

        Synchronous: creating an account moves no money, so there is
        nothing to check against the invariant yet (genesis() funds new
        agent accounts and checks once at the end, see genesis()).
        """
        if agent_id in self.accounts:
            raise DuplicateAccountError(agent_id)
        if is_minor and not parent_id:
            raise ParentNotConfiguredError(agent_id)
        account = Account(
            agent_id,
            AccountKind.AGENT,
            usd_cents=starting_usd_cents,
            sim_units=starting_sim,
            is_minor=is_minor,
            parent_id=parent_id,
        )
        self.accounts[agent_id] = account
        return account

    def end_parent_billing(self, agent_id: str) -> Account:
        """Stop routing an agent's USD spends to their parent.

        DESIGN.md section 4's parent-debit rule ("broker auto-debits the
        PARENT'S wallet for a minor's calls") is scoped to minors, and
        `resolve_usd_payer` reads exactly the two flags this clears. Until
        this existed nothing in the simulation ever cleared them: an
        account opened with `is_minor=True` kept billing its parent for
        the rest of the run, so a former minor who had aged past
        MINOR_AGE_LIMIT_YEARS years ago was still, financially, a child.
        `world.life.aging.advance_age` clears the life record's
        `sponsor_parent_id` at the same moment; this is the wallet half of
        that same transition (The 250 section 26).

        Synchronous and idempotent: it moves no money, so there is nothing
        to check against the $1000 invariant, and calling it on an
        already-independent account is a no-op rather than an error - a
        tick loop that re-sees the same birthday must not blow up.
        """
        account = self.get_account(agent_id)
        if account.kind is not AccountKind.AGENT:
            raise CurrencyNotSupportedError(agent_id, account.kind.value, "guardianship")
        account.is_minor = False
        account.parent_id = None
        return account

    def open_business(self, business_id: str, owner_agent_id: str) -> Account:
        """Register a business's sim-only account.

        DESIGN.md section 3: businesses are contestable and registered
        with government; the fee itself is register_business()'s job
        (it calls this after pre-validating the fee is affordable, see
        that method's docstring for why the ordering matters).
        """
        if business_id in self.accounts:
            raise DuplicateAccountError(business_id)
        owner = self.get_account(owner_agent_id)
        if owner.closed:
            raise AccountClosedError(owner_agent_id)
        account = Account(business_id, AccountKind.BUSINESS)
        self.accounts[business_id] = account
        return account

    # -- genesis ------------------------------------------------------

    async def genesis(
        self,
        agent_ids: list[str],
        *,
        agent_usd_cents: int = GENESIS_AGENT_USD_CENTS,
        treasury_usd_cents: int = GENESIS_TREASURY_USD_CENTS,
        tick: int = 0,
    ) -> None:
        """Seed genesis balances and check the $1000 invariant once.

        Genesis is exempt from the per-step invariant check every other
        operation gets: there is no valid intermediate state while
        agent wallets are still being funded one at a time, checking
        after each one would trip the tripwire on a config that is
        actually about to balance correctly. Instead every account is
        funded first, a GENESIS Transaction is recorded per funded
        account for the audit trail (from_account is the mint label,
        genesis money is not minted from nothing in the accounting
        sense, MINT_ACCOUNT_ID here only marks "this is where the
        system's fixed $1000 enters the books", see money.py), and the
        invariant is checked exactly once at the end. A misconfigured
        genesis still halts, just at boot instead of mid-fund, matching
        the engine's own "hard-fail boot, nothing partial ever serves"
        convention (engine/src/config.py).
        """
        if self.halted:
            raise LedgerHaltedError(self.halt_reason)

        treasury = self.accounts[TREASURY_ACCOUNT_ID]
        treasury.usd_cents += treasury_usd_cents
        self.transactions.append(
            Transaction(
                tick=tick,
                reason=TransactionReason.GENESIS,
                currency=Currency.USD,
                amount=treasury_usd_cents,
                from_account=MINT_ACCOUNT_ID,
                to_account=TREASURY_ACCOUNT_ID,
                memo="genesis treasury funding",
            )
        )

        for agent_id in agent_ids:
            self.open_agent(agent_id, starting_usd_cents=agent_usd_cents)
            self.transactions.append(
                Transaction(
                    tick=tick,
                    reason=TransactionReason.GENESIS,
                    currency=Currency.USD,
                    amount=agent_usd_cents,
                    from_account=MINT_ACCOUNT_ID,
                    to_account=agent_id,
                    memo="genesis agent wallet",
                )
            )

        # The closed economy is exactly what was just minted: treasury plus
        # every wallet. Recording it here is what lets a 50-agent-at-$1 run
        # hold its own invariant as strictly as the default 250-at-$2 one,
        # instead of being measured against a population it does not have.
        self.expected_total_usd_cents = treasury_usd_cents + agent_usd_cents * len(agent_ids)

        await self.enforce_invariant(tick)

    # -- the invariant --------------------------------------------------

    def usd_total(self) -> int:
        """Sum of every USD-capable account's usd_cents: agent wallets
        plus treasury plus burned. Pure and synchronous, see module
        docstring for why."""
        total = self.accounts[TREASURY_ACCOUNT_ID].usd_cents
        total += self.accounts[BURNED_ACCOUNT_ID].usd_cents
        total += sum(
            account.usd_cents
            for account in self.accounts.values()
            if account.kind is AccountKind.AGENT
        )
        return total

    def check_invariant(self) -> None:
        """Raise LedgerInvariantViolation and halt the ledger if wallets
        + treasury + burned != $1000.00. Kept separate from event
        emission (I/O) so it is directly unit-testable without an event
        loop, see module docstring."""
        total = self.usd_total()
        expected = self.expected_total_usd_cents
        if total != expected:
            self.halted = True
            self.halt_reason = (
                f"USD invariant violated: expected {expected} cents, got {total} cents"
            )
            raise LedgerInvariantViolation(expected, total)

    async def enforce_invariant(self, tick: int) -> None:
        """Check the invariant and, on violation, emit the
        economy.ledger.violated tripwire event (app/CONVENTIONS.md,
        "Event naming scheme") before propagating the exception. Every
        USD-moving public method calls this, via move(), immediately
        after apply_movement().
        """
        try:
            self.check_invariant()
        except LedgerInvariantViolation as exc:
            await self.emit(
                LedgerEvent(
                    type="economy.ledger.violated",
                    tick=tick,
                    payload={
                        "expected_cents": exc.expected_cents,
                        "actual_cents": exc.actual_cents,
                        "diff_cents": exc.actual_cents - exc.expected_cents,
                    },
                )
            )
            raise

    async def emit(self, event: LedgerEvent) -> None:
        if self.event_sink is not None:
            await self.event_sink(event)

    # -- the one mutation primitive --------------------------------------

    def apply_movement(
        self,
        *,
        from_account_id: str,
        to_account_id: str,
        amount: int,
        currency: Currency,
        reason: TransactionReason,
        tick: int,
        on_behalf_of: str | None = None,
        memo: str = "",
    ) -> Transaction:
        """Move `amount` of `currency` from one account to another and
        record it. Synchronous: pure bookkeeping, no I/O. Every balance
        mutation in this package goes through this method (see module
        docstring); callers that move USD must follow up with
        enforce_invariant(), which is why every public operation calls
        move() instead of this directly.
        """
        if self.halted:
            raise LedgerHaltedError(self.halt_reason)
        if amount <= 0:
            raise InvalidAmountError(amount)
        if from_account_id == to_account_id:
            raise SameAccountTransferError(from_account_id)
        if currency is Currency.USD and from_account_id == MINT_ACCOUNT_ID:
            # Genesis is the one place USD enters the books, and it
            # writes directly to account balances instead of going
            # through this method (see genesis()'s docstring). A move()
            # call reaching here with MINT_ACCOUNT_ID and USD is a bug:
            # DESIGN.md section 3 fixes the USD total at genesis, only
            # sim currency is minted at runtime (baby blessings).
            raise LedgerError(
                "USD cannot be minted outside genesis, MINT_ACCOUNT_ID is a sim-only "
                "faucet (DESIGN.md section 3: only sim blessings are minted at "
                "runtime, USD is fixed for the life of the run)"
            )

        source: Account | None = None
        if from_account_id != MINT_ACCOUNT_ID:
            source = self.get_account(from_account_id)
            if source.closed:
                raise AccountClosedError(from_account_id)

        destination = self.get_account(to_account_id)
        if destination.closed:
            raise AccountClosedError(to_account_id)

        if currency is Currency.USD:
            if source is not None and source.kind not in USD_CAPABLE_KINDS:
                raise CurrencyNotSupportedError(source.account_id, source.kind.value, "usd")
            if destination.kind not in USD_CAPABLE_KINDS:
                raise CurrencyNotSupportedError(
                    destination.account_id, destination.kind.value, "usd"
                )
            if source is not None:
                if source.usd_cents < amount:
                    raise InsufficientFundsError(
                        source.account_id, amount, source.usd_cents, "usd_cents"
                    )
                source.usd_cents -= amount
            destination.usd_cents += amount
        else:
            if source is not None:
                if source.sim_units < amount:
                    raise InsufficientFundsError(
                        source.account_id, amount, source.sim_units, "sim_units"
                    )
                source.sim_units -= amount
            destination.sim_units += amount

        transaction = Transaction(
            tick=tick,
            reason=reason,
            currency=currency,
            amount=amount,
            from_account=from_account_id,
            to_account=to_account_id,
            on_behalf_of=on_behalf_of,
            memo=memo,
        )
        self.transactions.append(transaction)
        return transaction

    async def move(
        self,
        *,
        from_account_id: str,
        to_account_id: str,
        amount: int,
        currency: Currency,
        reason: TransactionReason,
        tick: int,
        on_behalf_of: str | None = None,
        memo: str = "",
    ) -> Transaction:
        """The one async entry point every money-moving operation below
        funnels through: mutate and record (apply_movement), enforce
        the invariant for USD legs, then emit the wallet events. Doing
        all three here, instead of leaving the invariant check to each
        caller, means a future operation cannot forget it.
        """
        transaction = self.apply_movement(
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount=amount,
            currency=currency,
            reason=reason,
            tick=tick,
            on_behalf_of=on_behalf_of,
            memo=memo,
        )
        if currency is Currency.USD:
            await self.enforce_invariant(tick)
        await self.emit_movement_events(transaction)
        return transaction

    async def emit_movement_events(self, transaction: Transaction) -> None:
        base_payload = {
            "transaction_id": transaction.id,
            "reason": transaction.reason.value,
            "currency": transaction.currency.value,
            "amount": transaction.amount,
        }
        if transaction.from_account != MINT_ACCOUNT_ID:
            await self.emit(
                LedgerEvent(
                    type="economy.wallet.debited",
                    tick=transaction.tick,
                    payload={**base_payload, "account_id": transaction.from_account},
                )
            )
        await self.emit(
            LedgerEvent(
                type="economy.wallet.credited",
                tick=transaction.tick,
                payload={**base_payload, "account_id": transaction.to_account},
            )
        )

    # -- parent-debit resolution -----------------------------------------

    def resolve_usd_payer(self, agent_id: str) -> str:
        """DESIGN.md section 4: "broker auto-debits the PARENT'S wallet
        for a minor's calls." Returns the account id that actually pays
        a USD spend attributed to agent_id: the agent itself unless it
        is a minor with a parent on file, in which case the parent.
        """
        account = self.get_account(agent_id)
        if account.kind is not AccountKind.AGENT or not account.is_minor:
            return agent_id
        if not account.parent_id:
            raise ParentNotConfiguredError(agent_id)
        return account.parent_id

    async def debit_agent_usd(
        self,
        agent_id: str,
        to_account_id: str,
        amount: int,
        tick: int,
        *,
        reason: TransactionReason = TransactionReason.TRANSFER,
        memo: str = "",
    ) -> Transaction:
        """Any USD spend attributed to an agent, resolved through
        parent-debit. burn_for_inference() is the specialised
        inference-cost case of this; call this one directly for any
        other minor-attributable USD spend a caller defines later.
        """
        payer_id = self.resolve_usd_payer(agent_id)
        on_behalf_of = agent_id if payer_id != agent_id else None
        return await self.move(
            from_account_id=payer_id,
            to_account_id=to_account_id,
            amount=amount,
            currency=Currency.USD,
            reason=reason,
            tick=tick,
            on_behalf_of=on_behalf_of,
            memo=memo,
        )

    # -- exchange --------------------------------------------------------

    async def exchange_sim_for_usd(
        self, agent_id: str, sim_amount: int, tick: int, *, memo: str = ""
    ) -> tuple[Transaction, Transaction]:
        """DESIGN.md section 3: 10,000 sim -> $1 API credit (re-rated
        2026-08-01 from the original 100,000 sim figure), bought at
        the government store (a tool call, in person, location-gating is
        the tool registry's job, not this method's). Sim leaves the
        agent's own wallet, minors spend their own sim, parent-debit is
        a USD-only rule, and lands in the treasury's sim balance; USD
        leaves the treasury and lands in the agent's USD wallet.

        Both legs are validated before either one mutates anything, so
        an exchange either completes in full or leaves no partial trace
        (an agent short on sim, or a treasury too USD-poor to pay out,
        fails cleanly instead of debiting sim with no USD to show for
        it).
        """
        usd_cents = sim_to_usd_cents(sim_amount)
        if self.halted:
            raise LedgerHaltedError(self.halt_reason)

        agent = self.get_account(agent_id)
        if agent.kind is not AccountKind.AGENT:
            raise CurrencyNotSupportedError(agent_id, agent.kind.value, "sim exchange")
        if agent.closed:
            raise AccountClosedError(agent_id)
        if agent.sim_units < sim_amount:
            raise InsufficientFundsError(agent_id, sim_amount, agent.sim_units, "sim_units")

        treasury = self.accounts[TREASURY_ACCOUNT_ID]
        if treasury.usd_cents < usd_cents:
            raise InsufficientFundsError(
                TREASURY_ACCOUNT_ID, usd_cents, treasury.usd_cents, "usd_cents"
            )

        sim_leg = self.apply_movement(
            from_account_id=agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=sim_amount,
            currency=Currency.SIM,
            reason=TransactionReason.EXCHANGE,
            tick=tick,
            memo=memo or f"exchanged for {usd_cents} usd_cents at the government store",
        )
        usd_leg = self.apply_movement(
            from_account_id=TREASURY_ACCOUNT_ID,
            to_account_id=agent_id,
            amount=usd_cents,
            currency=Currency.USD,
            reason=TransactionReason.EXCHANGE,
            tick=tick,
            memo=memo or f"exchanged {sim_amount} sim at the government store",
        )
        await self.enforce_invariant(tick)
        await self.emit_movement_events(sim_leg)
        await self.emit_movement_events(usd_leg)
        return sim_leg, usd_leg

    # -- inference burn ----------------------------------------------------

    def _housekeeping_payer(self, agent_id: str, usd_cents: int, memo: str) -> "str | None":
        """The treasury, when this is housekeeping and the resident is broke.

        The 50-resident run failed 58 calls this way: the working-memory
        flush and the consolidation batch were billed to wallets that were
        already empty, so the residents whose morning had been most
        eventful were exactly the ones who lost it. Consolidation runs over
        a morning the town has already paid for - refusing it saves nothing
        and discards what the money bought.

        Thinking is untouched. A resident at $0 still cannot take a turn;
        that is cognitive poverty and it is the design.
        """
        if HOUSEKEEPING_PAYER != "treasury_fallback":
            return None
        lowered = (memo or "").strip().lower()
        if lowered.startswith("auto:"):
            lowered = lowered[len("auto:"):]
        if not lowered.startswith(HOUSEKEEPING_PURPOSES):
            return None
        try:
            payer = self.resolve_usd_payer(agent_id)
        except LedgerError:
            return None
        if self.usd_balance(payer) >= usd_cents:
            return None
        return TREASURY_ACCOUNT_ID

    def payer_for(self, agent_id: str, usd_cents: int, purpose: str = "") -> str:
        """Who pays for this call - the ONE answer both checks ask.

        The broker asks before a model call (`/ledger/payer`) and debits
        after it (`/ledger/debit` -> `burn_for_inference`). Until this
        existed only the second knew about the treasury fallback, and the
        first refused a broke resident's learning call on the resident's
        own balance - so the 50-resident run's 58 refusals, all recorded at
        0 tokens and $0, would have happened exactly as before.
        """
        return self._housekeeping_payer(agent_id, usd_cents, purpose) or self.resolve_usd_payer(agent_id)

    async def burn_for_inference(
        self, agent_id: str, usd_cents: int, tick: int, *, memo: str = ""
    ) -> Transaction:
        """The 'burned' leg of the invariant (app/CONVENTIONS.md: "a
        burn from an inference call" is one of the four transaction
        types the invariant is checked after). Resolves parent-debit
        first (DESIGN.md section 4): a minor's inference cost is
        charged to the parent's USD wallet, not the minor's own.
        broker/ is the real caller of this in production
        (app/CONVENTIONS.md: "Only world/ ... and broker/ ... ever
        mutate a wallet, the treasury, or the burned total"), reached
        through world's HTTP API since broker never imports world
        directly and world never calls broker (app/CONVENTIONS.md,
        "How modules talk").
        """
        treasury = self._housekeeping_payer(agent_id, usd_cents, memo)
        if treasury is not None:
            # Treasury to burned is the same leg of the invariant as wallet
            # to burned, so the $1,000 identity is untouched.
            return await self.move(
                from_account_id=treasury,
                to_account_id=BURNED_ACCOUNT_ID,
                amount=usd_cents,
                currency=Currency.USD,
                reason=TransactionReason.INFERENCE_BURN,
                tick=tick,
                on_behalf_of=agent_id,
                memo=f"{memo or 'housekeeping'} (treasury: {agent_id} could not pay)",
            )
        return await self.debit_agent_usd(
            agent_id,
            BURNED_ACCOUNT_ID,
            usd_cents,
            tick,
            reason=TransactionReason.INFERENCE_BURN,
            memo=memo or f"inference cost for {agent_id}",
        )

    # -- baby blessings / sim minting ---------------------------------------

    async def mint_sim(
        self,
        agent_id: str,
        amount: int,
        tick: int,
        *,
        reason: TransactionReason = TransactionReason.BLESSING,
        memo: str = "",
    ) -> Transaction:
        """Create sim-currency from nothing and credit an agent.
        DESIGN.md section 3: "Baby blessing: 5,000 sim to god" (re-rated
        2026-08-01 from the original 50,000 sim figure), the god
        console mints sim directly. This is the one legitimate sim
        source with no matching debit anywhere, which is why it is not
        part of the $1000 USD invariant (that invariant is USD-only by
        design, see money.py's TOTAL_USD_CENTS) and why MINT_ACCOUNT_ID
        has no backing Account (see money.py).
        """
        return await self.move(
            from_account_id=MINT_ACCOUNT_ID,
            to_account_id=agent_id,
            amount=amount,
            currency=Currency.SIM,
            reason=reason,
            tick=tick,
            memo=memo,
        )

    # -- wages, taxes, business fees, pensions -------------------------------

    async def pay_wage(
        self,
        employer_account_id: str,
        employee_agent_id: str,
        sim_amount: int,
        tick: int,
        *,
        memo: str = "",
    ) -> Transaction:
        """DESIGN.md section 4: government and businesses pay wages in
        sim-currency. Pass TREASURY_ACCOUNT_ID for a government job or a
        business account id (opened via open_business /
        register_business) for a private job.
        """
        return await self.move(
            from_account_id=employer_account_id,
            to_account_id=employee_agent_id,
            amount=sim_amount,
            currency=Currency.SIM,
            reason=TransactionReason.WAGE,
            tick=tick,
            memo=memo,
        )

    async def collect_tax(
        self, agent_id: str, sim_amount: int, tick: int, *, memo: str = ""
    ) -> Transaction:
        """DESIGN.md section 3: taxes are sim-currency, paid to treasury."""
        return await self.move(
            from_account_id=agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=sim_amount,
            currency=Currency.SIM,
            reason=TransactionReason.TAX,
            tick=tick,
            memo=memo,
        )

    async def register_business(
        self,
        business_id: str,
        owner_agent_id: str,
        fee_sim: int,
        tick: int,
        *,
        memo: str = "",
    ) -> Transaction:
        """DESIGN.md section 3: businesses register with government for
        a fee. Everything that could fail (owner missing or closed, fee
        unaffordable, business_id already taken) is checked before the
        business account is created, so a rejected registration leaves
        no half-registered business behind, unlike creating the account
        first and finding out the fee bounces.
        """
        if self.halted:
            raise LedgerHaltedError(self.halt_reason)
        if fee_sim <= 0:
            raise InvalidAmountError(fee_sim)
        if business_id in self.accounts:
            raise DuplicateAccountError(business_id)
        owner = self.get_account(owner_agent_id)
        if owner.closed:
            raise AccountClosedError(owner_agent_id)
        if owner.sim_units < fee_sim:
            raise InsufficientFundsError(owner_agent_id, fee_sim, owner.sim_units, "sim_units")

        self.open_business(business_id, owner_agent_id)
        return await self.move(
            from_account_id=owner_agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=fee_sim,
            currency=Currency.SIM,
            reason=TransactionReason.BUSINESS_REGISTRATION_FEE,
            tick=tick,
            memo=memo,
        )

    async def pay_pension(
        self,
        agent_id: str,
        agent_age: int,
        last_salary_sim: int,
        employment_type: Literal["private", "government"],
        tick: int,
        *,
        memo: str = "",
        pension_pct: int | None = None,
        minimum_age: int | None = None,
    ) -> Transaction:
        """DESIGN.md section 4: pensions from treasury at 70+, 25% of
        last salary for a private-sector retiree, 50% for a government
        one. Age and employment history are the caller's facts, world's
        health/aging system owns agent demographics, not this package,
        this method only enforces the fiscal rule and moves the money.

        GOV-09: those three numbers are also GOVERNMENT POLICY
        (`pension_private_pct`, `pension_government_pct`,
        `pension_eligibility_age`), and this method read module constants
        instead - so a town that voted retirement down to 65 or the
        private rate up to 40% changed numbers no payment ever consulted.
        `pension_pct` and `minimum_age` let the caller hand in what the
        charter currently says. They default to the constants, because
        `ledger/` must not import `charter/` (that direction is the
        import boundary charter/models.py's docstring sets out) and a
        ledger with no government still has to be able to pay a pension.
        """
        floor = PENSION_MINIMUM_AGE if minimum_age is None else minimum_age
        if agent_age < floor:
            raise PensionIneligibleError(agent_id, agent_age, floor)
        if pension_pct is not None:
            pct = pension_pct
        else:
            pct = (
                PENSION_GOVERNMENT_PCT
                if employment_type == "government"
                else PENSION_PRIVATE_PCT
            )
        pension_sim = last_salary_sim * pct // 100
        return await self.move(
            from_account_id=TREASURY_ACCOUNT_ID,
            to_account_id=agent_id,
            amount=pension_sim,
            currency=Currency.SIM,
            reason=TransactionReason.PENSION,
            tick=tick,
            memo=memo or f"{pct}% pension of last salary {last_salary_sim}",
        )

    # -- inheritance -------------------------------------------------------

    async def settle_inheritance(
        self, deceased_agent_id: str, heirs: list[str], tick: int, *, memo: str = ""
    ) -> list[Transaction]:
        """DESIGN.md section 5: "estates become inheritance." Splits the
        deceased's USD and sim balances evenly across heirs; an
        indivisible remainder (balance % len(heirs) != 0) escheats to
        treasury rather than being handed unevenly to whichever heir
        happens to be listed first, and an empty heirs list escheats the
        whole estate to treasury. All heir accounts are validated up
        front so a bad heir id fails before any money moves, instead of
        leaving the estate partially distributed.
        """
        if self.halted:
            raise LedgerHaltedError(self.halt_reason)
        deceased = self.get_account(deceased_agent_id)
        if deceased.kind is not AccountKind.AGENT:
            raise CurrencyNotSupportedError(deceased_agent_id, deceased.kind.value, "estate")
        if deceased.closed:
            raise AccountClosedError(deceased_agent_id)

        for heir_id in heirs:
            heir = self.get_account(heir_id)
            if heir.kind is not AccountKind.AGENT:
                raise CurrencyNotSupportedError(heir_id, heir.kind.value, "inheritance")
            if heir.closed:
                raise AccountClosedError(heir_id)

        usd_total = deceased.usd_cents
        sim_total = deceased.sim_units

        transactions: list[Transaction] = []
        transactions += await self.distribute_estate(
            deceased_agent_id, heirs, usd_total, Currency.USD, tick, memo
        )
        transactions += await self.distribute_estate(
            deceased_agent_id, heirs, sim_total, Currency.SIM, tick, memo
        )

        deceased.closed = True
        return transactions

    async def distribute_estate(
        self,
        deceased_agent_id: str,
        heirs: list[str],
        total: int,
        currency: Currency,
        tick: int,
        memo: str,
    ) -> list[Transaction]:
        """One currency's worth of settle_inheritance(). Every amount
        here is derived from `total`, a balance already known and owned
        solely by the deceased account at call time, so there is no
        insufficient-funds case to guard against beyond what move()
        already checks.
        """
        if total <= 0:
            return []

        transactions: list[Transaction] = []
        if not heirs:
            transactions.append(
                await self.move(
                    from_account_id=deceased_agent_id,
                    to_account_id=TREASURY_ACCOUNT_ID,
                    amount=total,
                    currency=currency,
                    reason=TransactionReason.INHERITANCE,
                    tick=tick,
                    memo=memo or "no heirs, estate escheated to treasury",
                )
            )
            return transactions

        share = total // len(heirs)
        remainder = total % len(heirs)
        if share > 0:
            for heir_id in heirs:
                transactions.append(
                    await self.move(
                        from_account_id=deceased_agent_id,
                        to_account_id=heir_id,
                        amount=share,
                        currency=currency,
                        reason=TransactionReason.INHERITANCE,
                        tick=tick,
                        memo=memo,
                    )
                )
        if remainder > 0:
            transactions.append(
                await self.move(
                    from_account_id=deceased_agent_id,
                    to_account_id=TREASURY_ACCOUNT_ID,
                    amount=remainder,
                    currency=currency,
                    reason=TransactionReason.INHERITANCE,
                    tick=tick,
                    memo="indivisible inheritance remainder escheated to treasury",
                )
            )
        return transactions
