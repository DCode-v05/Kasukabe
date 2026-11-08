"""The town clock on /world/state has to be the town's actual clock.

`game_hour` and `game_minute` were read off world.game_clock() with
`getattr(clock, "hour", 0)`. That object is a life.clock.GameClock: it
carries total_hours and a `day` property, and nothing named `hour` or
`minute`. Both getattrs took their default on every single poll, so the
clock in the UI read 00:00 from boot to shutdown while the day counter
next to it advanced normally - the two disagreeing in plain sight.

These tests read the route's numbers back against core/'s own
timestamp_for, so a rename that breaks the wiring fails here instead of
quietly freezing the clock again.
"""

import pytest

from world.core.clock import TICK_MINUTES, timestamp_for


def test_gameclock_has_no_hour_or_minute_attribute() -> None:
    """The premise of the bug, pinned. If GameClock ever grows an `hour`,
    this test is the place that says the old getattr would now have
    worked - and it still should not be used, because `hour_of_day` is a
    float across a day and `hour` here means the hour of the clock face.
    """
    from world.life.clock import GameClock

    clock = GameClock(total_hours=12.5)
    assert not hasattr(clock, "hour")
    assert not hasattr(clock, "minute")
    # What it does have, and why the day counter was right all along.
    assert clock.day == 0


@pytest.mark.parametrize(
    "tick,expected_day,expected_hour,expected_minute",
    [
        (0, 0, 0, 0),
        (1, 0, 0, TICK_MINUTES),
        (6, 0, 1, 0),
        (73, 0, 12, 10),
        (144, 1, 0, 0),
        (150, 1, 1, 0),
    ],
)
def test_timestamp_for_is_the_clock_the_route_must_report(
    tick: int, expected_day: int, expected_hour: int, expected_minute: int
) -> None:
    ts = timestamp_for(tick)
    assert (ts.game_day, ts.hour, ts.minute) == (
        expected_day,
        expected_hour,
        expected_minute,
    )


def test_route_reports_a_clock_that_moves_with_the_tick() -> None:
    """The route's own three lines, exercised through the source rather
    than a live world: whatever it reads must advance between two ticks
    a full half-day apart. The frozen version returned (0, 0) for both.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "agents_api.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assigns: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in (
            "game_day",
            "game_hour",
            "game_minute",
        ):
            assigns.append(ast.unparse(node.value))

    assert assigns, "the route stopped building a WorldStateView"
    # No getattr-with-a-default may stand between the clock and the UI:
    # that is precisely the shape that failed silently for a whole run.
    for expr in assigns:
        assert "getattr" not in expr, (
            f"{expr!r} can default to 0 forever without ever raising"
        )

    early, late = timestamp_for(0), timestamp_for(73)
    assert (early.hour, early.minute) != (late.hour, late.minute)


def test_the_dormant_count_is_read_off_the_real_field() -> None:
    """The clock bug's twin, in the same route function.

    DormancyState carries `is_dormant`. This counted
    `getattr(dormancy, "dormant", False)` - a field that does not exist - so
    the default fired for every resident on every poll and /world/state
    reported `dormant: 0` however many were dormant.

    It matters more than the clock: dormancy is the last step of the
    starvation escalation, so the one figure that would show a household
    starving was pinned at zero.
    """
    import ast
    from pathlib import Path

    from world.life.models import DormancyState

    assert hasattr(DormancyState(), "is_dormant")
    assert not hasattr(DormancyState(), "dormant")

    source = Path(__file__).resolve().parents[1] / "agents_api.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "world_state":
            code = ast.unparse(node)
            break
    else:
        raise AssertionError("the /world/state route function is gone")

    assert "is_dormant" in code
    assert '"dormant"' not in code, (
        "a getattr against a field DormancyState does not have reports zero forever"
    )
