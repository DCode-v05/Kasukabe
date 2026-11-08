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



# ECON-01 / GOV-06 / GOV-18: what the town starts out taxing.
#
# `GovernmentPolicy.tax_rate_bps` defaults to 0, and nothing ever changed
# it: `set_tax_rate` is government-namespace, seats are vacant because no
# election has ever been tallied (GOV-01), so the only rate Kasukabe has
# ever had is nothing. `head_tax_sim_per_head` then returns 0,
# `charge_daily_head_tax` skips every move, and the S1 gate - which counted
# households visited, not sim moved - reported a fully taxed day. The tax
# is the government's only revenue, the complaints desk's only cause, and
# the thing an election is about; at zero, none of those exist.
#
# The number is READ from `world/rules/village_rules.json` rather than
# retyped here. That file already says 300 bps and, per GOV-18, was a
# second rulebook only the browser consulted - the world's charter said 0
# while the viewer's economy said 3%, and the two could disagree with
# nobody noticing. One of them has to be the source; this makes it that
# one, for this field.
#
# A zero rate remains a legal policy: a government may vote the tax away.
# What is fixed here is that the town does not START at zero by accident.
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
    # `charter_fields` is how a scenario (world/scenario.py) seeds its own
    # rules - four-hour election windows instead of day-long ones, fewer
    # minister seats - at VERSION 0. Amending them afterwards would record
    # the town as having changed its own constitution before anyone woke up,
    # which is a different fact from the one the run is testing.
    charter = Charter(version=0, genesis_tick=genesis_tick, **(charter_fields or {}))
    policy = GovernmentPolicy(version=0, tax_rate_bps=genesis_tax_rate_bps())
    await store.seed_charter(charter)
    await store.seed_policy(policy)

    # GROW-12: a caretaker, only if a genesis file named one.
    #
    # Seats vacant is still the default and still what every run so far
    # did. The problem that made this switch necessary: no run has ever
    # crossed a first election, so `petition` - the only in-world entry to
    # the land-expansion pipeline - has refused every resident in every
    # run, and DESIGN section 1's "the town physically grows" has never
    # been reachable from inside the town.
    #
    # `terms_served` is left EMPTY on purpose. A caretaker was not
    # elected, so they have not served a term, and DESIGN section 10's
    # regime-transition metrics must not count them as one. `caretaker`
    # on the state is what lets a reader tell the difference.
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
