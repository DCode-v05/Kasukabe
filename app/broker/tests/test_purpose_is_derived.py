"""Every call logged `purpose="unspecified"`, so cost could never be
attributed to what the town was DOING.

The header that carries it is set by nothing: the engine's call site is in
frozen engine_core and `create_llm` offers no seam for a default header.
A $40 run could therefore be broken down by which resident burned it and
never by what for - the dimension a spend report most needs.

It is derived from the request shape now, and named as derived.
"""

from __future__ import annotations

import pytest

from broker.lanes import BACKGROUND_PURPOSE_PREFIXES, CallClass, classify_purpose, derive_purpose


def test_a_declared_purpose_is_never_overwritten() -> None:
    assert derive_purpose("compaction", {"tools": [{"name": "move"}]}) == "compaction"
    assert derive_purpose("learning_centre", {}) == "learning_centre"


def test_an_agent_turn_is_recognised_by_its_tools() -> None:
    """The town's tool schemas ride along with every resident's turn."""
    assert derive_purpose("unspecified", {"tools": [{"name": "move"}]}) == "auto:agent_turn"


def test_a_summarise_pass_is_recognised_by_its_system_prompt() -> None:
    assert derive_purpose("unspecified", {"system": "Summarise the conversation"}) == "auto:compaction"
    blocks = {"system": [{"type": "text", "text": "compact this thread"}]}
    assert derive_purpose("unspecified", blocks) == "auto:compaction"


def test_anything_else_is_labelled_other_not_guessed_at() -> None:
    assert derive_purpose("unspecified", {}) == "auto:other"


@pytest.mark.parametrize("body", [
    {"tools": [{"name": "move"}]},
    {"system": "summarise"},
    {},
])
def test_a_derived_tag_says_it_is_derived(body: dict) -> None:
    """A reader must never mistake a guess for a statement."""
    assert derive_purpose("unspecified", body).startswith("auto:")


def test_a_guess_cannot_reroute_traffic_to_another_lane() -> None:
    """Deliberate: `auto:` tags do not match the background prefixes, so
    deriving a purpose changes what the trace RECORDS and never which lane
    a call is served on. Everything was cognition before this; everything
    is cognition after it. Rerouting real traffic on an inference would be
    a much larger claim than this function is entitled to make.
    """
    for body in ({"tools": [{"name": "x"}]}, {"system": "summarise"}, {}):
        derived = derive_purpose("unspecified", body)
        assert classify_purpose(derived) == CallClass.COGNITION
        assert not any(derived.startswith(p) for p in BACKGROUND_PURPOSE_PREFIXES)


def test_a_long_system_prompt_does_not_stall_the_hot_path() -> None:
    """Prompts reach 150 KB. Only the head is scanned."""
    huge = {"system": "x" * 400_000}
    assert derive_purpose("unspecified", huge) == "auto:other"
