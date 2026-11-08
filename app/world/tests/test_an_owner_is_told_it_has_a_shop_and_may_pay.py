"""An owner is told it owns a shop, and that an employer may pay a wage.

TWO FINDINGS, one line apart.

The first: `run_genesis.ledger_of_self` has built `my_business` - the till,
the stock and the staff - on every tick since CORE-16, whose own comment says
it exists because "a shop owner was told nothing about their own till, stock
or staff, so 'run your business' meant guessing at the state of it". Nothing
ever read the key. It was computed every tick for every owner and dropped on
the floor, so the owner went on guessing.

The second: election30 set 0 wages and paid 0. Not refused - `set_wage` and
`pay_wage` are free, gated only to the premises, and were never called once,
while the construction crew talked among themselves about "today's wages"
that nothing paid. 29 of 30 residents ended poorer than they started.

The precedent for the fix is GOV-3's. A seat's verbs were in the winner's
schema all along and nothing connected the seat to them, until the
seat-holder's own perception named them - after which the 25 September run
cut the head tax at tick 118, the first government verb a resident ever
called. Nine words.

WHAT IT DOES NOT DO. It says what an employer MAY do, never that it should.
Same rule as the seat notice and the reason `vote` has never been worded as
an instruction: if a wage gets paid, it has to be because a resident decided
to pay one.
"""

from __future__ import annotations

from world.retrieval_depth import format_standing_brief

OWNER = {
    "name": "Giselle Ingram",
    "occupation": "builder",
    "sim_balance": 4200,
    "my_business": [
        {
            "business_id": "b_construction",
            "name": "Kasukabe Construction",
            "till_sim": 20000,
            "stock": {"timber": 60, "planks": 24},
            "staff": ["Bram Holloway", "Arjun Holloway"],
        }
    ],
}


def test_an_owner_is_told_which_shop_is_theirs() -> None:
    said = format_standing_brief(OWNER)

    assert "Kasukabe Construction" in said
    assert "b_construction" in said, "the id is what every shop verb takes"


def test_the_till_and_the_shelves_are_named() -> None:
    """The exact things CORE-16 computed and nobody rendered."""
    said = format_standing_brief(OWNER)

    assert "20000 sim" in said
    assert "60 timber" in said and "24 planks" in said


def test_the_staff_are_named() -> None:
    said = format_standing_brief(OWNER)

    assert "Bram Holloway" in said and "Arjun Holloway" in said
    assert "work for you" in said


def test_an_employer_is_told_the_wage_verbs_exist() -> None:
    """The GOV-3 move, applied to employment."""
    said = format_standing_brief(OWNER)

    assert "`set_wage`" in said
    assert "`pay_wage`" in said
    assert "premises" in said, "a verb nobody can reach is not an affordance"


def test_it_says_may_and_never_says_should() -> None:
    """If a wage is paid it has to be a decision, not an instruction
    followed - otherwise the run proves nothing about what residents do."""
    said = format_standing_brief(OWNER).lower()

    assert "may `set_wage`" in format_standing_brief(OWNER)
    for nudge in ("you should", "make sure", "remember to", "you must", "don't forget"):
        assert nudge not in said, f"the brief told the owner what to do: {nudge!r}"


def test_an_owner_with_no_staff_is_not_told_about_wages() -> None:
    """Nobody to pay. A verb named where it cannot apply is noise, and this
    brief is read every tick by every owner in the town."""
    said = format_standing_brief(
        {"name": "Marta", "sim_balance": 10,
         "my_business": [{"business_id": "b_mill", "name": "The Mill", "till_sim": 300}]}
    )

    assert "The Mill" in said
    assert "set_wage" not in said


def test_one_employee_reads_as_one_person() -> None:
    said = format_standing_brief(
        {"name": "Marta", "sim_balance": 10,
         "my_business": [{"business_id": "b_mill", "name": "The Mill",
                          "staff": ["Bram Holloway"]}]}
    )

    assert "Bram Holloway works for you." in said


def test_somebody_who_owns_nothing_reads_exactly_as_before() -> None:
    """The brief is every resident's, not just an owner's."""
    said = format_standing_brief({"name": "Kenji Jarrah", "sim_balance": 300})

    assert said == "You are Kenji Jarrah.\nYou have 300 sim in hand."


def test_two_shops_are_both_reported() -> None:
    said = format_standing_brief(
        {"name": "Giselle", "my_business": [
            {"business_id": "b_one", "name": "One", "staff": ["A"]},
            {"business_id": "b_two", "name": "Two"},
        ]}
    )

    assert "One" in said and "Two" in said
    assert said.count("set_wage") == 1, "only the shop with staff needs the verbs"
