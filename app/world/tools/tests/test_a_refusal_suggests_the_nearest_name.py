"""A refusal that lists a vocabulary should also point at the near miss.

Doc 01 (`docs/review/01-run-23-evaluation.md`) fix 5: "item names should
fuzzy-match". It is the last unclosed half of run 23's largest refusal
family. The vocabulary is already printed - `NotFoundError` lists the
businesses, `RecipeNotFoundError` lists the recipes - and a resident that
guessed `b_bakery_shop` still has to scan twelve ids and work out which
one it meant, on a turn it has already paid for.

The distinction this file holds: the refusal SUGGESTS, it never resolves.
`TOOL-01`'s own note in FINDINGS-REGISTER.md is the reason -

    "Folding at the tool boundary is necessary and not sufficient ... the
    resolved id has to reach every one of them or the refusal moves one
    line down."

- so a near miss that silently became a hit would move the failure
somewhere harder to see. Naming it is free and cannot be wrong; acting on
it is the resident's call.
"""
from __future__ import annotations

from world.tools.errors import (
    NotFoundError,
    RecipeNotFoundError,
    nearest_name,
)


class TestTheHelperItself:
    def test_it_finds_an_obvious_typo(self) -> None:
        assert nearest_name("b_bakerry", ["b_bakery", "b_mill"]) == "b_bakery"

    def test_it_finds_a_prefixed_guess(self) -> None:
        assert nearest_name("bakery", ["b_bakery", "b_quarry"]) == "b_bakery"

    def test_it_declines_when_nothing_is_close(self) -> None:
        """Silence beats a wrong steer. A resident told 'did you mean
        b_mill?' when it asked for a smelter has been sent further away
        than one told nothing."""
        assert nearest_name("smelter", ["b_bakery", "b_quarry"]) is None

    def test_it_declines_on_an_empty_vocabulary(self) -> None:
        assert nearest_name("anything", []) is None

    def test_it_never_suggests_the_thing_that_was_asked_for(self) -> None:
        """If the id were in the list the caller would not be raising."""
        assert nearest_name("b_mill", ["b_mill"]) is None


class TestTheRefusalsCarryIt:
    def test_a_missing_business_names_the_near_miss(self) -> None:
        message = str(NotFoundError("business", "b_bakerry", ["b_bakery", "b_mill"]))
        assert "did you mean 'b_bakery'" in message

    def test_it_still_lists_the_whole_vocabulary(self) -> None:
        """The suggestion is added, not substituted. A wrong guess about
        which one was meant must not cost the resident the list."""
        message = str(NotFoundError("business", "b_bakerry", ["b_bakery", "b_mill"]))
        assert "b_mill" in message

    def test_a_missing_recipe_names_the_near_miss(self) -> None:
        message = str(RecipeNotFoundError("mill_flour", ["mill_flour_from_wheat", "bake_bread"]))
        assert "did you mean 'mill_flour_from_wheat'" in message

    def test_a_wild_guess_gets_the_list_and_no_suggestion(self) -> None:
        message = str(NotFoundError("business", "government_resource_buyer", ["b_bakery", "b_mill"]))
        assert "did you mean" not in message
        assert "b_bakery" in message

    def test_a_hint_refusal_is_left_alone(self) -> None:
        """`hint` REPLACES the generated sentence by contract; a suggestion
        appended to it would be this class overruling the handler that
        knew better."""
        message = str(
            NotFoundError("business", "b_x", ["b_bakery"], hint="Sell at a shop, to a person.")
        )
        assert "did you mean" not in message
