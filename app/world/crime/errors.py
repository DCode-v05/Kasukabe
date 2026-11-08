"""Crime module error types.

One base class (`CrimeError`) so a future tool dispatcher can catch every
crime-package failure in one place, plus specific subclasses so it can
branch on exactly what went wrong, matching the shape of
`world/charter/errors.py` (see that module's own docstring for the
rationale). Kept as a fully independent hierarchy rather than subclassing
charter's `CharterError`: a caller handling a crime-tool failure should
not need to import charter's exception tree to catch it.
"""

from __future__ import annotations


class CrimeError(Exception):
    """Base class for every error this package raises."""


class NotAtRequiredLocation(CrimeError):
    """A government-complex-gated action (filing a complaint, requesting
    or signing a warrant, imposing an outcome) was attempted from
    somewhere else (DESIGN.md section 2: "every tool is location-gated").
    """

    def __init__(self, required: str, actual: str) -> None:
        self.required = required
        self.actual = actual
        super().__init__(f"requires location {required!r}, agent is at {actual!r}")


class NotCoLocated(CrimeError):
    """A proximity-gated action (theft, a fight, an interview, an arrest)
    was attempted between two agents who are not standing in the same
    place. DESIGN.md section 6 calls theft a "proximity tool"; this
    package extends the same physical-presence requirement to every
    peer-to-peer crime/justice action that has no fixed building to
    anchor a `NotAtRequiredLocation` check against instead.
    """

    def __init__(self, agent_a_id: str, agent_b_id: str, location_a: str | None, location_b: str | None) -> None:
        self.agent_a_id = agent_a_id
        self.agent_b_id = agent_b_id
        self.location_a = location_a
        self.location_b = location_b
        super().__init__(
            f"{agent_a_id!r} (at {location_a!r}) and {agent_b_id!r} (at {location_b!r}) "
            "are not co-located"
        )


class CannotTargetSelf(CrimeError):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"{agent_id!r} cannot target itself")


class InvalidCrimeAmount(CrimeError):
    def __init__(self, amount: int) -> None:
        self.amount = amount
        super().__init__(f"amount must be a positive integer, got {amount}")


class NotPoliceOfficer(CrimeError):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"{agent_id!r} does not hold a filled police job")


class NotMinister(CrimeError):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"{agent_id!r} does not hold a sitting minister seat")


class CaseNotFound(CrimeError):
    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        super().__init__(f"no case with id {case_id!r}")


class CaseAlreadyClosed(CrimeError):
    def __init__(self, case_id: str, status: str) -> None:
        self.case_id = case_id
        self.status = status
        super().__init__(f"case {case_id!r} is {status!r}, already closed")


class WarrantNotFound(CrimeError):
    def __init__(self, warrant_id: str) -> None:
        self.warrant_id = warrant_id
        super().__init__(f"no warrant with id {warrant_id!r}")


class WarrantNotPending(CrimeError):
    def __init__(self, warrant_id: str, status: str) -> None:
        self.warrant_id = warrant_id
        self.status = status
        super().__init__(f"warrant {warrant_id!r} is {status!r}, not open for this decision")


class WarrantNotGranted(CrimeError):
    """Raised when outcomes.py is asked to fine or jail someone off a
    warrant that was never signed, or that names a different agent or
    case (DESIGN.md section 6: "minister-signed warrant -> fine or
    jail" is not optional).
    """

    def __init__(self, warrant_id: str, detail: str) -> None:
        self.warrant_id = warrant_id
        self.detail = detail
        super().__init__(f"warrant {warrant_id!r} does not authorize this outcome: {detail}")


class AlreadyConfined(CrimeError):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"{agent_id!r} is already serving a confinement")
