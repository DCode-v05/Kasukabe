"""An alarm nobody can read is not an alarm.

`INFRA-27`. `BrokerService.content_loss` is the list of calls that were
billed for output which did not survive into the record - Build Spec v2
section 7.3, threshold 9.4. It is appended to correctly and read by
nothing: not the runner, not preflight, not the observatory, not
`/admin/trace`. Its own comment says it is "a live alarm for a health
surface to read", and no health surface reads it, so the threshold is
enforced by grepping a process that has already exited.

The fix is one line of surface. It costs nothing and turns a number that
existed only in memory into one an operator, the runner's terminal check,
and the observatory can all see while the run is still going.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from broker.app import create_app
from broker.config import BrokerSettings


def _client(tmp_path) -> TestClient:
    settings = BrokerSettings(
        mock_mode=True,
        trace_sqlite_path=str(tmp_path / "trace.db"),
    )
    return TestClient(create_app(settings))


def test_admin_trace_reports_content_loss(tmp_path) -> None:
    with _client(tmp_path) as client:
        body = client.get("/admin/trace").json()

    assert "content_loss" in body, (
        "INFRA-27: `content_loss` is appended to and exposed nowhere, so "
        f"threshold 9.4 has no live reader. Keys: {sorted(body)}"
    )


def test_it_starts_at_zero_rather_than_absent(tmp_path) -> None:
    """A missing key and a zero count are different answers, and a health
    check that cannot tell them apart reports 'fine' for 'not measured'."""
    with _client(tmp_path) as client:
        body = client.get("/admin/trace").json()

    assert body["content_loss"] == 0
