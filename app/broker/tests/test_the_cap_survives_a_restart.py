"""A restart must not hand the run a fresh budget.

`INFRA-14`. `SpendGovernor.spent_nano_dollars` starts at 0 on every boot,
so a broker restarted mid-run - a crash, an OOM, a deploy, `docker compose
restart` - forgets everything the run has already spent and will happily
spend the whole cap again. The cap is the only thing standing between a
bug and an unbounded Bedrock bill, and it was reset by the most ordinary
event in an operator's day.

The runner's own ledger-based governor is checkpointed and covers the same
line, but only for calls billed through THAT ledger; the broker's cap is
what stops calls the world never sees.

Every spent nano-dollar is already in `llm_calls.cost_nano_dollars` on the
configured store, so the sum is one query at boot against a database the
service has just opened. `since_boot` stays available separately, because
"what has this process spent" is a different question from "what has this
run spent" and an operator needs both.
"""
from __future__ import annotations

import pytest

from broker.app import build_service
from broker.config import BrokerSettings
from broker.trace import TraceRecord, trace_record_to_row
from supabase.config import SupabaseConfig
from supabase.sqlite_client import SqliteClient


@pytest.fixture(autouse=True)
def _no_leaked_aws_profile(monkeypatch) -> None:
    """Another module in this package sets AWS_PROFILE=kasukabe and does
    not put it back, so building real-mode lanes here raises
    ProfileNotFound depending on test ORDER. These tests are about the
    spend governor and have no business touching AWS at all; clearing it
    makes them independent of whoever ran first.
    """
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def _settings(tmp_path) -> BrokerSettings:
    return BrokerSettings(
        # Real mode: mock mode has no durable store, so there is no
        # prior spend to recover and the seeding correctly does nothing.
        mock_mode=False,
        trace_sqlite_path=str(tmp_path / "trace.db"),
        spend_cap_usd=10.0,
    )


def _write_a_prior_spend(settings: BrokerSettings, nano: int) -> None:
    """A row written through the broker's OWN serialiser.

    Not a hand-built dict: `trace_record_to_row` is what production uses,
    so a column added or renamed there breaks this fixture rather than
    letting it drift into testing a shape nobody writes.
    """
    record = TraceRecord(
        id="prior-run-call",
        agent_id="n01",
        payer_agent_id="n01",
        model="moonshotai.kimi-k2.5",
        requested_model="moonshotai.kimi-k2.5",
        region="ap-south-1",
        call_class="cognition",
        purpose="turn",
        request_body="{}",
        response_body="{}",
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=10,
        cost_nano_dollars=nano,
        cents_debited=0,
        latency_ms=1,
        rate_limit_wait_ms=0,
        success=True,
        error_type=None,
        error_message=None,
        created_at="2026-09-05T00:00:00Z",
    )
    client = SqliteClient(SupabaseConfig(sqlite_path=settings.trace_sqlite_path))
    client.ensure_schema()
    client.insert_sync("llm_calls", [trace_record_to_row(record)])


def test_a_restart_remembers_what_the_run_already_spent(tmp_path) -> None:
    settings = _settings(tmp_path)
    _write_a_prior_spend(settings, nano=4_000_000_000)   # $4 of a $10 cap

    service = build_service(settings)

    assert service.spend_governor.spent_nano_dollars == 4_000_000_000, (
        "INFRA-14: the governor booted at zero, so a restarted broker will "
        "spend the whole cap a second time"
    )


def test_a_fresh_store_still_starts_at_zero(tmp_path) -> None:
    """The other half: seeding must not invent spend that never happened."""
    service = build_service(_settings(tmp_path))
    assert service.spend_governor.spent_nano_dollars == 0


def test_this_process_reports_its_own_spend_separately(tmp_path) -> None:
    """`since_boot` is what tells an operator whether THIS process is
    burning, which the seeded total would otherwise hide."""
    settings = _settings(tmp_path)
    _write_a_prior_spend(settings, nano=4_000_000_000)

    service = build_service(settings)
    snapshot = service.spend_governor.snapshot()

    assert snapshot.spent_usd >= 4.0
    assert getattr(snapshot, "spent_since_boot_usd", None) == 0.0
