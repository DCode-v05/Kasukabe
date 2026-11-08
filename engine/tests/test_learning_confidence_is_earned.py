"""1.0 is a category to the learning pipeline, so the prompt must earn it.

`learning_centre/pipeline.py` reads `confidence >= 1.0` as `is_user_stated`
and stores the row as a stated fact, exempt from the 0.8 cap that every
inference gets. The resident knowledge prompt's first draft gave 1.0 to
"something stated plainly to you, or a refusal the world gave you" - nearly
everything a resident experiences - and run 26 stored every one of its 103
knowledge rows as `user_stated` at 1.0. That included rows that are not
knowledge at all:

    "world:find_people with query 'resident who tax collector official
     election' was attempted"

The register was right (0 of 103 rows said "the user"); the certainty was
not. The frozen pipeline cannot be changed, so the prompt is the lever.
"""

from __future__ import annotations

from src.learning_centre import pipeline as frozen_pipeline
from src.sim.resident_learning_prompts import KNOWLEDGE_ENCODING_SYSTEM as KNOWLEDGE


def test_the_pipeline_still_treats_one_point_zero_as_a_stated_fact():
    """If upstream ever stops doing this, the reason for this file is gone
    and the rule below should be re-derived rather than kept by habit."""
    import inspect

    assert "confidence >= 1.0" in inspect.getsource(frozen_pipeline)


def test_one_point_zero_is_reserved_for_what_was_said_word_for_word():
    assert "1.0 is rare" in KNOWLEDGE
    assert "word for word" in KNOWLEDGE


def test_the_old_blanket_rule_is_gone():
    assert "Something stated plainly to you, or a refusal the world gave you: 1.0" not in KNOWLEDGE


def test_a_tool_call_is_not_knowledge():
    assert "was attempted' is not knowledge" in KNOWLEDGE
    assert "Record what it taught you, or nothing." in KNOWLEDGE
