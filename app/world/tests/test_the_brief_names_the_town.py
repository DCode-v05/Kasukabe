"""An agent has to be told who exists before it can talk to them.

The standing brief named an agent its own trade, its workplace, its food
and its balance, and never once said who shared the town with it. So a
resident looking for a buyer reached for whatever names were within
reach - and the only names within reach were the ones written on the
map's buildings: "Carlos Gomez's apartment", "Harvey Oak Supply Store",
"Hobbs Cafe". Those belong to a cast of thirty who are not in this run.

Observed live on the 10-resident cast: Tobias hailing "Harvey" and
"Isabella", Bram walking to "sh_silva". No such people, so the words
reached nobody and the walk ended nowhere - which is exactly the
"characters talking to characters not in the sim" this fixes.
"""

from __future__ import annotations

from world.retrieval_depth import format_standing_brief

BRIEF = {
    "name": "Bram Holloway",
    "occupation": "miner",
    "location_id": "Kasukabe Ore Outcrop",
    "workplace": "Kasukabe Ore Outcrop",
    "food_units": 0,
    "sim_balance": 2242,
    "household": ["Ainsley Holloway (n02), trader", "Perrin Holloway (n04), student"],
    "townspeople": ["Wren Thorne (n06), farmer", "Marta Thorne (n07), baker"],
}


def test_the_brief_names_the_household() -> None:
    text = format_standing_brief(BRIEF)
    assert "Ainsley Holloway (n02), trader" in text
    assert "Perrin Holloway (n04), student" in text


def test_the_brief_names_the_rest_of_the_town() -> None:
    text = format_standing_brief(BRIEF)
    assert "Wren Thorne (n06), farmer" in text
    assert "Marta Thorne (n07), baker" in text


def test_it_says_the_roster_is_the_whole_town() -> None:
    """Naming nine people is not the same as saying there is no tenth.
    The closing clause is what makes an invented name obviously invented.
    """
    text = format_standing_brief(BRIEF)
    assert "only other people" in text


def test_household_and_town_are_told_apart() -> None:
    """Who you live with is not the same as who else exists."""
    text = format_standing_brief(BRIEF)
    assert text.index("Your household:") < text.index("The rest of the town:")
    household_line = [ln for ln in text.split("\n") if ln.startswith("Your household:")][0]
    assert "Wren Thorne" not in household_line


def test_it_does_not_leak_where_everybody_is() -> None:
    """A roster that carried positions would hand every agent a live map of
    the whole town for free. Perception and `look_around` are what report
    who is actually present."""
    text = format_standing_brief(BRIEF)
    assert "Kasukabe Farmland" not in text
    assert text.count("Kasukabe Ore Outcrop") <= 1


def test_a_brief_with_nobody_else_says_nothing_about_a_roster() -> None:
    """A one-resident world must not print an empty list."""
    alone = {k: v for k, v in BRIEF.items() if k not in ("household", "townspeople")}
    text = format_standing_brief(alone)
    assert "Your household:" not in text
    assert "The rest of the town:" not in text


def test_the_runner_builds_both_lists() -> None:
    import inspect

    from world.run_genesis import GenesisRunner

    source = inspect.getsource(GenesisRunner.standing_brief)
    assert '"household"' in source and '"townspeople"' in source


def test_the_dead_are_not_on_the_roster() -> None:
    import inspect

    from world.run_genesis import GenesisRunner

    source = inspect.getsource(GenesisRunner.standing_brief)
    assert 'getattr(other, "alive", True)' in source
