"""The Real-sim button names hosts and services that exist.

`UI-15`. `game/realsim.py` spawned its run with
`--broker-base-url http://broker-real:8200`, and no compose file defines
a `broker-real` service - `docker-compose.yml` has test, world, broker,
observatory and game. On the compose network that hostname does not
resolve, so the one button whose entire purpose is to spend real money on
real inference was wired to a host that is not there.

Second half: `_terminate` called `os.killpg(os.getpgid(pid), ...)` for a
non-container run and caught `ProcessLookupError`/`PermissionError`.
Neither of those is what a MISSING ATTRIBUTE raises, and `os.killpg` and
`os.getpgid` do not exist on Windows - which is where this project is
developed. So stopping a locally-spawned run raised `AttributeError` out
of `stop()` and left the run alive, engines still spending.

This file lives in `app/tests/` because it reads both `game/realsim.py`
and `docker-compose.yml`, which no single package owns.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
COMPOSE = APP / "docker-compose.yml"


def _compose_services() -> set[str]:
    return set(re.findall(r"^  ([a-z][\w-]*):", COMPOSE.read_text(encoding="utf-8"), re.M))


def test_the_broker_it_calls_is_a_service_that_exists() -> None:
    from game.realsim import DEFAULT_BROKER_URL

    host = DEFAULT_BROKER_URL.split("//", 1)[1].split(":", 1)[0]
    services = _compose_services()
    assert host in services, (
        f"UI-15: the run is spawned against {host!r} and docker-compose.yml "
        f"defines {sorted(services)} - the hostname does not resolve, so "
        "every inference call the run makes dies at DNS"
    )


def test_the_service_it_runs_in_exists_too() -> None:
    """The same class of mistake one line further down."""
    source = (APP / "game" / "realsim.py").read_text(encoding="utf-8")
    block = source[source.index('"docker", "compose", "run"'):][:1200]
    named = set(re.findall(r'^\s*"([a-z][\w-]*)",\s*$', block, re.M))
    services = _compose_services()
    assert named & services, (
        f"UI-15: the compose service this runs in is not one of "
        f"{sorted(services)}. Named in the command: {sorted(named)}"
    )


def test_an_override_still_wins() -> None:
    """A deployment that really does run a separate broker must be able to
    say so - the fix must not hard-wire the compose topology."""
    source = (APP / "game" / "realsim.py").read_text(encoding="utf-8")
    assert 'os.environ.get("BROKER_BASE_URL", DEFAULT_BROKER_URL)' in source


# ── stopping a run on the platform this is developed on ────────────────


def test_terminate_survives_a_host_with_no_process_groups() -> None:
    """Windows has no `os.killpg`. The old handler caught
    ProcessLookupError and PermissionError, and a missing attribute is
    neither - so `stop()` raised and the run kept spending."""
    from game.realsim import RealSimController, RunHandle

    controller = RealSimController()
    handle = RunHandle(agents=2, wallet_cents=5, budget_cap_cents=100,
                       until_survivors=1)
    handle.container_name = None

    class _Proc:
        pid = 4242

        def __init__(self) -> None:
            self.terminated = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout=None) -> int:
            return 0

        def kill(self) -> None:
            self.terminated = True

    proc = _Proc()
    handle.process = proc

    controller._terminate(handle)   # must not raise on any platform
    assert proc.terminated, (
        "UI-15: the run was not stopped - on a host with no process groups "
        "the kill path fell through and left the engines running"
    )


@pytest.mark.skipif(hasattr(os, "killpg"), reason="this host has process groups")
def test_this_host_is_the_one_the_bug_was_about() -> None:
    """Recorded rather than assumed: the test above is only evidence on a
    platform where `os.killpg` is genuinely absent."""
    assert not hasattr(os, "killpg")
