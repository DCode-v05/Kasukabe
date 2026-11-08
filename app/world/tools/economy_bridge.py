"""Connective tissue between world/tools/ and three sibling modules that
were, by design, built without depending on each other: world.ledger
(money), world.life (health/aging/marriage, built against a placeholder
`EconomyPort` Protocol - see life/ports.py's own docstring: "this is not a
permanent abstraction layer ... it should collapse to a direct call into
ledger.py once that module lands"), and world.charter (politics, built
against a plain-dict `EventSink` - see charter/events.py's own docstring).
That module landed as this package. This file is the collapse both of
those docstrings were waiting for.

Two seams:

- `LedgerEconomyPort` implements `life.ports.EconomyPort` over a real
  `world.ledger.Ledger`, so life/'s pure functions (marry(), the health
  and death flows) can be called by tool handlers without life/ ever
  importing ledger/ directly. Every life-initiated debit/credit is
  sim-currency moved against the government treasury - DESIGN.md never
  gives the temple, the pharmacy, or any other genesis building its own
  ledger account, and TOTAL_USD_CENTS' invariant only tracks agent
  wallets/treasury/burned anyway (ledger/money.py), so treasury is the
  correct, and only sensible, sim-currency counterparty for a fee with no
  dedicated sink. This is flagged as an open question in this task's
  report for whoever eventually gives genesis buildings their own
  accounts.
- `charter_event_to_world_event` / `life_event_to_world_event` convert
  charter's `{"type", "payload"}` dict and life's `LifeEvent` into
  `world.core.events.WorldEvent`, the one event type every world/
  submodule is expected to publish (core/events.py's own docstring).
  Both source shapes lack `tick` (charter/life were built to not depend
  on a clock module that did not exist yet); the tools-owned WorldState
  supplies it at the point of translation, alongside folding a bare
  `agent_id` into the `target_agent_id` payload key so
  core.perception.default_interrupt_policy picks the event up as
  something that agent should hear about even from across the map.
"""

from __future__ import annotations

from typing import Any, Callable

from world.core.events import WorldEvent
from world.ledger.core import Ledger
from world.ledger.errors import InsufficientFundsError as LedgerInsufficientFundsError
from world.ledger.models import Currency, LedgerEvent, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.life.models import LifeEvent
from world.life.ports import InsufficientFundsError as LifeInsufficientFundsError

TickProvider = Callable[[], int]


class LedgerEconomyPort:
    """`life.ports.EconomyPort`, backed by a real `world.ledger.Ledger`.

    Structural typing means no inheritance declaration is needed for this
    to satisfy the Protocol; it is written out anyway (matching methods,
    matching signatures) so a type checker confirms it.
    """

    PAYEE_BUSINESS_TYPE: dict[str, str] = {
        "pharmacy_treatment": "pharmacy",
    }

    def __init__(
        self, ledger: Ledger, tick_provider: TickProvider, world: object | None = None
    ) -> None:
        self.ledger = ledger
        self.tick_provider = tick_provider
        self.world = world

    def payee_for(self, reason: str) -> str:
        """ECON-29: who should receive this fee.

        The treasury when nobody in particular should - a wedding fee has
        no payee today because the temple is a PLACE and not a business
        with an account, so the town takes it. That is a real answer
        rather than a fallback, and it is written here so the next reader
        does not have to infer it from silence.
        """
        wanted = self.PAYEE_BUSINESS_TYPE.get(reason)
        if not wanted or self.world is None:
            return TREASURY_ACCOUNT_ID
        for business in self.world.businesses.values():
            if business.business_type != wanted:
                continue
            try:
                self.ledger.get_account(business.business_id)
            except Exception:  # noqa: BLE001 - a business with no account
                continue
            return business.business_id
        return TREASURY_ACCOUNT_ID

    REASONS: "dict[str, TransactionReason]" = {
        "daily_upkeep": TransactionReason.UPKEEP,
        "revival_payment": TransactionReason.REVIVAL,
        "baby_blessing": TransactionReason.BLESSING,
        "pharmacy_treatment": TransactionReason.FEE,
        "temple_wedding_fee": TransactionReason.FEE,
    }

    @classmethod
    def reason_for(cls, reason: str) -> TransactionReason:
        """ECON-03: the ledger reason for a life-side reason string.

        An unknown string stays TRANSFER rather than being guessed into a
        category. A movement filed under the WRONG heading is worse for
        the research than one filed under none, and the memo still carries
        the original string either way.
        """
        return cls.REASONS.get(reason, TransactionReason.TRANSFER)

    async def debit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        try:
            await self.ledger.move(
                from_account_id=agent_id,
                to_account_id=self.payee_for(reason),
                amount=amount,
                currency=Currency.SIM,
                reason=self.reason_for(reason),
                tick=self.tick_provider(),
                memo=reason,
            )
        except LedgerInsufficientFundsError as exc:
            raise LifeInsufficientFundsError(str(exc)) from exc

    async def credit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        await self.ledger.move(
            from_account_id=TREASURY_ACCOUNT_ID,
            to_account_id=agent_id,
            amount=amount,
            currency=Currency.SIM,
            reason=self.reason_for(reason),
            tick=self.tick_provider(),
            memo=reason,
        )

    async def credit_usd_cents(self, agent_id: str, amount_cents: int, reason: str) -> None:
        await self.ledger.move(
            from_account_id=TREASURY_ACCOUNT_ID,
            to_account_id=agent_id,
            amount=amount_cents,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=self.tick_provider(),
            memo=reason,
        )

    async def get_sim_balance(self, agent_id: str) -> int:
        return self.ledger.sim_balance(agent_id)


class LedgerGuardianshipPort:
    """`life.ports.GuardianshipPort`, backed by a real `world.ledger.Ledger`.

    Structural typing means no inheritance declaration is needed for this
    to satisfy the Protocol; written out anyway, same as
    LedgerEconomyPort above, so a type checker confirms it.
    """

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def end_parent_billing(self, agent_id: str) -> None:
        self.ledger.end_parent_billing(agent_id)


def ledger_event_to_world_event(event: LedgerEvent) -> WorldEvent:
    """`world.ledger.models.LedgerEvent` -> `WorldEvent`. Unlike charter's
    and life's event shapes, `LedgerEvent` already carries its own `tick`
    (ledger.py stamps it at the point of the movement), so no caller-side
    tick is needed here. This is the sink bootstrap.py wires into
    `Ledger(event_sink=...)`, which matters beyond completeness: it is
    the only path `economy.ledger.violated` (CONVENTIONS.md's ledger
    invariant tripwire) ever reaches the world event bus, and every
    consumer of that bus is expected to treat seeing it as "the
    simulation is now halted."
    """
    return WorldEvent(type=event.type, tick=event.tick, payload=dict(event.payload))


def charter_event_to_world_event(event: dict[str, Any], tick: int) -> WorldEvent:
    """`{"type": ..., "payload": ...}` (charter/events.py's `build_event`
    shape) -> WorldEvent. charter's own event types are already valid
    `<domain>.<subject>.<verb>` strings in the `politics` domain, so no
    translation beyond adding `tick` is needed.
    """
    return WorldEvent(type=event["type"], tick=tick, payload=dict(event.get("payload", {})))


def life_event_to_world_event(event: LifeEvent, tick: int) -> WorldEvent:
    """`LifeEvent` -> `WorldEvent`, folding the bare `agent_id` field into
    both the `target_agent_id` payload key (core/perception.py's
    payload-key convention, so a life-system event about one agent can
    interrupt that agent's schedule and reach their perception even when
    it happened somewhere else on the map - a marriage or a death is not
    something an agent only learns about by standing in the right place)
    and the plain `agent_id` payload key that core/perception.py's
    LocationIndex.attach() separately requires for `agent.moved` /
    `agent.spawned` / `agent.died` (its own docstring: "expected to
    publish `agent.moved` / `agent.spawned` with a string `agent_id` and
    `location_id` payload (and `agent.died` with just `agent_id`)").
    Without this second key, life/tick.py's own `agent.died` events (the
    only path world/ has for a natural death) reached LocationIndex with
    `target_agent_id` but no `agent_id`, so LocationIndex.remove() never
    fired and a dead agent stayed "present" at their last location
    forever - confirmed by the genesis dry run's own log spam ("agent.*
    event missing a string agent_id payload field, ignored by
    LocationIndex") for every life-domain event, not just deaths.
    """
    payload = dict(event.payload)
    if event.agent_id is not None:
        payload.setdefault("target_agent_id", event.agent_id)
        payload.setdefault("agent_id", event.agent_id)
    return WorldEvent(type=event.type, tick=tick, payload=payload)
