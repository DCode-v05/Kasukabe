"""The one live instrument is actually pointed at the run.

`UI-11`. The observatory was not started by any run script -
`runseries.sh`, `run50.sh` and `start-genesis.sh` all launch a broker and
a world and neither of them it. Its only launcher was docker-compose,
whose `observatory` service read `/data/run.db` while nothing in this
repo has ever written a file by that name: `start-genesis.sh` writes
`/data/kasukabe.db` and the series harness writes `data/<name>.db`.

So on every run the observatory was either absent (run 23: "not
reachable on :8500") or watching a file no run had written, in which case
it reported "no trace database ... has a run written one yet?" - which
reads as "the run has not started" rather than "I am looking in the wrong
place".

A live instrument nobody starts is not an instrument.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
COMPOSE = APP / "docker-compose.yml"
SCRIPTS = ["runseries.sh", "run50.sh"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_run_starts_the_observatory(script: str) -> None:
    source = (APP / script).read_text(encoding="utf-8")
    assert "observatory.app:app" in source, (
        f"UI-11: {script} starts a broker and a world and not the one thing "
        "that watches them, so every run is unobserved"
    )


@pytest.mark.parametrize("script", SCRIPTS)
def test_it_is_pointed_at_this_runs_trace(script: str) -> None:
    """Started but aimed elsewhere is the same as not started - that is
    exactly what compose was doing."""
    source = (APP / script).read_text(encoding="utf-8")
    launch = source[source.index("observatory.app:app") - 400:]
    assert 'TRACE_SQLITE_PATH="$DB"' in launch, (
        f"UI-11: {script} starts the observatory without telling it which "
        "trace store this run writes, so it watches the default and sees "
        "nothing"
    )


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_failed_observatory_does_not_fail_the_run(script: str) -> None:
    """A five-hour run must not die because its dashboard did not bind a
    port - but it must SAY so, because a silently absent instrument is how
    this finding happened."""
    source = (APP / script).read_text(encoding="utf-8")
    launch = source[source.index("observatory.app:app"):]
    assert "did NOT come up" in launch, (
        f"UI-11: {script} starts the observatory and never checks, so an "
        "unobserved run looks exactly like an observed one"
    )
    assert "exit 1" not in launch[:launch.index("QUIET_S") if "QUIET_S" in launch else 1200], (
        "a run must not be killed because its observatory failed to start"
    )


def test_compose_watches_a_file_a_run_writes() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    observatory = compose[compose.index("  observatory:"):]
    observatory = observatory[: observatory.index("\n  game:")]
    match = re.search(r"TRACE_SQLITE_PATH=(\S+)", observatory)
    assert match, "the observatory service sets no trace path at all"

    written = set(
        re.findall(r"TRACE_SQLITE_PATH=(\S+)", (APP / "start-genesis.sh").read_text(encoding="utf-8"))
    )
    assert match.group(1) in written, (
        f"UI-11: compose points the observatory at {match.group(1)} and the "
        f"containerised run writes {sorted(written)} - it watches a file "
        "nothing creates and reports the run as not started"
    )


def test_the_observatory_says_it_is_looking_in_the_wrong_place() -> None:
    """When the trace really is missing, the answer must name the path.
    Without it the operator cannot tell a run that has not started from an
    observatory aimed at the wrong file - the confusion UI-11 is made of."""
    from observatory.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(trace_db_path="/nowhere/no-such-run.db")) as client:
        body = client.get("/api/health").json()

    assert body["available"] is False
    assert "/nowhere/no-such-run.db" in body["why"]
