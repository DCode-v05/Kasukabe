"""Importing the broker does not open the production trace file.

`EVAL-17` / `INFRA-11` - the same defect written up from two directions.

`broker/app.py` ended with `app = create_app()`, which runs at IMPORT.
`create_app` builds a real `SqliteClient` on
`BrokerSettings.trace_sqlite_path`, and that defaults to
`/data/kasukabe.db`. So on any machine without a `/data` directory -
which is every developer's, and this one - `import broker.app` raised.
`broker/tests/test_broker_app.py` imports it, so pytest reported
"Interrupted: 1 error during collection" and the ENTIRE app suite ran
zero tests.

Setting `TRACE_SQLITE_PATH` to something relative swapped one failure for
another: collecting the tests then created `app/data/` inside the
checkout. A test run that writes to the working tree just by being
collected is a test run that cannot be trusted about anything else.

PEP 562's module `__getattr__` keeps `broker.app:app` resolving for
uvicorn while making the import inert. That form was chosen over
`--factory` because four launchers name `broker.app:app` - docker-compose,
the systemd unit, `run50.sh` and `runseries.sh` - and a flag would have
had to reach all four.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]


def _import_in_a_clean_process(**env_overrides: str) -> subprocess.CompletedProcess:
    """A fresh interpreter, so this measures IMPORT rather than whatever
    the test session has already built."""
    env = {k: v for k, v in os.environ.items() if k != "TRACE_SQLITE_PATH"}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import broker.app; print('ok')"],
        env=env, capture_output=True, text=True, cwd=APP, timeout=120,
    )


def test_a_fresh_clone_can_import_it() -> None:
    """The finding. With no TRACE_SQLITE_PATH and no /data, this raised.

    NOTE, because it matters for what this proves: on a machine that has
    since acquired an `app/data/` directory - as this one has, from
    earlier runs - the original symptom no longer reproduces even with
    the defect restored. This test is therefore evidence on a FRESH
    checkout and weak evidence here; `test_importing_it_builds_nothing`
    below is the one that fails on this machine, because it asks about
    the cause rather than the symptom.
    """
    result = _import_in_a_clean_process()
    assert result.returncode == 0, (
        "EVAL-17: importing broker.app fails on a machine with no /data, so "
        f"pytest aborts collection and the whole app suite runs nothing:\n"
        f"{result.stderr[-800:]}"
    )


def test_importing_it_builds_nothing() -> None:
    import broker.app as module

    # Whatever this session has already done, the module tells the truth
    # about whether IMPORT alone built an app.
    source = (APP / "broker" / "app.py").read_text(encoding="utf-8")
    assert "\napp = create_app()" not in source, (
        "EVAL-17: the app is constructed at module scope, so importing the "
        "module opens a SQLite writer on the production trace path"
    )
    assert hasattr(module, "__getattr__")


def test_the_asgi_name_still_resolves() -> None:
    """Four launchers say `broker.app:app`. The fix must not need them
    changed, or it is not a fix for the ones already deployed."""
    import broker.app as module

    app = module.app
    assert app is not None
    assert hasattr(app, "router"), "that is not an ASGI app"


def test_it_is_built_once() -> None:
    """Two apps would be two brokers holding two SQLite writers on one
    WAL, which is the thing the trace store's design forbids."""
    import broker.app as module

    assert module.app is module.app


def test_an_unknown_attribute_still_raises() -> None:
    """A module `__getattr__` that answers everything hides typos."""
    import broker.app as module

    with pytest.raises(AttributeError):
        module.definitely_not_a_real_attribute


def test_the_suite_does_not_write_into_the_checkout() -> None:
    """The second failure mode. `app/data/` appearing because tests were
    COLLECTED is a working tree the suite has edited."""
    conftest = APP / "broker" / "tests" / "conftest.py"
    assert conftest.exists(), (
        "EVAL-17: nothing points the broker suite's trace path at a "
        "temporary directory, so a test that builds an app writes into the "
        "checkout"
    )
    assert "TRACE_SQLITE_PATH" in conftest.read_text(encoding="utf-8")


def test_every_launcher_still_names_the_same_target() -> None:
    """If one of these had needed changing, the fix would be incomplete
    for whichever deployment was not updated."""
    for relative in (
        "docker-compose.yml",
        "infra/systemd/kasukabe-broker.service",
        "run50.sh",
        "runseries.sh",
    ):
        text = (APP / relative).read_text(encoding="utf-8")
        assert "broker.app:app" in text, (
            f"{relative} no longer names broker.app:app - if the ASGI target "
            "moved, every launcher must move with it"
        )
