"""Politics as data (DESIGN.md section 7).

Every rule of Kasukabe's government is a typed field on `Charter`
(election_interval_days, seat counts, amendment threshold, suffrage and
term rules), mutable ONLY through the amendment flow in `amendments.py` -
propose, vote to a 3/5 threshold, then Presidential sign or veto. Nothing
in this package reads or writes a Charter field by any other path.

Submodules:

- `models.py`     - every typed record: Charter, GovernmentPolicy,
                    GovernmentState, Election, AmendmentProposal, Job,
                    License, Tender, PatentApplication, Patent,
                    LandPetition, Organization, ComplaintEntry.
- `errors.py`     - CharterError and its typed subclasses.
- `events.py`     - the `politics.*` event catalogue and the async
                    `EventSink` seam (Supabase writer wiring happens in
                    world/, not here).
- `store.py`      - CharterStore: the in-memory, lock-guarded, async
                    single-writer for every collection above. Stands in
                    for a future Supabase-backed store behind the same
                    async method signatures.
- `genesis.py`    - seeds a fresh store with the genesis charter and
                    government policy.
- `elections.py`  - candidacy declaration, campaign period, in-person
                    voting at town hall, tally, transfer of power.
- `amendments.py` - propose / vote / Presidential sign or veto for
                    Charter field changes.
- `government.py` - tax rate, credit-store margin, jobs, police hires,
                    permits, builder licenses, tenders, pension config.
- `patent_office.py` - file / approve / reject / revoke for
                    agent-invented tools.
- `land_petitions.py` - government petitions to the President for land
                    expansion, and the President's approve/deny.
- `organizations.py`  - register parties/cults/religions/societies with
                    member rosters.
- `complaints.py` - the immutable complaint ledger (a genesis check on
                    government power, distinct from the crime-reporting
                    complaints desk in a future crime/ module).
- `engine.py`     - CharterEngine, the one facade world/ constructs and
                    wires everything else to.

This package has no import-time dependency on world/'s agents, ledger or
tool registry. GROW-30: that used to give "they are not built yet" as the
reason. All three exist; the boundary is deliberate, because the tool
layer imports THIS package and importing back would be a cycle. Callers pass in whatever facts
those modules would otherwise supply (an agent's age/residency for
suffrage checks, a location string for location gating, a fee amount
already validated as paid) as explicit arguments, so this package can be
built, tested, and used standalone ahead of the modules it will
eventually be wired to.
"""

from __future__ import annotations
