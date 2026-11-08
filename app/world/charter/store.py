"""In-memory state store for the charter module.

world/ doesn't have a Supabase-backed world-state store yet (DESIGN.md
section 9 lists `petitions`, `organizations`, `tools_registry`, and the
rest as future Supabase tables; app/CONVENTIONS.md says world/ "is the
single writer" for them once they exist). Until that lands, `CharterStore`
is the single writer for charter's slice of world state, held in memory.

Every public method is `async def` and guarded by one `asyncio.Lock`, even
though nothing here does real I/O today: app/CONVENTIONS.md requires async
for "world-state mutation" as a category, not just for the I/O calls that
happen to back it today, and this store's methods are exactly the surface
that gets swapped for real Supabase reads/writes later without changing
any caller's `await store.something(...)` call shape. The lock matters
even in memory: up to 250 agents can call government/election/patent
tools concurrently against one running world server process, and
"declare candidacy" or "cast vote" read-then-write against shared dicts
that must not interleave.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from world.charter.models import (
    Charter,
    ComplaintEntry,
    Election,
    GovernmentPolicy,
    GovernmentState,
    Job,
    LandPetition,
    License,
    Organization,
    Patent,
    PatentApplication,
    Tender,
)


@dataclass(frozen=True)
class OwnedField:
    """One field CharterStore.snapshot()/restore() round-trips: its name,
    how it's shaped (a list of models, a dict of models keyed by id, or a
    single model), and the pydantic model type to validate back into on
    restore.
    """

    name: str
    shape: str
    model: type


CHARTER_STORE_FIELDS: tuple[OwnedField, ...] = (
    OwnedField("charter_history", "list_model", Charter),
    OwnedField("policy_history", "list_model", GovernmentPolicy),
    OwnedField("government_state", "single_model", GovernmentState),
    OwnedField("elections", "dict_model", Election),
    OwnedField("jobs", "dict_model", Job),
    OwnedField("licenses", "dict_model", License),
    OwnedField("tenders", "dict_model", Tender),
    OwnedField("patent_applications", "dict_model", PatentApplication),
    OwnedField("patents", "dict_model", Patent),
    OwnedField("land_petitions", "dict_model", LandPetition),
    OwnedField("organizations", "dict_model", Organization),
    OwnedField("complaint_ledger", "list_model", ComplaintEntry),
)


class CharterStore:
    """Holds every piece of state charter/ owns.

    Collections keyed by id are plain dicts; the append-only complaint
    ledger is a list indexed by its own `ledger_index` field. Nothing
    outside this module writes to these dicts directly - every mutation
    goes through a method here, which is what makes the lock meaningful.
    """

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.charter_history: list[Charter] = []
        self.policy_history: list[GovernmentPolicy] = []
        self.government_state = GovernmentState()
        self.elections: dict[str, Election] = {}
        self.jobs: dict[str, Job] = {}
        self.licenses: dict[str, License] = {}
        self.tenders: dict[str, Tender] = {}
        self.patent_applications: dict[str, PatentApplication] = {}
        self.patents: dict[str, Patent] = {}
        self.land_petitions: dict[str, LandPetition] = {}
        self.organizations: dict[str, Organization] = {}
        self.complaint_ledger: list[ComplaintEntry] = []

        declared = {f.name for f in CHARTER_STORE_FIELDS}
        actual = set(vars(self)) - {"lock"}
        if declared != actual:
            missing = sorted(actual - declared)
            stale = sorted(declared - actual)
            raise AssertionError(
                "CharterStore.CHARTER_STORE_FIELDS has drifted from CharterStore.__init__: "
                f"attributes set by __init__ but missing from CHARTER_STORE_FIELDS={missing}, "
                f"entries in CHARTER_STORE_FIELDS no longer set by __init__={stale}. "
                "Update world/charter/store.py's CHARTER_STORE_FIELDS tuple so "
                "snapshot()/restore() keep covering every piece of governance state."
            )

    def current_charter(self) -> Charter:
        """Latest charter snapshot. Raises IndexError if `seed_charter`
        was never called; callers use `genesis.py`'s factory, which
        always seeds one, so hitting this in the real system means the
        store was built by hand and skipped genesis.
        """
        return self.charter_history[-1]

    def current_policy(self) -> GovernmentPolicy:
        return self.policy_history[-1]

    async def seed_charter(self, charter: Charter) -> None:
        async with self.lock:
            self.charter_history.append(charter)

    async def seed_policy(self, policy: GovernmentPolicy) -> None:
        async with self.lock:
            self.policy_history.append(policy)

    async def push_charter(self, charter: Charter) -> None:
        async with self.lock:
            self.charter_history.append(charter)

    async def push_policy(self, policy: GovernmentPolicy) -> None:
        async with self.lock:
            self.policy_history.append(policy)

    async def get_charter(self) -> Charter:
        async with self.lock:
            return self.current_charter()

    async def get_policy(self) -> GovernmentPolicy:
        async with self.lock:
            return self.current_policy()

    async def get_government_state(self) -> GovernmentState:
        async with self.lock:
            return self.government_state.model_copy(deep=True)

    async def set_government_state(self, state: GovernmentState) -> None:
        async with self.lock:
            self.government_state = state

    async def put_election(self, election: Election) -> None:
        async with self.lock:
            self.elections[election.election_id] = election

    async def get_election(self, election_id: str) -> Election | None:
        async with self.lock:
            election = self.elections.get(election_id)
            return election.model_copy(deep=True) if election is not None else None

    async def list_elections(self) -> list[Election]:
        async with self.lock:
            return [e.model_copy(deep=True) for e in self.elections.values()]

    async def put_job(self, job: Job) -> None:
        async with self.lock:
            self.jobs[job.job_id] = job

    async def get_job(self, job_id: str) -> Job | None:
        async with self.lock:
            job = self.jobs.get(job_id)
            return job.model_copy(deep=True) if job is not None else None

    async def list_jobs(self) -> list[Job]:
        async with self.lock:
            return [j.model_copy(deep=True) for j in self.jobs.values()]

    async def put_license(self, license_: License) -> None:
        async with self.lock:
            self.licenses[license_.license_id] = license_

    async def get_license(self, license_id: str) -> License | None:
        async with self.lock:
            license_ = self.licenses.get(license_id)
            return license_.model_copy(deep=True) if license_ is not None else None

    async def list_licenses(self) -> list[License]:
        async with self.lock:
            return [lic.model_copy(deep=True) for lic in self.licenses.values()]

    async def put_tender(self, tender: Tender) -> None:
        async with self.lock:
            self.tenders[tender.tender_id] = tender

    async def get_tender(self, tender_id: str) -> Tender | None:
        async with self.lock:
            tender = self.tenders.get(tender_id)
            return tender.model_copy(deep=True) if tender is not None else None

    async def list_tenders(self) -> list[Tender]:
        async with self.lock:
            return [t.model_copy(deep=True) for t in self.tenders.values()]

    async def put_patent_application(self, application: PatentApplication) -> None:
        async with self.lock:
            self.patent_applications[application.application_id] = application

    async def get_patent_application(self, application_id: str) -> PatentApplication | None:
        async with self.lock:
            application = self.patent_applications.get(application_id)
            return application.model_copy(deep=True) if application is not None else None

    async def list_patent_applications(self) -> list[PatentApplication]:
        async with self.lock:
            return [a.model_copy(deep=True) for a in self.patent_applications.values()]

    async def put_patent(self, patent: Patent) -> None:
        async with self.lock:
            self.patents[patent.patent_id] = patent

    async def get_patent(self, patent_id: str) -> Patent | None:
        async with self.lock:
            patent = self.patents.get(patent_id)
            return patent.model_copy(deep=True) if patent is not None else None

    async def list_patents(self) -> list[Patent]:
        async with self.lock:
            return [p.model_copy(deep=True) for p in self.patents.values()]

    async def put_land_petition(self, petition: LandPetition) -> None:
        async with self.lock:
            self.land_petitions[petition.petition_id] = petition

    async def get_land_petition(self, petition_id: str) -> LandPetition | None:
        async with self.lock:
            petition = self.land_petitions.get(petition_id)
            return petition.model_copy(deep=True) if petition is not None else None

    async def list_land_petitions(self) -> list[LandPetition]:
        async with self.lock:
            return [p.model_copy(deep=True) for p in self.land_petitions.values()]

    async def put_organization(self, organization: Organization) -> None:
        async with self.lock:
            self.organizations[organization.org_id] = organization

    async def get_organization(self, org_id: str) -> Organization | None:
        async with self.lock:
            organization = self.organizations.get(org_id)
            return organization.model_copy(deep=True) if organization is not None else None

    async def list_organizations(self) -> list[Organization]:
        async with self.lock:
            return [o.model_copy(deep=True) for o in self.organizations.values()]

    async def append_complaint(
        self,
        complainant_agent_id: str,
        against_id: str,
        against_kind: str,
        description: str,
        filed_tick: int,
    ) -> ComplaintEntry:
        async with self.lock:
            entry = ComplaintEntry(
                ledger_index=len(self.complaint_ledger),
                complainant_agent_id=complainant_agent_id,
                against_id=against_id,
                against_kind=against_kind,
                description=description,
                filed_tick=filed_tick,
            )
            self.complaint_ledger.append(entry)
            return entry

    async def list_complaints(self) -> list[ComplaintEntry]:
        async with self.lock:
            return list(self.complaint_ledger)


    def snapshot(self) -> dict[str, Any]:
        """JSON-safe dump of every field CHARTER_STORE_FIELDS names -
        the full charter history (every amendment-produced version),
        policy history, sitting government, every election (in whatever
        phase it's in, tallied or not), jobs, licenses, tenders, the
        patent-office queue (applications and granted patents),
        land petitions, organizations, and the immutable complaint
        ledger."""
        data: dict[str, Any] = {}
        for owned in CHARTER_STORE_FIELDS:
            value = getattr(self, owned.name)
            if owned.shape == "list_model":
                data[owned.name] = [item.model_dump(mode="json") for item in value]
            elif owned.shape == "dict_model":
                data[owned.name] = {k: v.model_dump(mode="json") for k, v in value.items()}
            else:
                data[owned.name] = value.model_dump(mode="json")
        return data

    def restore(self, data: dict[str, Any]) -> None:
        """Inverse of snapshot(): rebuilds real pydantic model instances
        (never raw dicts - `current_charter()` and every sub-engine
        method downstream expect a real `Charter`/`Election`/etc, not
        JSON). Deliberately has no `.get(name, default)` legacy-checkpoint
        tolerance: CHARTER_STORE_FIELDS did not exist before this fix, so
        there is no earlier checkpoint format that ever wrote governance
        state to restore leniently - a checkpoint claiming to carry a
        "charter" component but missing one of these keys is corrupt, and
        CheckpointManager's own aggregated-error behavior (see
        world/core/checkpoint.py) is the right way to surface that, not a
        silently-empty field.
        """
        for owned in CHARTER_STORE_FIELDS:
            raw = data[owned.name]
            if owned.shape == "list_model":
                setattr(self, owned.name, [owned.model.model_validate(item) for item in raw])
            elif owned.shape == "dict_model":
                setattr(
                    self, owned.name, {k: owned.model.model_validate(v) for k, v in raw.items()}
                )
            else:
                setattr(self, owned.name, owned.model.model_validate(raw))
