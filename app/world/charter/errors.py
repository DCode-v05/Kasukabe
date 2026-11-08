"""Charter module error types.

One base class (`CharterError`) so a caller further up the stack (the
future world/ tool dispatcher) can catch every charter failure with a
single `except CharterError`, plus specific subclasses so it can also
branch on exactly what went wrong (surfaced to the inventing agent as a
tool-call error, per DESIGN.md section 2's tool registry). `CharterValidationError`
follows the same aggregate-all-errors shape as `engine/src/config.py`'s
`ConfigError`: a bad amendment can fail more than one Charter invariant at
once, and the caller benefits from seeing all of them in one response
instead of fixing them one at a time.
"""

from __future__ import annotations


class CharterError(Exception):
    """Base class for every error this module raises."""


class CharterValidationError(CharterError):
    """One or more field values would violate a Charter/GovernmentPolicy
    invariant. Wraps pydantic's own `ValidationError` message text so
    callers get charter-domain language instead of a raw pydantic trace.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "; ".join(self.errors)
        super().__init__(f"invalid charter value(s): {joined}")


class NotSittingGovernment(CharterError):
    """Caller is not currently the PM or a minister."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"{agent_id} does not hold a sitting government seat")


class NotAtRequiredLocation(CharterError):
    """Caller invoked a location-gated action from the wrong place
    (DESIGN.md section 2: "every tool is location-gated").
    """

    def __init__(self, required: str, actual: str) -> None:
        self.required = required
        self.actual = actual
        super().__init__(f"requires location {required!r}, agent is at {actual!r}")


class NotEligibleToVote(CharterError):
    """Fails the charter's suffrage rules (age and/or residency)."""

    def __init__(self, agent_id: str, reason: str) -> None:
        self.agent_id = agent_id
        self.reason = reason
        super().__init__(f"{agent_id} is not eligible to vote: {reason}")


class TermLimitExceeded(CharterError):
    def __init__(self, agent_id: str, terms_served: int, term_limit: int) -> None:
        self.agent_id = agent_id
        self.terms_served = terms_served
        self.term_limit = term_limit
        super().__init__(
            f"{agent_id} has served {terms_served} term(s), at the limit of {term_limit}"
        )


class ElectionPhaseError(CharterError):
    def __init__(self, election_id: str, expected: str, actual: str) -> None:
        self.election_id = election_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"election {election_id} is in phase {actual!r}, expected {expected!r}")


class AlreadyDeclaredCandidacy(CharterError):
    def __init__(self, election_id: str, agent_id: str) -> None:
        self.election_id = election_id
        self.agent_id = agent_id
        super().__init__(f"{agent_id} already declared candidacy in election {election_id}")


class AlreadyVoted(CharterError):
    def __init__(self, election_id: str, agent_id: str) -> None:
        self.election_id = election_id
        self.agent_id = agent_id
        super().__init__(f"{agent_id} already voted in election {election_id}")


class ElectionNotFound(CharterError):
    def __init__(self, election_id: str) -> None:
        self.election_id = election_id
        super().__init__(f"no election with id {election_id}")


class AmendmentNotFound(CharterError):
    def __init__(self, proposal_id: str) -> None:
        self.proposal_id = proposal_id
        super().__init__(f"no amendment proposal with id {proposal_id}")


class AmendmentNotPending(CharterError):
    def __init__(self, proposal_id: str, status: str) -> None:
        self.proposal_id = proposal_id
        self.status = status
        super().__init__(f"amendment {proposal_id} is {status!r}, not open for this action")


class UnknownCharterField(CharterError):
    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(f"{field_name!r} is not an amendable charter field")


class InsufficientFee(CharterError):
    def __init__(self, required_sim: int, paid_sim: int) -> None:
        self.required_sim = required_sim
        self.paid_sim = paid_sim
        super().__init__(f"fee of {required_sim} sim required, {paid_sim} sim paid")


class PatentApplicationNotFound(CharterError):
    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(f"no patent application with id {application_id}")


class PatentApplicationNotPending(CharterError):
    def __init__(self, application_id: str, status: str) -> None:
        self.application_id = application_id
        self.status = status
        super().__init__(f"patent application {application_id} is {status!r}, not pending")


class PatentNotFound(CharterError):
    def __init__(self, patent_id: str) -> None:
        self.patent_id = patent_id
        super().__init__(f"no patent with id {patent_id}")


class PatentAlreadyRevoked(CharterError):
    def __init__(self, patent_id: str) -> None:
        self.patent_id = patent_id
        super().__init__(f"patent {patent_id} is already revoked")


class PetitionNotFound(CharterError):
    def __init__(self, petition_id: str) -> None:
        self.petition_id = petition_id
        super().__init__(f"no land petition with id {petition_id}")


class PetitionNotPending(CharterError):
    def __init__(self, petition_id: str, status: str) -> None:
        self.petition_id = petition_id
        self.status = status
        super().__init__(f"land petition {petition_id} is {status!r}, not pending")


class JobNotFound(CharterError):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"no job with id {job_id}")


class LicenseNotFound(CharterError):
    def __init__(self, license_id: str) -> None:
        self.license_id = license_id
        super().__init__(f"no license with id {license_id}")


class LicenseAlreadyRevoked(CharterError):
    def __init__(self, license_id: str) -> None:
        self.license_id = license_id
        super().__init__(f"license {license_id} is already revoked")


class TenderNotFound(CharterError):
    def __init__(self, tender_id: str) -> None:
        self.tender_id = tender_id
        super().__init__(f"no tender with id {tender_id}")


class TenderNotOpen(CharterError):
    def __init__(self, tender_id: str, status: str) -> None:
        self.tender_id = tender_id
        self.status = status
        super().__init__(f"tender {tender_id} is {status!r}, not open")


class OrganizationNotFound(CharterError):
    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        super().__init__(f"no organization with id {org_id}")


class DuplicateOrganizationName(CharterError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"an organization named {name!r} already exists")


class NotOrganizationMember(CharterError):
    def __init__(self, org_id: str, agent_id: str) -> None:
        self.org_id = org_id
        self.agent_id = agent_id
        super().__init__(f"{agent_id} is not a member of organization {org_id}")
