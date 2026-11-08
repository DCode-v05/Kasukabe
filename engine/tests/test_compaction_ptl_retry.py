import os
import pytest
from pathlib import Path

from src.config import Settings
from src.herald import create_llm
from src.herald.types import Message
from src.engine_core.context_engineering import (
    summarize_old_messages,
    compact_messages_with_summary,
    PRESERVE_RECENT,
)
from src.engine_core.events import (
    CompactionPtlRetryEvent,
    CompactionPtlFailedEvent,
)

LIVE = bool(os.environ.get("CDT_URL")) and bool(os.environ.get("CDT_KEY"))
pytestmark = pytest.mark.skipif(
    not LIVE, reason="CDT_URL/CDT_KEY not set — live PTL retry tests skipped"
)


def live_settings() -> Settings:
    settings = Settings.load()
    settings.sqlite_db_path = ":memory:"
    settings.catalog_dir = str(Path(__file__).parent.parent / "catalog")
    return settings


def make_bulky_messages(total_target_tokens: int, rounds: int = 20) -> list[Message]:
    """Create a conversation of approximately total_target_tokens.

    Each round has one user message and one assistant message. Content is
    padded with filler chars so token estimation (chars/4 * 1.2) lands
    close to the target.
    """
    chars_per_msg = (total_target_tokens * 4) // (rounds * 2)
    msgs = []
    for i in range(rounds):
        msgs.append(
            Message(
                role="user",
                content=f"Round {i}: user asks about topic {i}. " + "x" * chars_per_msg,
                timestamp=float(i),
            )
        )
        msgs.append(
            Message(
                role="assistant",
                content=f"Round {i}: assistant explains topic {i}. " + "y" * chars_per_msg,
                timestamp=float(i) + 0.5,
            )
        )
    return msgs


class TestPtlRetryPreCheckWithRealLLM:
    """Pre-check overflow triggers retry, eventually LLM call succeeds."""

    @pytest.mark.asyncio
    async def test_precheck_retry_produces_summary(self):
        """Messages exceed context_window on estimate. Pre-check fires, drops
        rounds, eventually messages fit, real LLM call produces a summary."""
        settings = live_settings()
        adapter = create_llm(settings)

        msgs = make_bulky_messages(total_target_tokens=50_000, rounds=20)

        events = []

        async def capture(evt):
            events.append(evt)

        summary, recent = await summarize_old_messages(
            msgs,
            adapter,
            preserve_recent=PRESERVE_RECENT,
            context_window=20_000,
            on_ptl_event=capture,
        )

        assert isinstance(summary, str)
        assert isinstance(recent, list)
        assert len(recent) == PRESERVE_RECENT

        retry_events = [e for e in events if isinstance(e, CompactionPtlRetryEvent)]
        assert len(retry_events) >= 1, "Pre-check should trigger at least one retry"
        assert retry_events[0].trigger == "pre_check"

        if summary:
            assert len(summary) > 50, "Non-empty summary should have real content"

    @pytest.mark.asyncio
    async def test_full_compact_with_summary_ptl_retry(self):
        """End-to-end through compact_messages_with_summary: PTL retry fires
        inside, final output is shorter than input."""
        settings = live_settings()
        adapter = create_llm(settings)

        msgs = make_bulky_messages(total_target_tokens=50_000, rounds=20)

        events = []

        async def capture(evt):
            events.append(evt)

        result = await compact_messages_with_summary(
            msgs,
            adapter,
            context_window=20_000,
            on_ptl_event=capture,
        )

        assert isinstance(result, list)
        assert len(result) < len(msgs), "Compaction should reduce message count"

        retry_events = [e for e in events if isinstance(e, CompactionPtlRetryEvent)]
        assert len(retry_events) >= 1


class TestPtlRetryFeatureFlagWithRealLLM:
    """Feature flag integration with real adapter."""

    @pytest.mark.asyncio
    async def test_flag_disabled_skips_retry(self, monkeypatch):
        monkeypatch.setenv("FEATURE_COMPACTION_PTL_RETRY", "false")
        settings = live_settings()
        adapter = create_llm(settings)

        msgs = make_bulky_messages(total_target_tokens=50_000, rounds=20)

        events = []

        async def capture(evt):
            events.append(evt)

        summary, recent = await summarize_old_messages(
            msgs,
            adapter,
            preserve_recent=PRESERVE_RECENT,
            context_window=5000,
            on_ptl_event=capture,
        )

        assert summary == ""
        retry_events = [e for e in events if isinstance(e, CompactionPtlRetryEvent)]
        assert len(retry_events) == 0, "No retries when flag is disabled"

        failed_events = [e for e in events if isinstance(e, CompactionPtlFailedEvent)]
        assert len(failed_events) >= 1
