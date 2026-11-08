"""Residents address each other with a full stop, and the world called it a mention.

MEASURED on run4 (docs/runs/series/run4). Thirteen remarks were spoken to
the room. Nine of them NAME a resident who is standing there. Three
resolved to a listener. Six did not, and they share one shape:

    'Wren Thorne. I have wheat in my hand and words that need saying.'
    'Bram Holloway. I worked all morning and have nothing.'
    'Liora Thorne. You were looking for Perrin Holloway this morning.'
    'Idris Thorne. Corwin Thorne. You fell trees.'

The name is its own sentence. That is how a person gets someone's
attention across a room, and `_VOCATIVE_AFTER` held ",;:!?-" and no full
stop, so every one of these was filed as talk to nobody in particular.

WHAT IT COST. The reply-rate gate counts a remark as answered when the
person addressed later speaks back to the ASKER. A remark addressed to
the room has no addressee, so it can never be answered, can never appear
in that person's open questions, and never puts them under any obligation
at all. G1 has sat between 25% and 45% across four runs while the town
was, in plain English, talking to each other by name.

THE HARD PART, and why the full stop was left out in the first place: a
name followed by a full stop is also just a sentence ending in a name.

    'I saw Greta.'          - not addressed to Greta
    'Greta. Sit down.'      - addressed to Greta

The difference is POSITION. A vocative full stop opens the remark or
follows another sentence; it never closes a clause the name is the object
of. So the full stop counts only when the name STARTS a sentence, which
is exactly the shape all six missed remarks have and none of the false
ones do.

ALSO PINNED HERE: a false positive found in the same measurement.
"You can go find Perrin, or go home to Nell" resolved to Perrin, who is
mentioned rather than addressed - "find Perrin," has a comma after it and
the comma rule alone cannot tell the two apart.
"""

from __future__ import annotations

import pytest

from world.tools.primitives.social import addressee_from_content


class Town:
    """The two things addressee_from_content reads. A real WorldState
    would drag the whole registry in for a pure-text decision."""

    def __init__(self, **names: str) -> None:
        self.agent_names = dict(names)
        self.agents = {agent_id: None for agent_id in names}

    def location_of(self, agent_id: str) -> str:
        return "here"


HERE = Town(
    n01="Bram Holloway", n02="Ainsley Holloway", n03="Tobias Holloway",
    n04="Perrin Holloway", n05="Nell Holloway", n06="Wren Thorne",
    n08="Idris Thorne", n10="Liora Thorne",
)


def spoken(content: str, by: str = "n07") -> str | None:
    return addressee_from_content(HERE, by, "here", content)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Wren Thorne. I have wheat in my hand and words that need saying.", "n06"),
        ("Bram Holloway. I worked all morning and have nothing. Lost timber.", "n01"),
        ("Liora Thorne. You were looking for Perrin Holloway this morning.", "n10"),
        ("Idris Thorne. Corwin Thorne. You fell trees.", "n08"),
        ("Wren. Got nothing in my pack and Ainsley's walked off.", "n06"),
        ("Nell. The oven's cold.", "n05"),
    ],
)
def test_a_name_that_opens_the_remark_is_an_address(content: str, expected: str) -> None:
    assert spoken(content) == expected, f"{content!r} reached nobody"


def test_a_name_after_a_finished_sentence_is_an_address_too() -> None:
    """'I have walked all morning. Bram. Do you have food?' - the name is
    its own sentence, just not the first one."""
    assert spoken("I have walked all morning. Bram. Do you have food?") == "n01"


@pytest.mark.parametrize("content", [
    "I saw Wren Thorne at the mill.",
    "Perrin told me something that doesn't make sense.",
    "Wren Thorne's wheat was the best in the valley.",
    "The marker sits between our land and Bram Holloway's.",
    "I need to find Liora Thorne.",
])
def test_a_mention_is_not_an_address(content: str) -> None:
    """The whole risk of this change. A remark wrongly filed as addressed
    puts a resident under an obligation nobody gave them, and shows up in
    their open questions as something they must answer."""
    assert spoken(content) is None, f"{content!r} was treated as an address"


def test_a_name_mid_clause_with_a_comma_is_still_not_an_address() -> None:
    """The false positive found in the same run4 measurement: "You can go
    find Perrin, or go home to Nell" resolved to Perrin, who is being
    talked ABOUT."""
    assert spoken("I've eaten now. You can go find Perrin, or go home to Nell.") is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Bram, right, so I'm heading to the farmland today.", "n01"),
        ("Now then, Wren. Cold soil and dry.", "n06"),
        ("Ainsley Holloway, Tobias Holloway - this house is out of provisions.", None),
        ("Wren: the field's near ready.", "n06"),
        ("Nell? Are you there?", "n05"),
    ],
)
def test_the_existing_punctuation_still_addresses(content: str, expected) -> None:
    got = spoken(content)
    if expected is None:
        assert got is not None, "a comma-vocative must still resolve to somebody"
    else:
        assert got == expected


def test_speaking_your_own_name_addresses_nobody() -> None:
    assert addressee_from_content(HERE, "n06", "here", "Wren Thorne. I said no.") is None


def test_somebody_who_is_not_here_is_not_addressed() -> None:
    """Speech reaches the room. Naming an absent person is a mention of
    them, whatever the punctuation."""

    class Elsewhere(Town):
        def location_of(self, agent_id: str) -> str:
            return "here" if agent_id == "n02" else "the mill"

    town = Elsewhere(n06="Wren Thorne")
    assert addressee_from_content(town, "n02", "here", "Wren Thorne. Come here.") is None


def test_an_empty_remark_addresses_nobody() -> None:
    assert spoken("") is None
