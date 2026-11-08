"""Every run so far spent its first nine minutes with the town asleep.

MEASURED, and in both runs of the series independently:

    run2   utterances at ticks 36..43, run ended at 44
    short1 utterances at ticks 36..56, run ended at 56

Tick 36 is not a coincidence. TICK_MINUTES is 10 and DAY_START_HOUR is 6,
so tick 36 is 06:00 exactly - the moment the town wakes. A run starts at
midnight, and `is_sleeping = not timestamp.is_daytime` short-circuits
every resident's turn until dawn.

WHAT THAT DID TO THE MEASUREMENTS. At the default 15 seconds per tick,
those 36 ticks are NINE MINUTES of wall clock. Run 2 was a nine-minute
run. So the whole of its conversation - 21 utterances, one settlement, a
six-speaker gathering - happened in the last hundred seconds, and then it
was graded on how many questions had been answered.

The scheduler wakes each resident every three ticks. A question asked at
tick 40 cannot be answered before tick 43 at the earliest, and run 2
ended at 44. G1 and G2 have therefore never once been measured on a town
that had time to hold a conversation, and no amount of prompt work could
have moved them.

THIS IS NOT A THRESHOLD BEING MOVED. Build Spec v2 section 10.4 forbids
changing a bar after seeing a result, and neither bar moves: G1 stays at
60% and G2 at three exchanges. What changes is that the run now CONTAINS
the thing the bar is about. It is the same class of defect as the
settlement detector that could not see a trade, arriving from the
harness's side rather than the report's.

WHAT IT DOES NOT DO. It does not skip the night's work. The learning
pipeline runs at night by design (DESIGN.md section 1) and a run that
wants to study it starts at midnight, which is still the default. This is
an option for a short diagnostic run, and a long run is unaffected either
way.
"""

from __future__ import annotations

import pytest

from world.core.clock import DAY_START_HOUR, TICK_MINUTES, timestamp_for
from world.run_genesis import DAWN_TICK, build_arg_parser


def test_dawn_is_where_the_measurements_say_it_is() -> None:
    """Both runs' first utterance landed on tick 36 and this is why."""
    assert DAWN_TICK == DAY_START_HOUR * 60 // TICK_MINUTES == 36
    assert timestamp_for(DAWN_TICK).is_daytime
    assert not timestamp_for(DAWN_TICK - 1).is_daytime


def test_the_flag_exists_and_defaults_to_midnight() -> None:
    """Midnight stays the default. The night is real and the learning
    pipeline runs in it; this is an option for a short run, not a change
    to what the world is."""
    parsed = build_arg_parser().parse_args([])
    assert getattr(parsed, "start_at_dawn", False) is False


def test_the_flag_is_accepted() -> None:
    parsed = build_arg_parser().parse_args(["--start-at-dawn"])
    assert parsed.start_at_dawn is True


@pytest.mark.parametrize("tick", [DAWN_TICK, DAWN_TICK + 1, DAWN_TICK + 95])
def test_a_run_starting_at_dawn_is_awake_from_its_first_tick(tick: int) -> None:
    """The whole point: the first tick of the run has to be one on which
    a resident can act, or the run is measuring its own startup."""
    assert timestamp_for(tick).is_daytime


def test_the_town_still_gets_a_night_eventually() -> None:
    """Starting at dawn must not mean permanent daylight - the night is a
    real part of the cycle and a run long enough to reach it should."""
    assert not timestamp_for(DAWN_TICK + 16 * 60 // TICK_MINUTES).is_daytime
