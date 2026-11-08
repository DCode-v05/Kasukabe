"""Every knob in an infra template is one the code reads.

`INFRA-04`. The systemd + env deployment path could not boot the broker
or run a town, in six separate ways at once:

  * `broker.env.example` set `MOCK_MODE=false`, and
    `BrokerSettings.build_from_env` REJECTS any value of MOCK_MODE with a
    ConfigError - so the unit crash-looped on the first line of its own
    env file.
  * It set `KASUKABE_SQLITE_PATH`, which the broker does not read, and
    never `TRACE_SQLITE_PATH`, which it does - so the trace defaulted to
    `/data/kasukabe.db`, a path the unit's `ProtectSystem=strict` +
    `ReadWritePaths=/opt/kasukabe/data` makes unwritable, and Litestream
    would have replicated a file the broker never wrote.
  * It set `RPM_PER_MODEL`; the setting is `RPM_PER_LANE`.
  * It carried `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` under a "BLOCKER, do
    not silently work around" paragraph describing `SupabaseRestSink` -
    deleted since. A standing blocker about a thing that no longer exists
    is how an operator concludes the deployment is impossible.
  * `kasukabe-world.service` ran `world.app:app`, whose own docstring
    says "scaffold placeholder, superseded ... not booted by anything
    else in this repo": 31 lines, no map, no ticks, no ledger.
  * There was NO UNIT for `world.run_genesis` at all, so the whole path
    could bring up five services and still have an empty world.

The test that matters is the first one below: a template variable the
code does not read is a lie whichever way it is set.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
ENV_DIR = APP / "infra" / "env"
UNIT_DIR = APP / "infra" / "systemd"


def _declared(path: Path) -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", path.read_text(encoding="utf-8"), re.M))


def _read_by(module: Path) -> set[str]:
    """Every env var name the module looks up."""
    source = module.read_text(encoding="utf-8")
    return set(re.findall(r'(?:get|getenv|environ\.get)\(\s*"([A-Z][A-Z0-9_]*)"', source)) | set(
        re.findall(r'environ\[\s*"([A-Z][A-Z0-9_]*)"\s*\]', source)
    )


def test_the_broker_template_sets_only_what_the_broker_reads() -> None:
    declared = _declared(ENV_DIR / "broker.env.example")
    read = _read_by(APP / "broker" / "config.py")
    unread = {name for name in declared - read if not name.startswith("AWS_")}
    assert not unread, (
        f"INFRA-04: broker.env.example sets {sorted(unread)}, which "
        "broker/config.py never reads - an operator sets them, nothing "
        "happens, and the failure surfaces four files away"
    )


def test_the_broker_template_does_not_set_the_one_var_that_is_refused() -> None:
    """MOCK_MODE is not ignored, it is a ConfigError. Setting it in the
    template made the unit crash-loop at boot."""
    declared = _declared(ENV_DIR / "broker.env.example")
    assert "MOCK_MODE" not in declared, (
        "INFRA-04: the template sets MOCK_MODE and build_from_env rejects "
        "any value of it, so the service cannot start"
    )


def test_the_trace_path_is_set_and_is_writable_by_the_unit() -> None:
    """Default `/data/kasukabe.db` is outside the unit's ReadWritePaths,
    so the first flush fails on a hardened host."""
    declared = dict(
        re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$",
                   (ENV_DIR / "broker.env.example").read_text(encoding="utf-8"), re.M)
    )
    path = declared.get("TRACE_SQLITE_PATH")
    assert path, "INFRA-04: broker.env.example never sets TRACE_SQLITE_PATH"

    unit = (UNIT_DIR / "kasukabe-broker.service").read_text(encoding="utf-8")
    writable = re.findall(r"^ReadWritePaths=(.*)$", unit, re.M)
    allowed = [p for line in writable for p in line.split()]
    assert any(path.startswith(root.rstrip("/") + "/") for root in allowed), (
        f"INFRA-04: the broker writes {path} and the unit only permits "
        f"{allowed} - the first trace flush fails under ProtectSystem=strict"
    )


def test_no_template_still_warns_about_a_deleted_component() -> None:
    """`SupabaseRestSink` was removed. A standing "BLOCKER, do not
    silently work around" about it reads as "this deployment cannot
    work"."""
    for template in ENV_DIR.glob("*.example"):
        text = template.read_text(encoding="utf-8")
        assert "SupabaseRestSink" not in text, (
            f"INFRA-04: {template.name} still blocks on SupabaseRestSink, "
            "which no longer exists"
        )


def test_the_world_unit_boots_a_real_world() -> None:
    unit = (UNIT_DIR / "kasukabe-world.service").read_text(encoding="utf-8")
    exec_line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    assert "world.app:app" not in exec_line, (
        "INFRA-04: the unit boots world/app.py, a 31-line health-check "
        "scaffold whose own docstring says it is superseded and booted by "
        "nothing - the service answers /health and cannot run a town"
    )
    assert "world.main:app" in exec_line


def test_something_in_infra_can_start_a_town() -> None:
    """The gap that made the other five moot."""
    units = list(UNIT_DIR.glob("*.service"))
    starters = [u for u in units if "world.run_genesis" in u.read_text(encoding="utf-8")]
    assert starters, (
        "INFRA-04: no systemd unit runs world.run_genesis, so this "
        f"deployment path can start {len(units)} services and still have "
        "an empty world"
    )


def test_the_genesis_unit_has_an_env_file_that_exists() -> None:
    unit = (UNIT_DIR / "kasukabe-genesis.service").read_text(encoding="utf-8")
    named = re.search(r"^EnvironmentFile=(.*)$", unit, re.M)
    assert named, "the genesis unit reads no env file"
    template = ENV_DIR / (Path(named.group(1)).name + ".example")
    assert template.exists(), (
        f"INFRA-04: the unit reads {named.group(1)} and there is no "
        f"{template.name} to copy - the operator has to invent it"
    )


def test_the_genesis_template_sets_only_what_the_runner_reads() -> None:
    declared = _declared(ENV_DIR / "genesis.env.example")
    read = (
        _read_by(APP / "world" / "run_genesis.py")
        | _read_by(APP / "world" / "admin_api.py")
    )
    unread = declared - read
    assert not unread, (
        f"INFRA-04, again: genesis.env.example sets {sorted(unread)} and "
        "world/run_genesis.py reads none of them"
    )


def test_the_two_world_units_do_not_both_own_the_port() -> None:
    """Both bind :8100. Running both is a port collision, and the runner's
    embedded world is the one every real run uses."""
    genesis = (UNIT_DIR / "kasukabe-genesis.service").read_text(encoding="utf-8")
    assert "Conflicts=kasukabe-world.service" in genesis, (
        "INFRA-04: the genesis unit and the standalone world unit both bind "
        "8100 and nothing stops them being enabled together"
    )
