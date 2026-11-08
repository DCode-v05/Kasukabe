"""The seams between crime/ and everything it needs but does not own:
money movement (world's ledger), injury (world/life's health system),
who's a sitting minister or a filled police officer (world/charter's
government state), and where an agent currently stands (world/core's
LocationIndex).

Same pattern as `world/life/ports.py`, for the same reason: crime/ is
built to be tested standalone, without importing a sibling package's
internals, and the real implementations are expected to be adapted onto
these `Protocol`s once world/'s tick engine or HTTP dispatcher wires
every world/ submodule together (see each module's own docstring for
which port it needs). Protocol is structural typing, not inheritance -
`world.core.perception.LocationIndex` already satisfies `LocationPort`
exactly as written, with zero adapter code, because its `location_of`
method already has this signature.
"""

from __future__ import annotations

from typing import Protocol

from world.crime.errors import NotCoLocated
from world.crime.models import InjurySeverity


class EconomyPort(Protocol):
    """What crime/ needs from the ledger. Amounts are always positive
    integer sim-currency units; a caller that has already validated an
    amount is responsible for not calling this with a non-positive one,
    same contract as `world.ledger.core.Ledger.move`.
    """

    async def transfer_sim(
        self, from_agent_id: str, to_agent_id: str, amount: int, tick: int, memo: str = ""
    ) -> None:
        """Move stolen proceeds from a theft victim to the thief on a
        successful attempt (theft.py)."""
        ...

    async def collect_fine(self, convict_agent_id: str, amount_sim: int, tick: int, memo: str = "") -> None:
        """Move a fine from the convicted agent to the treasury
        (outcomes.py). The destination account is the implementation's
        business, not crime/'s - this package never names
        `world.ledger.money.TREASURY_ACCOUNT_ID` directly."""
        ...


class HealthPort(Protocol):
    """What fights.py needs from the health system. DESIGN.md section 6:
    "Fights injure (feeds health system)". `world.life.health`'s
    `AilmentKind` enum (short/medium/long/terminal) has no injury variant
    today - it models randomly-rolled sickness, not assault - so this is
    the seam a future life/ update, or the world tick-engine glue layer,
    is expected to use to route a fight injury into `HealthState` however
    that system ends up representing it. Deliberately offers no way to
    report a death: DESIGN.md section 6 is explicit that "killing is
    excluded by world physics", and this Protocol has no method that
    could end an agent's life.
    """

    async def apply_injury(self, agent_id: str, severity: InjurySeverity, tick: int) -> None: ...


class GovernmentPort(Protocol):
    """What crime/ needs from charter/'s government state: who is
    currently a sitting minister (warrants.py's signature check) and who
    currently holds a filled police job (investigation.py, warrants.py's
    request side, arrests.py, outcomes.py). The real implementation
    adapts `world.charter.store.CharterStore.get_government_state()`
    (for `is_minister`) and its job list filtered to
    `job_type == "police"` and `status == JobStatus.FILLED` (for
    `is_police_officer`) - crime/ has no import-time dependency on
    charter/'s concrete types to keep the two packages independently
    testable, matching this whole file's rationale.
    """

    async def is_minister(self, agent_id: str) -> bool: ...

    async def is_police_officer(self, agent_id: str) -> bool: ...


class LocationPort(Protocol):
    """What crime/ needs from world/core's live agent positions. Matches
    `world.core.perception.LocationIndex.location_of`'s exact signature
    on purpose (see module docstring)."""

    def location_of(self, agent_id: str) -> str | None: ...


def require_co_located(location: LocationPort, agent_a_id: str, agent_b_id: str) -> str:
    """Shared proximity check for every peer-to-peer crime/justice action
    (theft, a fight, an interview, an arrest). Returns the shared
    location id so the caller can stamp it on the record it is building
    instead of looking it up a second time; raises NotCoLocated if the
    two agents are not in the same place (including either one having no
    known location at all).
    """
    location_a = location.location_of(agent_a_id)
    location_b = location.location_of(agent_b_id)
    if location_a is None or location_b is None or location_a != location_b:
        raise NotCoLocated(agent_a_id, agent_b_id, location_a, location_b)
    return location_a
