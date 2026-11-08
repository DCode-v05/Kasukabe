"""Shared test fixtures for the charter test suite."""

from __future__ import annotations

from typing import Any

import pytest


class EventRecorder:
    """An `EventSink` that remembers everything it was given, so tests can
    assert on emitted events without a real Supabase or Realtime channel.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def sink(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


@pytest.fixture
def recorder() -> EventRecorder:
    return EventRecorder()
