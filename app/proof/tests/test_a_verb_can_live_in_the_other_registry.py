"""A verb the world registry does not serve is not therefore gone.

`proof` looked in one registry and said of `recall_memories`,
`recall_session_log` and `search_memory` that "the registry no longer serves
this verb" - nine rows of UNPROVEN, six in replay and three in reach, over
59 recorded calls that a real model made and the world answered.

All three are alive. They are registered ENGINE-side, in
`engine/src/sim/pinned_boot.py`, onto the same registry the `world:*` tools
reach the model through - `world/tools/repeated_calls.py` says so in its own
comment. An instrument that reports a live verb as withdrawn is the exact
failure this package was built to prevent, pointed the other way: not a green
tick for something that never ran, but a shrug at something that runs fine.

The resolution executes the real builders in the engine's own interpreter and
reads the schema they return. It does not read source text, and it does not
guess: when the engine cannot be reached the verdict stays UNPROVEN and says
that is why.
"""

from __future__ import annotations

import pytest

from proof.engine_tools import ENGINE_SIDE_VERBS, engine_tool_schemas, shape_fits

pytestmark = pytest.mark.skipif(
    engine_tool_schemas() is None,
    reason="the engine's interpreter is not available on this machine",
)


def test_the_three_memory_verbs_are_served_engine_side() -> None:
    schemas = engine_tool_schemas()

    assert set(schemas) == set(ENGINE_SIDE_VERBS), (
        "pinned_boot registers exactly these three onto the model's registry; "
        f"got {sorted(schemas)}"
    )


def test_the_shapes_the_corpus_recorded_still_fit() -> None:
    """The six replay rows, as the corpus actually spelled them."""
    schemas = engine_tool_schemas()

    for verb, params in (
        ("recall_memories", ()),
        ("recall_memories", ("count",)),
        ("recall_memories", ("count", "kind")),
        ("recall_memories", ("kind",)),
        ("recall_session_log", ()),
        ("recall_session_log", ("turns",)),
        ("search_memory", ("query",)),
    ):
        assert shape_fits(schemas[verb], params) is None, f"{verb}({', '.join(params)})"


def test_a_parameter_the_engine_does_not_take_is_refused() -> None:
    """The check has to be able to say no, or it is not a check."""
    schemas = engine_tool_schemas()

    why = shape_fits(schemas["recall_session_log"], ("turns", "colour"))

    assert why and "colour" in why


def test_a_required_parameter_that_is_missing_is_refused() -> None:
    schemas = engine_tool_schemas()

    why = shape_fits(schemas["search_memory"], ())

    assert why and "query" in why
