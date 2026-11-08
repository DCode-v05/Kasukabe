"""A6: the resident is told what its own thinking costs.

From docs/kasukabetoolspec.md A6, verbatim on the point: the block is
"computed from cost_disclosure.py, which already reads usd_balance and
computes a burn rate and runway **and is rendered nowhere**."

That is exactly the state. `gather_wallet_and_burn` and
`estimate_ticks_until_dormancy` are written, tested, and read by no
prompt, so a resident has never once been told how long its money lasts.
The spec's reason for wanting it is not comfort: *"The runway figure is
what makes `buy_energy` a verb with a felt need behind it"* - a verb that
converts money into thinking is a curiosity to somebody who does not know
its thinking is running out.

The shape the spec asks for:

    Energy: $1.42 in your wallet. Your last turn cost $0.011. At this
    morning's rate that is about 5.3 game-days. Money: 478 sim. Owed
    today: upkeep 14, head tax 6.

Facts only. The spec says so twice - "No advice about thinking less" -
and A6's second test is named for it. This is the same discipline
`standing_brief` already holds: state the world, never the strategy.
"""

from __future__ import annotations

from world.retrieval_depth import format_volatile_line


def test_the_volatile_line_matches_the_ledger_to_the_cent() -> None:
    """Every figure is the ledger's own, printed, not recomputed. A line
    that rounds a wallet is a line a resident cannot plan against."""
    line = format_volatile_line({
        "usd_wallet_cents": 142,
        "sim_balance": 478,
        "last_turn_usd_cents": 1.1,
        "runway_game_days": 5.3,
    })
    assert line is not None
    assert "Energy: 1,420." in line
    assert "478 sim" in line
    assert "5.3" in line


def test_the_volatile_line_gives_no_advice() -> None:
    """The spec's own second test. A line that says "think less" would
    make every measurement of frugality a measurement of this sentence."""
    line = format_volatile_line({
        "usd_wallet_cents": 12, "sim_balance": 3,
        "last_turn_usd_cents": 1.4, "runway_game_days": 0.1,
    })
    assert line is not None
    banned = ("should", "must", "try to", "consider", "careful", "save", "spend less", "cheaper")
    lowered = line.lower()
    for word in banned:
        assert word not in lowered, f"the volatile line advises: {word!r} in {line!r}"


def test_an_unknown_runway_is_said_and_not_guessed() -> None:
    """`estimate_ticks_until_dormancy` returns None when there is no burn
    to project from, and its docstring is explicit that "unknown" is the
    honest answer rather than "forever". The line must not invent one."""
    line = format_volatile_line({
        "usd_wallet_cents": 200, "sim_balance": 100,
        "last_turn_usd_cents": 0.0, "runway_game_days": None,
    })
    assert line is not None
    assert "not yet known" in line.lower() or "unknown" in line.lower()
    assert "forever" not in line.lower()


def test_what_is_owed_today_is_shown_when_there_is_something_owed() -> None:
    line = format_volatile_line({
        "usd_wallet_cents": 142, "sim_balance": 478,
        "last_turn_usd_cents": 1.1, "runway_game_days": 5.3,
        "upkeep_due_sim": 14, "head_tax_due_sim": 6,
    })
    assert "upkeep 14" in line and "head tax 6" in line


def test_nothing_owed_is_simply_not_mentioned() -> None:
    """A run that has never crossed a day boundary owes nothing, and
    printing "upkeep 0, head tax 0" every tick would teach residents that
    the words mean nothing."""
    line = format_volatile_line({
        "usd_wallet_cents": 142, "sim_balance": 478,
        "last_turn_usd_cents": 1.1, "runway_game_days": 5.3,
    })
    assert "upkeep" not in line.lower()


def test_no_wallet_information_produces_no_line_at_all() -> None:
    """Better silent than a line of dashes: a resident reading "Energy: $—"
    learns less than one reading nothing."""
    assert format_volatile_line(None) is None
    assert format_volatile_line({}) is None


def test_the_line_is_rendered_into_the_perception_a_resident_sees() -> None:
    """The whole finding is that this arithmetic exists and reaches no
    prompt. A formatter nobody calls repeats it."""
    import inspect

    from world import retrieval_depth

    source = inspect.getsource(retrieval_depth.render_perception_text)
    assert "format_volatile_line" in source, (
        "the volatile line is computed and still reaches no resident"
    )
