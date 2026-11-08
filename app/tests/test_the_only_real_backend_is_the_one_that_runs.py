"""The process supervisor is the only backend that can host an engine.

`INFRA-20`. The Firecracker track is unwired and untested on hardware:
`--supervisor-backend firecracker` is refused by the runner, the kernel
was never compiled, the real-hardware test is skipped everywhere, and the
`extra_env` contract every engine needs (`AGENT_ID`, `WORLD_BASE_URL`,
`KASUKABE_HOME_ROOT`, `SIM_AGENT_PORT`, `KASUKABE_ABLATION_ARM`) has to
ride the kernel command line, which has never been tried.

The review marks it "documented", and that is the right call: a
250-resident deployment today is 250 OS processes on one host at 27-88 MB
each, which fits an m5.metal comfortably. This is not a defect to fix, it
is a state to keep honest.

What this file protects is the honesty. Three things drift silently: a
refusal can be softened, a skip can become an absence, and a "not
supported" message can stop saying what to do instead. All three would
turn a documented gap back into a surprise - which is what `CORE-20` was,
for the namespace backend, right up until this week.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
RUNNER = APP / "world" / "run_genesis.py"
REAL_HW = APP / "infra" / "firecracker" / "tests" / "test_real_firecracker_integration.py"


def test_the_runner_refuses_firecracker() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert 'config.supervisor_backend == "firecracker"' in source, (
        "INFRA-20: the firecracker backend is accepted, and it cannot host "
        "an engine - the kernel was never compiled and the boot-args "
        "contract has never been tried"
    )
    assert "NotImplementedError" in source


def test_the_refusal_says_what_to_do_instead() -> None:
    """A refusal with no alternative reads as "this project cannot do
    that", when what is true is "not from here"."""
    source = RUNNER.read_text(encoding="utf-8")
    refusal = source[source.index('supervisor_backend=\'firecracker\' is not wired'):][:700]
    assert "infra/" in refusal
    assert "VMManager" in refusal, (
        "the message does not name the thing a caller would have to inject"
    )


def test_the_refusal_explains_why_it_is_not_simply_wired() -> None:
    """It is a module boundary, not an oversight - `world/` may not import
    `infra/`. Without that sentence the next reader tries to wire it."""
    source = RUNNER.read_text(encoding="utf-8")
    refusal = source[source.index('supervisor_backend=\'firecracker\' is not wired'):][:700]
    assert "module boundaries" in refusal or "CONVENTIONS" in refusal


def test_the_real_hardware_test_still_exists() -> None:
    """A skipped test is a record of what has not been run. A DELETED one
    is a gap nobody can see."""
    assert REAL_HW.exists(), (
        "INFRA-20: the real-hardware Firecracker test is gone, so nothing "
        "records that the boot path has never been exercised"
    )


def test_it_says_where_and_how_it_must_be_run() -> None:
    """"Skipped" without "here is the command, on that host" is a gap that
    never closes, because nobody knows what closing it would look like."""
    doc = REAL_HW.read_text(encoding="utf-8")[:2000]
    assert "FIRECRACKER_REAL_HW=1" in doc, (
        "the file does not name the command that would actually run it"
    )
    assert "m5.metal" in doc, "it does not say which host"


def test_it_says_why_it_cannot_run_here() -> None:
    """The difference between "we did not bother" and "this machine has no
    /dev/kvm" is the whole value of the record."""
    doc = REAL_HW.read_text(encoding="utf-8")[:2000]
    assert "/dev/kvm" in doc
    assert "not a decision to skip it out of" in doc


def test_the_process_backend_is_what_the_units_start() -> None:
    """INFRA-04 added a genesis systemd unit. If it named a backend that
    cannot host an engine, the deployment path would bring up a town of
    statues - and the refusal above only fires in `main()`, which an
    infra-driven runner bypasses."""
    unit = (APP / "infra" / "systemd" / "kasukabe-genesis.service").read_text(encoding="utf-8")
    backend = re.search(r"--supervisor-backend\s+(\S+)", unit)
    assert backend, "the genesis unit does not name a supervisor backend at all"
    assert backend.group(1) == "process", (
        f"INFRA-20: the genesis unit starts the {backend.group(1)!r} backend, "
        "and only 'process' can host an engine today"
    )
