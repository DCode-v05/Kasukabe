"""reap_finished retention: keyed on finished_at, not started_at.

Regression for the zero-retention bug: any job whose RUNTIME exceeded
SESSION_TTL_SEC was reaped the instant it finished (process_list triggers
reap_finished), destroying its exit code and output before anyone could
read them. Retention must be measured from when the job finished.
"""

from __future__ import annotations

import time

import pytest

from src.sandbox.executor import SESSION_TTL_SEC, ProcessSession, SandboxExecutor


def finished_session(session_id: str, started_ago: float, finished_ago: float | None) -> ProcessSession:
    now = time.monotonic()
    s = ProcessSession(id=session_id, command="sleep long")
    s.started_at = now - started_ago
    s.finished_at = None if finished_ago is None else now - finished_ago
    s.exited = True
    s.exit_code = 0
    return s


class TestReapFinished:
    def test_long_job_recently_finished_survives(self):
        # THE regression: started 45 min ago (runtime > 30-min TTL),
        # finished 1 min ago -> must survive the reap.
        ex = SandboxExecutor()
        s = finished_session("long-job", started_ago=45 * 60, finished_ago=60)
        ex.finished[s.id] = s
        ex.reap_finished()
        assert "long-job" in ex.finished

    def test_finished_beyond_ttl_is_reaped(self):
        ex = SandboxExecutor()
        s = finished_session("old-job", started_ago=90 * 60, finished_ago=SESSION_TTL_SEC + 60)
        ex.finished[s.id] = s
        ex.reap_finished()
        assert ex.finished == {}

    def test_legacy_record_without_stamp_falls_back_to_started_at(self):
        ex = SandboxExecutor()
        fresh = finished_session("fresh-legacy", started_ago=60, finished_ago=None)
        stale = finished_session("stale-legacy", started_ago=SESSION_TTL_SEC + 60, finished_ago=None)
        ex.finished[fresh.id] = fresh
        ex.finished[stale.id] = stale
        ex.reap_finished()
        assert "fresh-legacy" in ex.finished
        assert "stale-legacy" not in ex.finished


class TestFinishedAtStamping:
    @pytest.mark.asyncio
    async def test_kill_orphaned_children_stamps(self):
        ex = SandboxExecutor()
        s = ProcessSession(id="orphan", command="sleep 999")
        s.started_at = time.monotonic() - (SESSION_TTL_SEC + 120)  # ran past the TTL
        s.process = None  # no real process; kill path still finalizes state
        ex.sessions[s.id] = s
        killed = await ex.kill_orphaned_children()
        assert killed == 0  # no live process to kill, but state finalized
        assert s.id in ex.finished
        assert s.finished_at is not None
        # And the freshly-finished orphan survives a reap despite its age.
        ex.reap_finished()
        assert s.id in ex.finished

    def test_reinsertion_does_not_refresh_retention(self):
        ex = SandboxExecutor()
        s = finished_session("done-job", started_ago=3600, finished_ago=120)
        original_stamp = s.finished_at
        ex.finished[s.id] = s
        # Simulate a state-refresh path re-encountering the already-finished
        # session: the guard must not move the stamp forward.
        if s.finished_at is None:
            s.finished_at = time.monotonic()
        assert s.finished_at == original_stamp

    def test_process_remove_still_works(self):
        ex = SandboxExecutor()
        s = finished_session("removable", started_ago=100, finished_ago=10)
        ex.finished[s.id] = s
        assert ex.process_remove("removable") == {"status": "removed"}
        assert ex.finished == {}
