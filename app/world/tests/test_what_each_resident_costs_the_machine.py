"""What each resident's engine costs in CPU and memory, per agent, in the log.

WHY IT IS NEEDED. Every memory figure this project has acted on is a
SINGLE GLOBAL AVERAGE - `MB_PER_ENGINE_PROCESS = 550`, with
`measured_mb_per_engine()` reporting the mean across whatever is running.
That is enough to answer "will thirty engines fit" and cannot answer any
of the questions actually worth asking:

  * Which resident is expensive, and why? n09 was measured writing 18,547
    characters of prose per turn while others wrote a few hundred. Nothing
    in the logs said what that cost.
  * Does an engine grow over a run? The working-memory flush reached
    99,591 tokens in one call, so something accumulates - and whether that
    is memory as well as prompt has never been recorded.
  * When a run dies, was it the machine? A run that ends in ReadError
    looks identical whether the engine crashed, was swapped out, or simply
    hung.

The mean hides all three. A per-agent, per-sample record answers them, and
it is the only way to say honestly how far this machine goes.

WHAT IT SAMPLES. `rss_mb` (resident set size - real physical memory) and
`cpu_percent` per engine process, tagged with the agent id, written as one
JSON object per line so it can be read back with the same tools every
other record in this project uses.

WHAT IT DOES NOT DO. It does not kill anything, throttle anything or
refuse a run. `world/preflight.py` owns the refuse-before-boot decision
and `memory_budget.py` owns the arithmetic; this only records what
happened, because a sampler that also intervened would make its own
readings unreadable.

IT MUST NEVER TAKE A RUN DOWN. A resource sampler that raises has cost the
run it was measuring, which is worse than no sampler at all - the numbers
are the least important thing in the process.
"""

from __future__ import annotations

import json
import os


from world.resource_log import ResourceSampler, sample_process


def test_sampling_this_very_process_returns_real_numbers() -> None:
    """Against the interpreter running the test, so the numbers cannot be
    a fixture agreeing with itself."""
    reading = sample_process(os.getpid())
    assert reading is not None
    assert reading["rss_mb"] > 1, "a live Python process uses more than a megabyte"
    assert reading["cpu_percent"] >= 0.0


def test_a_process_that_is_not_there_reads_as_nothing() -> None:
    """A dead engine is a normal thing to sample - the supervisor restarts
    them - and must not raise."""
    assert sample_process(2_147_483_646) is None


def test_a_nonsense_pid_does_not_raise() -> None:
    for pid in (0, -1, None):
        assert sample_process(pid) is None


def test_a_sample_is_one_json_line_per_agent(tmp_path) -> None:
    """One object per line, like every other record in this project, so
    the same reading tools work on it."""
    path = tmp_path / "resources.jsonl"
    sampler = ResourceSampler(path, {"n01": os.getpid(), "n02": os.getpid()})
    sampler.sample(tick=7)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert {r["agent_id"] for r in rows} == {"n01", "n02"}
    for row in rows:
        assert row["tick"] == 7
        assert row["rss_mb"] > 1
        assert "cpu_percent" in row
        assert "at" in row, "a sample with no timestamp cannot be put on a graph"


def test_samples_accumulate_rather_than_overwrite(tmp_path) -> None:
    """The question is how an engine GROWS. A file holding only the last
    reading answers nothing."""
    path = tmp_path / "resources.jsonl"
    sampler = ResourceSampler(path, {"n01": os.getpid()})
    sampler.sample(tick=1)
    sampler.sample(tick=2)
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_a_dead_engine_is_recorded_as_dead_not_skipped(tmp_path) -> None:
    """Silence and death look identical in a log that skips what it cannot
    read. A run whose engines died would show a shrinking file and no
    reason - which is how run 16 lost a resident's entire existence."""
    path = tmp_path / "resources.jsonl"
    sampler = ResourceSampler(path, {"n01": os.getpid(), "n02": 2_147_483_646})
    sampler.sample(tick=1)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    dead = next(r for r in rows if r["agent_id"] == "n02")
    assert dead["alive"] is False
    assert dead["rss_mb"] == 0


def test_the_roster_can_change_between_samples(tmp_path) -> None:
    """Engines are restarted by the watchdog and get new pids. A sampler
    that cached the roster would go on reporting a pid that has gone."""
    path = tmp_path / "resources.jsonl"
    sampler = ResourceSampler(path, {"n01": os.getpid()})
    sampler.sample(tick=1)
    sampler.pids = {"n01": os.getpid(), "n02": os.getpid()}
    sampler.sample(tick=2)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3


def test_an_unwritable_path_does_not_raise(tmp_path) -> None:
    """The numbers are the least important thing in the process. A
    sampler that raises has cost the run it was measuring."""
    sampler = ResourceSampler(tmp_path / "no" / "such" / "dir" / "r.jsonl", {"n01": os.getpid()})
    sampler.sample(tick=1)


def test_a_broken_roster_does_not_raise(tmp_path) -> None:
    sampler = ResourceSampler(tmp_path / "r.jsonl", {"n01": "not-a-pid", None: 5})
    sampler.sample(tick=1)


def test_sampling_is_cheap_enough_to_run_every_tick(tmp_path) -> None:
    """Fifty engines sampled every tick, for hours. A sampler that costs a
    measurable share of the tick is changing what it measures."""
    import time

    sampler = ResourceSampler(tmp_path / "r.jsonl", {f"n{i:02d}": os.getpid() for i in range(50)})
    started = time.perf_counter()
    sampler.sample(tick=1)
    assert time.perf_counter() - started < 2.0


def test_the_summary_names_the_most_expensive_resident(tmp_path) -> None:
    """The whole point of per-agent over a mean. n09 wrote 18,547
    characters a turn while others wrote hundreds; the log has to be able
    to say so."""
    from world.resource_log import summarise

    path = tmp_path / "r.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for agent, rss in (("n01", 400), ("n09", 900), ("n02", 410)):
            handle.write(json.dumps({
                "agent_id": agent, "tick": 1, "rss_mb": rss,
                "cpu_percent": 1.0, "alive": True, "at": 0.0,
            }) + "\n")

    report = summarise(path)
    assert report["peak_rss_mb"]["n09"] == 900
    assert report["heaviest"] == "n09"
    assert report["total_peak_rss_mb"] == 1710


def test_the_summary_says_how_far_the_machine_goes(tmp_path) -> None:
    """The question the operator actually has: can this box run fifty?
    Answered from what the engines really cost, not from a constant."""
    from world.resource_log import summarise

    path = tmp_path / "r.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for agent in ("n01", "n02"):
            handle.write(json.dumps({
                "agent_id": agent, "tick": 1, "rss_mb": 500,
                "cpu_percent": 1.0, "alive": True, "at": 0.0,
            }) + "\n")

    report = summarise(path)
    assert report["mean_peak_rss_mb"] == 500
    assert report["engines"] == 2


def test_an_empty_log_summarises_to_nothing_rather_than_raising(tmp_path) -> None:
    from world.resource_log import summarise

    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert summarise(path)["engines"] == 0


def test_a_corrupt_line_is_skipped_not_fatal(tmp_path) -> None:
    """The file is written by a live run that may be killed mid-line."""
    from world.resource_log import summarise

    path = tmp_path / "r.jsonl"
    path.write_text(
        json.dumps({"agent_id": "n01", "tick": 1, "rss_mb": 100,
                    "cpu_percent": 0.0, "alive": True, "at": 0.0})
        + "\n{\"agent_id\": \"n02\", \"rss",
        encoding="utf-8",
    )
    assert summarise(path)["engines"] == 1


def test_the_runner_takes_a_resource_log_flag() -> None:
    """Gate 1 of Build Spec v2 section 1. A sampler the tick loop never
    reaches passes every unit test above and records nothing."""
    from world.run_genesis import build_arg_parser

    assert build_arg_parser().parse_args([]).resource_log is None
    assert build_arg_parser().parse_args(["--resource-log", "r.jsonl"]).resource_log


def test_it_is_off_unless_asked_for() -> None:
    """A diagnostic, not a default. A run that does not want per-tick
    process probes should not pay for them."""
    from world.run_genesis import GenesisRunnerConfig

    assert GenesisRunnerConfig().resource_log_path is None


def test_the_tick_loop_samples() -> None:
    """Severing this is what the test is for: delete the call from
    tick_once and every run records an empty file."""
    import inspect

    from world.run_genesis import GenesisRunner

    source = inspect.getsource(GenesisRunner)
    assert "self.sample_resources(" in source, (
        "nothing in the tick loop samples, so the log is written once at "
        "boot and never again"
    )


def test_the_roster_is_read_fresh_from_the_supervisor() -> None:
    """The watchdog restarts silent engines and a restarted engine has a
    new pid. A roster cached at boot reports a process that has gone -
    the same defect shape as a stale conversation pointer."""
    import inspect

    from world.run_genesis import GenesisRunner

    source = inspect.getsource(GenesisRunner.sample_resources)
    assert "self.resources.pids = {" in source
    assert "processes" in source


def test_sampling_a_run_with_no_sampler_is_a_no_op() -> None:
    from world.run_genesis import GenesisRunner

    runner = GenesisRunner.__new__(GenesisRunner)
    runner.resources = None
    runner.sample_resources(1)


def test_a_process_tree_sums_its_children(tmp_path) -> None:
    """The interpreter running this test, plus a child it spawns. The
    tree must be worth more than the parent alone."""
    import subprocess
    import sys as _sys

    from world.resource_log import sample_tree

    child = subprocess.Popen(
        [_sys.executable, "-c", "import time; x=[0]*3_000_000; time.sleep(20)"]
    )
    try:
        import time as _time

        _time.sleep(3)
        alone = sample_process(os.getpid())
        tree = sample_tree(os.getpid())
        assert alone is not None and tree is not None
        assert tree["rss_mb"] > alone["rss_mb"], (
            "the tree is not counting the child, so every engine reads as "
            "its launcher"
        )
        assert tree["processes"] >= 2
    finally:
        child.kill()
        child.wait(timeout=10)


def test_a_leaf_process_is_its_own_tree() -> None:
    """No children is not an error, and must not read as zero."""
    from world.resource_log import sample_tree

    tree = sample_tree(os.getpid())
    assert tree is not None and tree["rss_mb"] > 1


def test_a_dead_tree_reads_as_nothing() -> None:
    from world.resource_log import sample_tree

    assert sample_tree(2_147_483_646) is None


def test_the_sampler_records_the_tree_not_the_launcher(tmp_path) -> None:
    """The log itself, which is what anybody actually reads."""
    import json as _json

    path = tmp_path / "r.jsonl"
    ResourceSampler(path, {"n01": os.getpid()}).sample(tick=1)
    row = _json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["processes"] >= 1, "the row does not say how many processes it summed"


def test_walking_the_tree_is_still_cheap_enough(tmp_path) -> None:
    """Fifty agents, a hundred processes, every tick. The parent map must
    be built ONCE per sample and not once per agent - fifty wmic calls a
    tick would cost more than the town."""
    import time as _time

    sampler = ResourceSampler(tmp_path / "r.jsonl", {f"n{i:02d}": os.getpid() for i in range(50)})
    started = _time.perf_counter()
    sampler.sample(tick=1)
    assert _time.perf_counter() - started < 8.0
