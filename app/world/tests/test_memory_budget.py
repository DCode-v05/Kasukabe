"""The preflight that stops a 40-engine run from taking down an 8 GB laptop.

The failure it prevents is not a clean one: the machine swaps, everything
slows, and the OOM killer takes whichever process it fancies - often the world
server, so the run dies looking like a world bug rather than a memory one.
"""

import world.memory_budget as mb


def test_a_run_that_fits_is_allowed():
    v = mb.check(4, mb_per_engine=10)
    assert v.fits is True


def test_a_run_that_does_not_fit_is_refused():
    v = mb.check(100_000, mb_per_engine=1000)
    assert v.fits is False


def test_the_refusal_says_what_to_do_instead():
    """An error that only says "no" makes the operator guess. This one names a
    number and the command that produces it."""
    v = mb.check(100_000, mb_per_engine=1000)
    text = v.explain()

    assert str(v.affordable) in text
    assert "make_genesis_subset" in text
    assert "--genesis-path" in text
    assert "close some applications" in text.lower()


def test_the_reserve_does_not_double_count_the_os():
    """`available_mb()` already reports what is free AFTER the OS has taken its
    share. The first draft reserved another 2 GB on top and concluded an 8 GB
    laptop could run one engine.

    Pinned against the measured cost of the run's own services - world 87 MB,
    viewer 12 MB, broker ~20 MB - so a future edit that inflates it has to
    argue with a number.
    """
    assert mb.RESERVED_SYSTEM_MB <= 1024, (
        "the reserve is for this run's own services (~120 MB measured) plus "
        "swap margin, not for the operating system"
    )


def test_both_backend_costs_are_measured_figures():
    """Measured on an M1: ~110 MB for an engine in its own OS process (the
    interpreter and imports are paid again every time), 2.81 MB for one
    namespaced into a shared process. Both constants carry headroom over what
    was observed; neither may drop below it."""
    assert mb.MB_PER_ENGINE_PROCESS >= 48
    assert mb.MB_PER_ENGINE_NAMESPACE >= 3


def test_the_namespace_backend_is_what_makes_30_to_50_agents_possible():
    """The whole point. Same agent count, same machine, different backend."""
    ns = mb.check(50, backend="namespace")
    proc = mb.check(50, backend="process")

    assert ns.needed_mb < 500, "50 namespaced agents should be a few hundred MB"
    assert proc.needed_mb > 2500, "50 separate processes should be gigabytes"
    assert proc.needed_mb > ns.needed_mb * 5


def test_the_shared_process_fixed_cost_is_counted():
    """One interpreter plus the engine's imports, paid once. Ignoring it would
    under-report a small run by more than its agents cost."""
    assert mb.check(1, backend="namespace").needed_mb > mb.NAMESPACE_BASE_MB


def test_available_memory_never_raises_and_never_returns_zero():
    """A preflight that throws is worse than no preflight: it turns an
    advisory check into an outage on any platform whose counters we cannot
    read."""
    assert mb.available_mb() > 0


def test_at_least_one_engine_is_always_affordable():
    """Reporting `affordable = 0` would make the suggested command
    (`make_genesis_subset 0`) nonsense."""
    v = mb.check(1, mb_per_engine=10 ** 9)
    assert v.affordable >= 1


def test_a_measured_figure_overrides_the_constant():
    from world.memory_budget import MB_PER_ENGINE_PROCESS, check

    on_constant = check(30, backend="process")
    measured = check(30, backend="process", mb_per_engine=33)

    assert on_constant.needed_mb == 30 * MB_PER_ENGINE_PROCESS
    assert measured.needed_mb == 30 * 33
    assert measured.needed_mb < on_constant.needed_mb


def test_nothing_running_measures_nothing(monkeypatch):
    """The ordinary pre-boot case. None, so the caller falls back to the
    constant - erring high, which is the safe direction before a run."""
    from world import memory_budget

    assert memory_budget.measured_mb_per_engine(marker="no-such-process-xyzzy") is None


def test_the_measurement_counts_engines_not_every_python():
    """The world server, the broker and the reporter are all Python. A
    per-ENGINE figure that includes them is not a per-engine figure.

    Calls the PARSER, not the platform dispatcher. This test used to
    monkeypatch `subprocess.run` and call `measured_mb_per_engine`, which
    on Windows takes the `_windows_measured` branch and never reads the
    fake at all - so it failed on every machine that is not Linux while
    looking like it covered the parsing it names.
    """
    from world import memory_budget

    fake = (
        "  RSS ARGS\n"
        "51200 /path/.venv/bin/python -m src.sim.sim_main\n"
        "40960 /path/.venv/bin/python -m src.sim.sim_main\n"
        "999999 /path/.venv/bin/python -m uvicorn world.main:app\n"
        "888888 /usr/bin/grep src.sim.sim_main\n"
    )

    seen, mean = memory_budget.engines_from_ps_output(fake)
    assert seen == 2, "the uvicorn process and the grep must not be counted"
    assert mean == 45


def test_ps_output_with_no_engines_in_it_is_not_a_measurement():
    from world import memory_budget

    assert memory_budget.engines_from_ps_output("  RSS ARGS\n") is None


def test_ps_being_unavailable_is_not_an_error(monkeypatch):
    """Windows has no ps. The caller falls back to the constant rather than
    the check exploding."""
    import subprocess

    from world import memory_budget

    def boom(*a, **k):
        raise OSError("no ps here")

    monkeypatch.setattr(subprocess, "run", boom)
    assert memory_budget.measured_mb_per_engine() is None


def test_both_guards_read_the_same_source(monkeypatch, tmp_path):
    """A run passed the fifteen-point checklist and was refused two seconds
    later by a second memory guard nobody had updated: "10 engines need
    about 5500 MB" on a machine that had just measured them at 70 MB each.
    Preflight had been taught to prefer a measurement; run_genesis's own
    pre-spawn check had not."""
    import inspect

    from world import run_genesis

    source = inspect.getsource(run_genesis.GenesisRunner.start_agents)
    assert "measured_mb_per_engine" in source, (
        "the pre-spawn guard must prefer a live measurement, like preflight"
    )
    assert "last_measurement" in source, (
        "and fall back to this machine's last reading before the constant"
    )


def test_a_reading_from_another_platform_is_ignored(tmp_path):
    """The whole reason the file exists is that the figure is
    platform-specific. Carrying one across would repeat the mistake."""
    import json as _json

    from world.memory_budget import last_measurement

    path = tmp_path / "m.json"
    path.write_text(_json.dumps(
        {"engines": 10, "mb_each": 550, "platform": "definitely-not-this-one"}),
        encoding="utf-8")
    assert last_measurement(path) is None


def test_a_reading_round_trips(tmp_path):
    from world.memory_budget import last_measurement, remember_measurement

    path = tmp_path / "m.json"
    remember_measurement(10, 70, path)
    assert last_measurement(path) == 70


def test_a_missing_or_junk_file_is_no_measurement(tmp_path):
    from world.memory_budget import last_measurement

    assert last_measurement(tmp_path / "nope.json") is None
    junk = tmp_path / "junk.json"
    junk.write_text("not json at all", encoding="utf-8")
    assert last_measurement(junk) is None
