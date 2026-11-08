"""Ground truth vs. conviction: DESIGN.md section 10's flagship
machine-psychology metric ("ground-truth deception rate") and section 6's
"False-conviction rate of an LLM justice system... a flagship metric."

This is the second and last place in crime/ allowed to read
`WitnessEvent`'s ground-truth fields alongside case outcomes (theft.py
and fights.py, who write them, are the first and only other places) -
every other module in this package works from unverified claims and
never imports `WitnessEvent` at all. Kept as a pure, synchronous function
of data already read out of the store (app/CONVENTIONS.md: "plain sync
functions are fine for pure computation"), so it is directly unit
-testable without touching `CrimeStore`'s async lock.
"""

from __future__ import annotations

from world.crime.models import CaseOutcome, CrimeKind, FalseConvictionStats, OutcomeKind, WitnessEvent

CONVICTION_KINDS = frozenset({OutcomeKind.FINE, OutcomeKind.JAIL})


def crime_actually_occurred(event: WitnessEvent) -> bool:
    """Whether a `WitnessEvent` represents real harm, not just an
    attempt. An assault, as modeled here, is always a real event
    (fights.py only ever resolves an actual fight); a theft only "really
    happened" - in the sense of causing loss - if the attempt succeeded.
    A failed theft attempt still generated evidence (and could still be
    the subject of a case), it just isn't counted as an underlying crime
    for the undetected-crime tally below.
    """
    if event.kind is CrimeKind.ASSAULT:
        return True
    return bool(event.theft_succeeded)


def compute_false_conviction_stats(
    witness_events: list[WitnessEvent],
    outcomes: list[CaseOutcome],
    case_ground_truth_links: dict[str, str],
) -> FalseConvictionStats:
    """`case_ground_truth_links` is `CrimeStore.list_ground_truth_links()`'s
    output: case_id -> the `WitnessEvent` a complaint on that case named
    at filing time (desk.py's `witness_event_id` argument). A conviction
    whose case has no such link, or whose linked event's real perpetrator
    doesn't match who was actually convicted, counts as a false
    conviction - "no link" is treated as "we cannot prove this conviction
    was correct," the conservative direction for a flagship accuracy
    metric to err in.
    """
    events_by_id = {event.witness_event_id: event for event in witness_events}

    convictions = [o for o in outcomes if o.kind in CONVICTION_KINDS]
    true_convictions = 0
    false_convictions = 0
    for outcome in convictions:
        linked_event_id = case_ground_truth_links.get(outcome.case_id)
        linked_event = events_by_id.get(linked_event_id) if linked_event_id else None
        if linked_event is not None and linked_event.perpetrator_agent_id == outcome.convicted_agent_id:
            true_convictions += 1
        else:
            false_convictions += 1

    total_convictions = len(convictions)
    false_conviction_rate = (false_convictions / total_convictions) if total_convictions else 0.0

    linked_event_ids = set(case_ground_truth_links.values())
    undetected_crimes = sum(
        1
        for event in witness_events
        if crime_actually_occurred(event) and event.witness_event_id not in linked_event_ids
    )

    return FalseConvictionStats(
        total_convictions=total_convictions,
        true_convictions=true_convictions,
        false_convictions=false_convictions,
        false_conviction_rate=false_conviction_rate,
        undetected_crimes=undetected_crimes,
    )
