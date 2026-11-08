"""Memory feeder — build_recall_reminder renders the recalled bundle.

The source-registry half of this file went with engine_core/sourcing.py: with
no registry there are no [m1] labels, which is exactly the unlabeled shape the
first test already pinned."""

from src.engine_core.thread import build_recall_reminder

BUNDLE = {
    "episodes": [{"key": "Q3 planning", "description": "launch date set to Aug 14", "id": "ep-1"}],
    "knowledge": [{"content": "Stripe is the payment provider", "id": "kn-1"}],
    "social": [{"name": "Shrivathsan", "id": "sg-1"}],
}


def test_reminder_without_registry_is_unlabeled():
    out = build_recall_reminder(BUNDLE)
    assert "[m1]" not in out
    assert "- Q3 planning: launch date set to Aug 14" in out
    assert "- Stripe is the payment provider" in out
    assert "- Shrivathsan" in out


def test_empty_bundle_returns_none_either_way():
    assert build_recall_reminder({}) is None
    assert build_recall_reminder({}, registry=None) is None
