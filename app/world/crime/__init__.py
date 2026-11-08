"""Crime and justice (DESIGN.md section 6, and section 2's `arrest`
primitive).

This package is world/'s justice system: a witness-evidence model ground
truth flows into once and never again, a police force recruited by
government on a treasury salary, and a conviction pipeline that is
deliberately blind to the very ground truth this package itself records -
so that DESIGN.md section 10's flagship "false-conviction rate of an LLM
justice system" metric measures something real.

Submodules:

- `models.py`         - every typed record: WitnessEvent (ground truth),
                         ComplaintRecord, CaseFile, InterviewLog, Warrant,
                         ArrestRecord, CaseOutcome, ConfinementRecord,
                         FalseConvictionStats.
- `errors.py`          - CrimeError and its typed subclasses.
- `events.py`          - the `crime.*` event catalogue and the async
                         `EventSink` seam (Supabase writer wiring happens
                         in world/, not here).
- `ports.py`           - Protocol seams onto the ledger (money), health
                         (injury), charter (minister/police checks), and
                         core (agent location), so this package builds
                         and tests standalone.
- `store.py`           - CrimeStore: the in-memory, lock-guarded, async
                         single-writer for every collection above.
- `theft.py`           - the `steal` primitive's resolution: success/fail
                         and witness rolls (DESIGN.md's 70%/30%), and the
                         one true record of who actually did it.
- `fights.py`          - the fight mechanic: injury only, structurally
                         no death path, feeds `HealthPort`.
- `desk.py`            - the crime complaints desk: files a complaint,
                         opens or attaches to a `CaseFile`.
- `investigation.py`   - police interviews and the "never omniscient"
                         evidence view: public records plus an officer's
                         own interviews, nothing more.
- `warrants.py`        - warrant request (police) and signature
                         (minister only).
- `arrests.py`         - the `arrest` primitive: police-only, co-located.
- `outcomes.py`        - fine (ledger) or jail (confinement, no income,
                         tools locked to the jail location), gated on a
                         granted warrant.
- `metrics.py`         - ground truth vs. conviction: the false
                         -conviction rate.
- `engine.py`          - CrimeEngine, the one facade world/ constructs
                         and wires everything else to.

Like charter/ and life/, this package has no import-time dependency on
any not-yet-built world/ module (agents, the tool registry, Supabase). It
depends on one already-built sibling directly - `world.charter.models`'s
`LOCATION_GOVERNMENT_COMPLEX` constant, reused rather than duplicated as
a literal string - and on everything else only through `ports.py`'s
Protocols, so it can be built, tested, and used standalone ahead of the
modules (world/'s tool dispatcher, the real ledger/health/charter wiring)
it will eventually be wired to.
"""

from __future__ import annotations
