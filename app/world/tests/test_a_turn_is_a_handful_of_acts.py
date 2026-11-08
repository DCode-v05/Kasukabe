"""Twelve was not a ceiling; it was a number nothing reached.

Measured from the 50-resident run's own turn records: 1,957 turns, 9,488
tool calls, mean 4.85, median 3, p90 10. A ceiling of 12 would have cut 4%
of turns short - which is to say it bound almost nothing - while the long
turns it let through were not rich ones. 19% of turns hit a refusal and
those average 6.73 calls against 4.42 for the rest: the long turn is a
resident that could not get the first thing to work.

At 6 the median turn is untouched, a quarter of turns are cut short, and
19% of every call the run makes is never made. The knob was already there
(`KASUKABE_MAX_CALLS_PER_TICK`); what was missing was a number chosen from
a measurement.
"""

from __future__ import annotations

from world.call_budget import MAX_CALLS_PER_TICK, MIN_CALLS_PER_TICK, budget_for


def test_a_turn_is_capped_at_a_handful_of_acts() -> None:
    assert MAX_CALLS_PER_TICK == 6


def test_a_rich_resident_gets_the_ceiling_and_no_more() -> None:
    """Wealth buys runway, not a twelve-call turn."""
    assert budget_for(200_00).calls == MAX_CALLS_PER_TICK


def test_a_resident_with_nothing_still_gets_the_floor() -> None:
    """It is about to fall dormant; taking its last turn away first would
    mean it could not spend that turn asking anybody for help."""
    assert budget_for(0).calls == MIN_CALLS_PER_TICK


def test_the_budget_still_tightens_as_the_wallet_empties() -> None:
    """The mechanism the ceiling sits on top of: 5% of the wallet, so a
    resident down to its last cents gets two calls and lasts, rather than
    six calls and stops."""
    assert budget_for(10) .calls < budget_for(200_00).calls


def test_an_operator_can_still_choose(monkeypatch) -> None:
    monkeypatch.setenv("KASUKABE_MAX_CALLS_PER_TICK", "12")

    assert budget_for(200_00).calls == 12
