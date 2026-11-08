"""Genesis: the town's first charter and government policy.

DESIGN.md section 7: "Genesis Town Charter: free-market democracy."
Section 3 backs that with the economic defaults (no tax, no credit-store
margin at the start - prices are "fully emergent"). Everything built here
is exactly the defaults from `Charter()` and `GovernmentPolicy()` with
`version=0`: genesis is not special-cased data, it is what you get from
the ordinary models with nothing amended yet. That is deliberate - it
keeps "what changed since genesis" a simple diff against `charter_history[0]`
instead of genesis living in a separate shape.

The government seats start vacant (DESIGN.md doesn't specify a caretaker
government; the town's first PM and ministers are whoever wins the first
election). `government.py`'s actions that require a sitting seat holder
will correctly refuse to run until the first election concludes.

GROW-12. That is still the default, and it had a cost nobody had priced:
no run has ever crossed a first election, so every seat-gated verb has
refused every resident in every run - including `petition`, which is the
only in-world entry to the land-expansion pipeline. "The town physically
grows" has therefore never been reachable from inside the town.

A genesis file may now name `caretaker_pm_agent_id`, and this seeds them
as an interim PM marked `caretaker=True`. Off unless asked for, because
appointing a PM nobody elected is a research decision (DESIGN section 10
measures regime transitions) - and their `terms_served` stays empty, so
those metrics never mistake an appointment for a term.
"""

from __future__ import annotations

from world.charter.events import EVENT_CHARTER_GENESIS_SEEDED, EventSink, build_event, noop_sink
from world.charter.models import Charter, GovernmentPolicy, GovernmentState
from world.charter.store import CharterStore


GENESIS_TAX_RATE_BPS_FALLBACK = 300


def genesis_tax_rate_bps() -> int:
    """The rulebook's tax rate, or the fallback if it cannot be read.

    Falls back rather than raising: a malformed or missing rulebook must
    not stop a town from booting, and a town that boots at 300 bps is
    closer to right than one that does not boot at all.
    """
    try:
        from world.rules.loader import load_rules

        return int(load_rules().get("economy.tax_rate_bps"))
    except Exception:  # noqa: BLE001 - see the docstring
        return GENESIS_TAX_RATE_BPS_FALLBACK


async def seed_genesis(
    store: CharterStore,
    genesis_tick: int,
    event_sink: EventSink | None = None,
    caretaker_pm_agent_id: str | None = None,
    charter_fields: dict[str, object] | None = None,
) -> None:
    """Populate a fresh `CharterStore` with the genesis charter and
    government policy. Idempotent only in the sense that calling it twice
    on the same store appends two charter versions - callers should call
    this exactly once per store, which is what `engine.py`'s
    `create_genesis_charter_engine` does.
    """
    sink = event_sink if event_sink is not None else noop_sink
    charter = Charter(version=0, genesis_tick=genesis_tick, **(charter_fields or {}))
    policy = GovernmentPolicy(version=0, tax_rate_bps=genesis_tax_rate_bps())
    await store.seed_charter(charter)
    await store.seed_policy(policy)

    if caretaker_pm_agent_id:
        await store.set_government_state(
            GovernmentState(
                pm_agent_id=caretaker_pm_agent_id,
                minister_agent_ids=[],
                terms_served={},
                caretaker=True,
            )
        )
    await sink(
        build_event(
            EVENT_CHARTER_GENESIS_SEEDED,
            {
                "genesis_tick": genesis_tick,
                "charter": charter.model_dump(mode="json"),
                "policy": policy.model_dump(mode="json"),
            },
        )
    )
