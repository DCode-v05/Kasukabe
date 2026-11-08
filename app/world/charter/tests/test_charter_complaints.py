"""Tests for world/charter/complaints.py: the immutable complaint ledger."""

from __future__ import annotations

import pytest

from world.charter.complaints import ComplaintLedger
from world.charter.events import EVENT_COMPLAINT_FILED
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder


@pytest.mark.asyncio
async def test_file_complaint_against_government_official() -> None:
    recorder = EventRecorder()
    store = CharterStore()
    ledger = ComplaintLedger(store, event_sink=recorder.sink)

    entry = await ledger.file_complaint(
        "agent-1", "pm", "government", "abused the treasury", now_tick=10
    )
    assert entry.ledger_index == 0
    assert entry.against_kind == "government"
    assert recorder.of_type(EVENT_COMPLAINT_FILED)


@pytest.mark.asyncio
async def test_complaint_filing_is_open_to_any_agent_no_gating() -> None:
    # No location argument, no sitting-government requirement: filing a
    # complaint against the government cannot itself be gated by the
    # government (module docstring's rationale).
    store = CharterStore()
    ledger = ComplaintLedger(store)
    entry = await ledger.file_complaint("random-agent", "minister-1", "government", "d", now_tick=0)
    assert entry.complainant_agent_id == "random-agent"


@pytest.mark.asyncio
async def test_list_all_preserves_filing_order() -> None:
    store = CharterStore()
    ledger = ComplaintLedger(store)
    await ledger.file_complaint("a1", "gov", "government", "first", now_tick=0)
    await ledger.file_complaint("a2", "gov", "government", "second", now_tick=1)
    await ledger.file_complaint("a3", "gov", "government", "third", now_tick=2)

    entries = await ledger.list_all()
    assert [e.description for e in entries] == ["first", "second", "third"]
    assert [e.ledger_index for e in entries] == [0, 1, 2]


def test_ledger_has_no_edit_or_delete_api() -> None:
    # The immutability guarantee is the absence of these methods, not a
    # runtime check: there is nothing to call to mutate or remove an
    # existing entry, on either the engine or the store underneath it.
    for forbidden in ("update_complaint", "edit_complaint", "delete_complaint", "remove_complaint"):
        assert not hasattr(ComplaintLedger, forbidden)
        assert not hasattr(CharterStore, forbidden)
