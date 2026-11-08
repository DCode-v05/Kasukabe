"""Declaring a refusal is not the same as silencing it.

`proof/deliberate.py` lets this project say "yes, that tightening was on
purpose" about a recorded call shape the registry now refuses. That is a
useful thing to be able to say and a dangerous one: a list that turns red
rows green on request is how a gate stops being read.

So the declaration is checked against the refusal it claims to explain. The
parameter named in the entry has to be the parameter the town actually
complains about. If a verb tightens somewhere else tomorrow, the sentence
changes, the match fails, and the row is a failure again - which is exactly
what should happen to a decision that has quietly moved.
"""

from __future__ import annotations

from proof.deliberate import DECLARED, declared_reason


def test_a_declared_refusal_that_matches_is_explained() -> None:
    reason = declared_reason(
        "look_around(query)",
        "ToolValidationError: look_around: invalid params: no such parameter 'query'.",
    )

    assert reason and "search" in reason


def test_a_refusal_about_something_else_is_not_explained() -> None:
    """The verb tightened elsewhere; the old declaration must not cover it."""
    reason = declared_reason(
        "look_around(query)",
        "ToolValidationError: look_around: invalid params: no such parameter 'radius'.",
    )

    assert reason is None


def test_a_shape_nobody_declared_is_not_explained() -> None:
    assert declared_reason("harvest(field_id)", "no such parameter 'field_id'") is None


def test_every_declaration_says_which_parameter_and_why() -> None:
    for signature, (parameter, reason) in DECLARED.items():
        assert parameter, signature
        assert parameter in signature, f"{signature} declares {parameter!r}, which it does not contain"
        assert len(reason) > 60, f"{signature}'s reason is too thin to be a decision"


def test_nothing_foldable_is_declared_instead_of_folded() -> None:
    """A wrong word belongs in SYNONYMS, where it helps the next live run.

    With one exception, and it is a real one: a call that names the same
    thing twice - `describe_place(location_id, place_id)` - cannot be fixed
    by folding, because the fold has nowhere to land and the two words may
    disagree. So the rule is: a declared parameter must not be foldable
    UNLESS its target is already in the same call.
    """
    from world.tools.param_repair import SYNONYMS

    for signature in DECLARED:
        verb, _, rest = signature.partition("(")
        named = {p.strip() for p in rest.rstrip(")").split(",")}
        parameter = DECLARED[signature][0]
        target = SYNONYMS.get(verb, {}).get(parameter)
        if target is None:
            continue
        assert target in named, (
            f"{verb}.{parameter} folds to {target!r} and is declared anyway - "
            "folding is better, because it helps the next live run"
        )
