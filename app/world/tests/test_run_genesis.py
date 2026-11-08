"""Tests for the genesis runner (world/run_genesis.py).

Covers exactly the behaviors the genesis-runner task asks for: booting the
world from genesis.json, launching agent units, running ticks, checkpointing,
resuming from the newest checkpoint, stopping cleanly when the spend
governor trips, the live-tunable retrieval-depth knob, and live metrics.

Every test here uses `--mock`-equivalent construction (mock_mode=True,
MockSupervisor + MockAgentGateway) or a harmless trivial subprocess command
for ProcessSupervisor - app/CONVENTIONS.md: "Never call a real LLM API in a
test." No test spends real money or reaches Bedrock; the ledger accounts
these tests burn against are the genesis-seeded mock wallets in this
process's own in-memory WorldState.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_PATENT_OFFICE,
    LOCATION_TOWN_HALL,
    AmendmentStatus,
    Candidacy,
    ElectionPhase,
    EligibilityFacts,
    GovernmentState,
    OrganizationKind,
)
from world.core.checkpoint import WorldCheckpoint
from world.core.clock import timestamp_for
from world.ledger.money import BURNED_ACCOUNT_ID, TOTAL_USD_CENTS, TREASURY_ACCOUNT_ID
from world.retrieval_depth import RetrievalDepthConfig
from world.run_genesis import (
    WORLD_STATE_FIELD_TYPES,
    CharterCheckpointAdapter,
    ConfigError,
    DEFAULT_CALLS_PER_TURN,
    GenesisRunner,
    GenesisRunnerConfig,
    HttpAgentGateway,
    LocalCheckpointStore,
    MockAgentGateway,
    MockSupervisor,
    ProcessSupervisor,
    RunnerCounters,
    RunnerCountersCheckpointAdapter,
    TickOutcome,
    UnitAlreadyExistsError,
    UnitSpec,
    UnitState,
    UnknownUnitError,
    build_arg_parser,
    build_embedded_world_app,
    config_from_args,
    default_engine_command_builder,
    to_jsonable,
)
from world.tools.models import Subpoena, ToolInvocationRecord
from world.tools.state import OWNED_STATE_FIELDS


class TestGenesisRunnerConfig:
    def test_defaults_are_real_engines(self) -> None:
        """The safe default is no longer the fake one.

        A runner that defaults to MockSupervisor writes checkpoints, moves
        metrics and flows ticks while no engine ever starts - which reads as
        a healthy run right up until someone asks what the agents decided.
        """
        config = GenesisRunnerConfig()
        assert config.mock_mode is False
        assert config.validate() == []

    def test_validate_rejects_bad_world_mode(self) -> None:
        config = GenesisRunnerConfig(world_mode="bogus")
        errors = config.validate()
        assert any("world_mode" in e for e in errors)

    def test_validate_rejects_bad_supervisor_backend(self) -> None:
        config = GenesisRunnerConfig(supervisor_backend="bogus")
        errors = config.validate()
        assert any("supervisor_backend" in e for e in errors)

    def test_validate_rejects_non_positive_budget_cap(self) -> None:
        config = GenesisRunnerConfig(budget_cap_cents=0)
        errors = config.validate()
        assert any("budget_cap_cents" in e for e in errors)

    def test_validate_rejects_budget_cap_exceeding_total_ledger(self) -> None:
        config = GenesisRunnerConfig(budget_cap_cents=TOTAL_USD_CENTS + 1)
        errors = config.validate()
        assert any("exceeds" in e for e in errors)

    def test_validate_accepts_budget_cap_at_task_hard_cap(self) -> None:
        config = GenesisRunnerConfig(budget_cap_cents=40_000)
        assert config.validate() == []

    def test_validate_rejects_retrieval_depth_out_of_range(self) -> None:
        config = GenesisRunnerConfig(retrieval_depth_fraction=1.5)
        errors = config.validate()
        assert any("retrieval_depth_fraction" in e for e in errors)

    def test_validate_rejects_non_positive_tick_timeout(self) -> None:
        config = GenesisRunnerConfig(tick_timeout_s=0)
        errors = config.validate()
        assert any("tick_timeout_s" in e for e in errors)

    def test_validate_rejects_scheduler_fraction_out_of_range(self) -> None:
        config = GenesisRunnerConfig(scheduler_target_fraction=0)
        errors = config.validate()
        assert any("scheduler_target_fraction" in e for e in errors)

    def test_validate_rejects_non_positive_max_game_days(self) -> None:
        config = GenesisRunnerConfig(max_game_days=0)
        errors = config.validate()
        assert any("max_game_days" in e for e in errors)

    def test_validate_aggregates_every_error_at_once(self) -> None:
        config = GenesisRunnerConfig(
            world_mode="bogus", supervisor_backend="bogus", budget_cap_cents=0
        )
        errors = config.validate()
        assert len(errors) >= 3

    def test_from_env_defaults_when_empty(self) -> None:
        config = GenesisRunnerConfig.from_env({})
        assert config.mock_mode is False
        assert config.budget_cap_cents == GenesisRunnerConfig().budget_cap_cents

    def test_from_env_reads_the_world_mode_and_refuses_the_broken_one(self) -> None:
        """`world_mode` used to be sampled inside the every-knob test above
        with the value `external`, which CORE-27 now refuses: that host does
        not serve the engine contract, so accepting it booted N engine
        processes that died at the first dispatch.

        Split in two, because the test was asserting two things at once -
        that the knob is READ, and that this value is ACCEPTED - and only
        the first was ever the point.
        """
        with pytest.raises(ConfigError) as caught:
            GenesisRunnerConfig.from_env({"KASUKABE_WORLD_MODE": "external"})
        assert "external" in str(caught.value), (
            "the knob was not read from the environment at all"
        )
        assert "/tools/registry" in str(caught.value), (
            "the refusal does not say what is missing"
        )

    def test_from_env_reads_every_knob(self) -> None:
        config = GenesisRunnerConfig.from_env(
            {
                "KASUKABE_GENESIS_SEED": "7",
                "KASUKABE_BUDGET_CAP_CENTS": "1234",
                "KASUKABE_RETRIEVAL_DEPTH": "0.5",
                "KASUKABE_WORLD_PORT": "9999",
                "KASUKABE_SUPERVISOR_BACKEND": "firecracker",
                "KASUKABE_CHECKPOINT_EVERY_DAY": "false",
                "KASUKABE_MAX_GAME_DAYS": "3",
            }
        )
        assert config.mock_mode is False
        assert config.population_seed == 7
        assert config.budget_cap_cents == 1234
        assert config.retrieval_depth_fraction == 0.5
        assert config.world_port == 9999
        assert config.supervisor_backend == "firecracker"
        assert config.checkpoint_every_day is False
        assert config.max_game_days == 3

    def test_from_env_raises_config_error_on_invalid_combo(self) -> None:
        with pytest.raises(ConfigError):
            GenesisRunnerConfig.from_env({"KASUKABE_BUDGET_CAP_CENTS": "0"})

    def test_world_base_url_defaults_to_host_port(self) -> None:
        config = GenesisRunnerConfig(world_host="1.2.3.4", world_port=8100)
        assert config.world_base_url() == "http://1.2.3.4:8100"

    def test_world_base_url_override_wins(self) -> None:
        config = GenesisRunnerConfig(world_base_url_override="http://example:9")
        assert config.world_base_url() == "http://example:9"


class TestCliArgParsing:
    def test_mock_flag_is_refused_rather_than_silently_spending(self) -> None:
        """A script still passing --mock expects a free dry run. Handing it
        real Bedrock spend instead is the one outcome worse than stopping."""
        import pytest as _pytest

        parser = build_arg_parser()
        args = parser.parse_args(["--mock"])
        with _pytest.raises(SystemExit, match="no longer supported"):
            config_from_args(args)

    def test_live_flag_clears_mock_mode(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--live"])
        config = config_from_args(args)
        assert config.mock_mode is False

    def test_budget_cap_override(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--budget-cap-cents", "999"])
        config = config_from_args(args)
        assert config.budget_cap_cents == 999

    def test_retrieval_depth_override(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--retrieval-depth", "0.9"])
        config = config_from_args(args)
        assert config.retrieval_depth_fraction == 0.9

    def test_invalid_override_raises_config_error(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--budget-cap-cents", "-5"])
        with pytest.raises(ConfigError):
            config_from_args(args)

    def test_burst_ticks_and_no_resume_parse(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--burst-ticks", "10", "--no-resume"])
        assert args.burst_ticks == 10
        assert args.no_resume is True


class TestToJsonable:
    def test_plain_scalars_pass_through(self) -> None:
        assert to_jsonable(1) == 1
        assert to_jsonable("x") == "x"
        assert to_jsonable(None) is None

    def test_dict_and_list_recurse(self) -> None:
        assert to_jsonable({"a": [1, 2, {"b": 3}]}) == {"a": [1, 2, {"b": 3}]}

    def test_set_becomes_list(self) -> None:
        result = to_jsonable({1, 2, 3})
        assert isinstance(result, list)
        assert sorted(result) == [1, 2, 3]

    def test_enum_becomes_value(self) -> None:
        assert to_jsonable(UnitState.RUNNING) == "running"

    def test_dataclass_becomes_dict(self) -> None:
        @dataclass
        class Point:
            x: int
            y: int

        assert to_jsonable(Point(1, 2)) == {"x": 1, "y": 2}


class TestWorldStateFieldClassification:
    def test_covers_every_owned_state_field_by_name(self) -> None:
        """The structural guarantee risk #3 asks for: WORLD_STATE_FIELD_TYPES
        is derived from OWNED_STATE_FIELDS itself (see
        classify_world_state_fields()'s docstring), so this can never
        drift the way the old hand-maintained WORLD_STATE_DICT_MODEL_TYPES/
        WORLD_STATE_LIST_MODEL_TYPES pair did - this test pins that
        equality down so a future refactor that breaks the derivation
        fails loudly here too, not just at the next checkpoint."""
        assert set(WORLD_STATE_FIELD_TYPES) == set(OWNED_STATE_FIELDS)

    def test_classifies_known_model_dict_and_list_fields_correctly(self) -> None:
        assert WORLD_STATE_FIELD_TYPES["subpoenas"] == ("dict_model", Subpoena)
        assert WORLD_STATE_FIELD_TYPES["tool_invocations"] == ("list_model", ToolInvocationRecord)
        assert WORLD_STATE_FIELD_TYPES["skills"] == ("plain", None)


class TestProcessSupervisor:
    @pytest.mark.asyncio
    async def test_start_with_trivial_command_reaches_running(self, tmp_path: Path) -> None:
        supervisor = ProcessSupervisor(lambda spec: ["sleep", "5"], cwd=tmp_path)
        try:
            status = await supervisor.start(
                UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k")
            )
            assert status.state is UnitState.RUNNING
            assert status.is_alive is True
        finally:
            await supervisor.shutdown_all()

    @pytest.mark.asyncio
    async def test_start_nonexistent_command_reports_failed_without_raising(
        self, tmp_path: Path
    ) -> None:
        supervisor = ProcessSupervisor(
            lambda spec: ["/no/such/binary-xyz"], cwd=tmp_path
        )
        status = await supervisor.start(
            UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k")
        )
        assert status.state is UnitState.FAILED
        assert status.detail

    @pytest.mark.asyncio
    async def test_start_twice_while_alive_raises(self, tmp_path: Path) -> None:
        supervisor = ProcessSupervisor(lambda spec: ["sleep", "5"], cwd=tmp_path)
        try:
            spec = UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k")
            await supervisor.start(spec)
            with pytest.raises(UnitAlreadyExistsError):
                await supervisor.start(spec)
        finally:
            await supervisor.shutdown_all()

    @pytest.mark.asyncio
    async def test_stop_transitions_to_stopped(self, tmp_path: Path) -> None:
        supervisor = ProcessSupervisor(lambda spec: ["sleep", "5"], cwd=tmp_path)
        spec = UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k")
        await supervisor.start(spec)
        status = await supervisor.stop("u1")
        assert status.state is UnitState.STOPPED

    @pytest.mark.asyncio
    async def test_status_on_unknown_unit_raises(self, tmp_path: Path) -> None:
        supervisor = ProcessSupervisor(lambda spec: ["sleep", "5"], cwd=tmp_path)
        with pytest.raises(UnknownUnitError):
            await supervisor.status("nope")

    @pytest.mark.asyncio
    async def test_stop_on_unknown_unit_raises(self, tmp_path: Path) -> None:
        supervisor = ProcessSupervisor(lambda spec: ["sleep", "5"], cwd=tmp_path)
        with pytest.raises(UnknownUnitError):
            await supervisor.stop("nope")

    @pytest.mark.asyncio
    async def test_health_true_only_while_running(self, tmp_path: Path) -> None:
        supervisor = ProcessSupervisor(lambda spec: ["sleep", "5"], cwd=tmp_path)
        spec = UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k")
        await supervisor.start(spec)
        assert await supervisor.health("u1") is True
        await supervisor.stop("u1")
        assert await supervisor.health("u1") is False

    @pytest.mark.asyncio
    async def test_shutdown_all_stops_every_unit(self, tmp_path: Path) -> None:
        supervisor = ProcessSupervisor(lambda spec: ["sleep", "5"], cwd=tmp_path)
        for i in range(3):
            await supervisor.start(
                UnitSpec(unit_id=f"u{i}", engine_id=f"e{i}", cdt_url="http://x", cdt_key="k")
            )
        await supervisor.shutdown_all()
        statuses = await supervisor.list()
        assert all(s.state is UnitState.STOPPED for s in statuses)

    def test_default_engine_command_builder_falls_back_when_no_venv(self, tmp_path: Path) -> None:
        import os as os_module
        import sys as sys_module

        builder = default_engine_command_builder(tmp_path)
        command = builder(UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k"))
        expected = sys_module.executable if os_module.name == "nt" else "python3"
        assert command == [expected, "-m", "src.sim.sim_main"]

    def test_default_engine_command_builder_finds_this_platforms_venv(self, tmp_path: Path) -> None:
        """The path a real run actually takes, on whichever OS is running.

        BOTH layouts are created, so this asserts the builder picks the one
        matching the running OS and IGNORES the other. That is the real
        contract: start-genesis.sh bind-mounts the host's engine/ into a
        Linux container, so a Windows-built .venv/Scripts/python.exe is
        genuinely visible there and must not be chosen.
        """
        import os as os_module

        posix = tmp_path / ".venv" / "bin" / "python"
        windows = tmp_path / ".venv" / "Scripts" / "python.exe"
        for layout in (posix, windows):
            layout.parent.mkdir(parents=True, exist_ok=True)
            layout.write_text("", encoding="utf-8")
        expected = windows if os_module.name == "nt" else posix

        builder = default_engine_command_builder(tmp_path)
        command = builder(UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k"))
        assert command == [str(expected), "-m", "src.sim.sim_main"]


class TestMockSupervisor:
    @pytest.mark.asyncio
    async def test_start_is_immediately_running(self) -> None:
        supervisor = MockSupervisor()
        status = await supervisor.start(
            UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k")
        )
        assert status.state is UnitState.RUNNING

    @pytest.mark.asyncio
    async def test_full_lifecycle(self) -> None:
        supervisor = MockSupervisor()
        spec = UnitSpec(unit_id="u1", engine_id="e1", cdt_url="http://x", cdt_key="k")
        await supervisor.start(spec)
        assert await supervisor.health("u1") is True
        await supervisor.stop("u1")
        assert await supervisor.health("u1") is False
        assert len(await supervisor.list()) == 1
        await supervisor.shutdown_all()

    @pytest.mark.asyncio
    async def test_unknown_unit_operations_raise(self) -> None:
        supervisor = MockSupervisor()
        with pytest.raises(UnknownUnitError):
            await supervisor.status("nope")
        with pytest.raises(UnknownUnitError):
            await supervisor.stop("nope")


class TestMockAgentGateway:
    @pytest.mark.asyncio
    async def test_sleeping_agent_costs_nothing(self) -> None:
        import random

        gateway = MockAgentGateway(RetrievalDepthConfig(fraction=1.0), random.Random(1))
        outcome = await gateway.send_tick("a1", tick=1, is_sleeping=True, perception="")
        assert outcome.cost_usd_cents == 0
        assert outcome.input_tokens == 0
        assert outcome.succeeded is True

    @pytest.mark.asyncio
    async def test_awake_agent_reports_depth_derived_cost(self) -> None:
        import random

        depth = RetrievalDepthConfig(fraction=0.5)
        gateway = MockAgentGateway(depth, random.Random(1))
        outcome = await gateway.send_tick("a1", tick=1, is_sleeping=False, perception="hi")
        assert outcome.input_tokens == depth.estimated_input_tokens
        assert outcome.output_tokens == 291
        assert outcome.succeeded is True


class TestLocalCheckpointStore:
    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        store = LocalCheckpointStore(tmp_path)
        checkpoint = WorldCheckpoint(tick_index=5, game_day=0, components={"x": {"y": 1}})
        path = store.write(checkpoint)
        assert path.exists()
        restored = store.read(path)
        assert restored.tick_index == 5
        assert restored.components == {"x": {"y": 1}}

    def test_write_is_gzip_compressed(self, tmp_path: Path) -> None:
        store = LocalCheckpointStore(tmp_path)
        checkpoint = WorldCheckpoint(tick_index=1, game_day=0)
        path = store.write(checkpoint)
        with gzip.open(path, "rt") as f:
            payload = json.load(f)
        assert payload["tick_index"] == 1

    def test_write_leaves_no_tmp_file_behind(self, tmp_path: Path) -> None:
        store = LocalCheckpointStore(tmp_path)
        store.write(WorldCheckpoint(tick_index=1, game_day=0))
        assert list(tmp_path.glob("*.tmp")) == []

    def test_latest_is_none_when_empty(self, tmp_path: Path) -> None:
        store = LocalCheckpointStore(tmp_path)
        assert store.latest() is None

    def test_latest_returns_highest_tick(self, tmp_path: Path) -> None:
        store = LocalCheckpointStore(tmp_path)
        store.write(WorldCheckpoint(tick_index=5, game_day=0))
        store.write(WorldCheckpoint(tick_index=20, game_day=0))
        store.write(WorldCheckpoint(tick_index=10, game_day=0))
        latest = store.latest()
        assert latest is not None
        assert latest.tick_index == 20

    def test_directory_created_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "checkpoints"
        LocalCheckpointStore(target)
        assert target.is_dir()

    def test_write_fsyncs_the_file_and_the_directory(self, tmp_path: Path, monkeypatch) -> None:
        """DESIGN.md section 9: this host is "spot ... can be reclaimed at
        any time" - a rename() without an fsync is atomic w.r.t. visibility
        but not durability against that exact failure mode. Asserts both
        fsync calls this durability fix added actually happen: once for the
        temp file's fd (before it is linked in under its real name) and
        once for the checkpoint directory's fd (after the rename), matching
        the write-fsync-rename-fsync-dir recipe this method's own comment
        documents. A spy on os.fsync, not a mock of the write path itself -
        the real bytes still land on the real filesystem (verified by the
        next assertion), only the fsync call sites are being counted.
        """
        import os as os_module

        store = LocalCheckpointStore(tmp_path)
        fsynced_fds: list[int] = []
        real_fsync = os_module.fsync

        def spy_fsync(fd: int) -> None:
            fsynced_fds.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os_module, "fsync", spy_fsync)

        path = store.write(WorldCheckpoint(tick_index=7, game_day=0, components={"a": 1}))

        expected_fsyncs = 1 if os_module.name == "nt" else 2
        assert len(fsynced_fds) == expected_fsyncs
        assert path.exists()
        with gzip.open(path, "rt") as f:
            assert json.load(f)["components"] == {"a": 1}


class TestRunnerCountersCheckpointAdapter:
    def test_round_trips_every_field(self) -> None:
        import random

        counters = RunnerCounters(
            births=3,
            deaths=1,
            elections=2,
            crimes=4,
            businesses_registered=5,
            retrieval_depth_fraction=0.7,
            last_life_hour_processed=12.5,
            last_life_day_processed=1,
            mock_cost_remainder_usd=0.0042,
        )
        counters.rng_state = random.Random(9).getstate()
        adapter = RunnerCountersCheckpointAdapter(counters)

        snapshot = adapter.snapshot()
        restored_counters = RunnerCounters()
        restore_adapter = RunnerCountersCheckpointAdapter(restored_counters)
        restore_adapter.restore(json.loads(json.dumps(snapshot)))

        assert restored_counters.births == 3
        assert restored_counters.deaths == 1
        assert restored_counters.elections == 2
        assert restored_counters.crimes == 4
        assert restored_counters.businesses_registered == 5
        assert restored_counters.retrieval_depth_fraction == 0.7
        assert restored_counters.last_life_hour_processed == 12.5
        assert restored_counters.last_life_day_processed == 1
        assert restored_counters.mock_cost_remainder_usd == pytest.approx(0.0042)
        assert restored_counters.rng_state == counters.rng_state

    def test_restore_tolerates_checkpoint_written_before_remainder_field_existed(self) -> None:
        counters = RunnerCounters()
        adapter = RunnerCountersCheckpointAdapter(counters)
        legacy_snapshot = {
            "births": 0,
            "deaths": 0,
            "elections": 0,
            "crimes": 0,
            "businesses_registered": 0,
            "rng_state": None,
            "retrieval_depth_fraction": 0.0,
            "last_life_hour_processed": 0.0,
            "last_life_day_processed": -1,
        }
        adapter.restore(legacy_snapshot)
        assert counters.mock_cost_remainder_usd == 0.0


def make_runner(tmp_path: Path, **overrides: object) -> GenesisRunner:
    kwargs: dict[str, object] = dict(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    )
    kwargs.update(overrides)
    config = GenesisRunnerConfig(**kwargs)
    return GenesisRunner(config)


async def make_launched_runner(tmp_path: Path, **overrides: object) -> GenesisRunner:
    """boot() + start_agents(), the two steps every real run does before
    ticking (main_async's own sequence) - run_tick()/run_burst()/
    run_continuous() all assert self.gateway is not None, so any test that
    drives the tick loop needs a launched runner, not just a booted one."""
    runner = make_runner(tmp_path, **overrides)
    await runner.boot(resume=False)
    await runner.start_agents()
    return runner


class TestGenesisRunnerBoot:
    @pytest.mark.asyncio
    async def test_boot_seeds_full_genesis_population(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        assert runner.world_state is not None
        assert len(runner.world_state.agents) == runner.genesis.population
        assert len(runner.world_state.households) > 0
        import json as _json
        from pathlib import Path as _Path
        _cast = _json.loads(
            (_Path(__file__).resolve().parents[2] / "world" / "genesis.json")
            .read_text(encoding="utf-8")
        )
        _expected = len(_cast["small_businesses"]) + (
            1 if _cast.get("construction_company") else 0
        )
        assert len(runner.world_state.businesses) == _expected

    @pytest.mark.asyncio
    async def test_boot_holds_the_thousand_dollar_invariant(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        assert runner.ledger is not None
        wallets_total = sum(
            runner.ledger.usd_balance(agent_id) for agent_id in runner.world_state.agents
        )
        wallets_total += sum(
            runner.ledger.usd_balance(business_id)
            for business_id in runner.world_state.businesses
        )
        treasury = runner.ledger.usd_balance(TREASURY_ACCOUNT_ID)
        burned = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        assert wallets_total + treasury + burned == TOTAL_USD_CENTS

    @pytest.mark.asyncio
    async def test_boot_places_every_agent_at_a_real_location(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        for agent_id in runner.world_state.agents:
            assert runner.world_state.locations.location_of(agent_id) is not None

    @pytest.mark.asyncio
    async def test_boot_with_no_checkpoint_present_does_not_resume(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=True)
        assert runner.world_state.current_tick == 0


class TestGenesisRunnerTickLoop:
    @pytest.mark.asyncio
    async def test_run_burst_advances_tick_and_updates_metrics(self, tmp_path: Path) -> None:
        runner = await make_launched_runner(tmp_path)
        results = await runner.run_burst(10)
        assert len(results) == 10
        assert runner.world_state.current_tick == 10
        snapshot = runner.metrics.snapshot(game_day=0)
        assert snapshot["population"] == runner.genesis.population
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_run_burst_stops_early_when_governor_trips(self, tmp_path: Path) -> None:
        runner = await make_launched_runner(
            tmp_path, budget_cap_cents=1, retrieval_depth_fraction=1.0
        )
        results = await runner.run_burst(200)
        assert runner.governor.tripped is True
        assert len(results) < 200
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_day_boundary_writes_a_local_checkpoint(self, tmp_path: Path) -> None:
        runner = await make_launched_runner(tmp_path)
        await runner.run_burst(150)
        checkpoints = runner.checkpoint_store.list_checkpoints()
        assert len(checkpoints) >= 1
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_request_stop_halts_run_continuous(self, tmp_path: Path) -> None:
        runner = await make_launched_runner(tmp_path, max_game_days=1000)

        async def stop_soon() -> None:
            import asyncio

            await asyncio.sleep(0)
            for _ in range(3):
                await asyncio.sleep(0)
            runner.request_stop()

        import asyncio

        await asyncio.gather(runner.run_continuous(), stop_soon())
        assert runner.stop_requested is True
        await runner.shutdown()


class TestGenesisRunnerCheckpointResume:
    @pytest.mark.asyncio
    async def test_resume_restores_tick_index(self, tmp_path: Path) -> None:
        first = await make_launched_runner(tmp_path)
        await first.run_burst(20)
        await first.shutdown()

        second = make_runner(tmp_path)
        await second.boot(resume=True)
        assert second.world_state.current_tick == 20
        await second.shutdown()

    @pytest.mark.asyncio
    async def test_resume_restores_runner_counters(self, tmp_path: Path) -> None:
        first = await make_launched_runner(tmp_path, retrieval_depth_fraction=1.0)
        await first.run_burst(30)
        deaths_before = first.counters.deaths
        births_before = first.counters.births
        remainder_before = first.counters.mock_cost_remainder_usd
        await first.shutdown()

        second = make_runner(tmp_path)
        await second.boot(resume=True)
        assert second.counters.deaths == deaths_before
        assert second.counters.births == births_before
        assert second.counters.mock_cost_remainder_usd == pytest.approx(remainder_before)
        await second.shutdown()

    @pytest.mark.asyncio
    async def test_resume_preserves_ledger_invariant(self, tmp_path: Path) -> None:
        first = await make_launched_runner(tmp_path, retrieval_depth_fraction=1.0)
        await first.run_burst(30)
        await first.shutdown()

        second = make_runner(tmp_path)
        await second.boot(resume=True)
        wallets_total = sum(
            second.ledger.usd_balance(agent_id) for agent_id in second.world_state.agents
        )
        wallets_total += sum(
            second.ledger.usd_balance(business_id)
            for business_id in second.world_state.businesses
        )
        treasury = second.ledger.usd_balance(TREASURY_ACCOUNT_ID)
        burned = second.ledger.usd_balance(BURNED_ACCOUNT_ID)
        assert wallets_total + treasury + burned == TOTAL_USD_CENTS
        await second.shutdown()

    @pytest.mark.asyncio
    async def test_resume_continues_burning_from_the_carried_remainder(
        self, tmp_path: Path
    ) -> None:
        """Regression test for the sub-cent rounding bug: a resumed run
        must not restart mock_cost_remainder_usd at 0.0, or a run stopped
        and resumed many times would silently under-report spend forever
        relative to one that ran straight through."""
        first = await make_launched_runner(tmp_path, retrieval_depth_fraction=1.0)
        await first.run_burst(3)
        remainder_before = first.counters.mock_cost_remainder_usd
        await first.shutdown()

        second = make_runner(tmp_path, retrieval_depth_fraction=1.0)
        await second.boot(resume=True)
        assert second.counters.mock_cost_remainder_usd == pytest.approx(remainder_before)
        await second.shutdown()


def adult_facts(agent_id: str) -> EligibilityFacts:
    return EligibilityFacts(agent_id=agent_id, age_years=30, is_resident=True)


class TestGenesisRunnerCharterResume:
    @pytest.mark.asyncio
    async def test_resume_restores_a_mid_flight_election_bit_identical(
        self, tmp_path: Path
    ) -> None:
        first = await make_launched_runner(tmp_path)
        charter = first.world_state.charter

        elections = await charter.store.list_elections()
        assert len(elections) == 1
        election_id = elections[0].election_id

        adults = [
            agent_id
            for agent_id, record in first.world_state.agents.items()
            if record.age_years >= 18 and record.alive
        ]
        assert len(adults) >= 6
        candidates, voter_id = adults[:5], adults[5]

        for agent_id in candidates:
            await charter.elections.declare_candidacy(
                election_id, agent_id, adult_facts(agent_id), now_tick=0, location=LOCATION_TOWN_HALL
            )
        await charter.elections.cast_vote(
            election_id,
            voter_id,
            candidates[0],
            adult_facts(voter_id),
            now_tick=300,
            location=LOCATION_TOWN_HALL,
        )

        election_before = await charter.store.get_election(election_id)
        assert election_before.phase == ElectionPhase.VOTING_OPEN
        assert election_before.result is None

        await first.shutdown()

        second = make_runner(tmp_path)
        await second.boot(resume=True)
        restored_charter = second.world_state.charter

        election_after = await restored_charter.store.get_election(election_id)
        assert election_after is not None
        assert election_after == election_before
        assert election_after.phase == ElectionPhase.VOTING_OPEN
        assert election_after.candidates.keys() == set(candidates)
        assert election_after.ballots == {voter_id: candidates[0]}
        await second.shutdown()

    @pytest.mark.asyncio
    async def test_resume_restores_charter_election_government_orgs_and_patent_queue(
        self, tmp_path: Path
    ) -> None:
        """The full slice STATUS.md risk #4 names by name: charter
        (history, including an in-flight amendment), election state,
        government membership, organizations, and the patent-office
        queue - all asserted bit-identical after a real process boundary
        (a brand-new GenesisRunner instance against the same checkpoint
        directory), not merely "present"."""
        first = await make_launched_runner(tmp_path)
        charter = first.world_state.charter

        elections = await charter.store.list_elections()
        election_id = elections[0].election_id
        adults = [
            agent_id
            for agent_id, record in first.world_state.agents.items()
            if record.age_years >= 18 and record.alive
        ]
        candidates = adults[:5]
        for agent_id in candidates:
            await charter.elections.declare_candidacy(
                election_id, agent_id, adult_facts(agent_id), now_tick=0, location=LOCATION_TOWN_HALL
            )
        for i, voter_id in enumerate(adults[5:10]):
            await charter.elections.cast_vote(
                election_id,
                voter_id,
                candidates[i % len(candidates)],
                adult_facts(voter_id),
                now_tick=300,
                location=LOCATION_TOWN_HALL,
            )
        tallied = await charter.elections.tally(election_id, now_tick=500)
        assert tallied.result is not None
        pm_agent_id = tallied.result.pm_agent_id
        assert pm_agent_id is not None

        proposal = await charter.amendments.propose_amendment(
            "patent_filing_fee_sim", 2500, "raise it", "treasury needs it",
            pm_agent_id, now_tick=501,
        )
        await charter.amendments.cast_amendment_vote(
            proposal.proposal_id, pm_agent_id, True, now_tick=502
        )

        org = await charter.organizations.register_organization(
            OrganizationKind.PARTY, "Midflight Party", pm_agent_id, "we exist",
            LOCATION_GOVERNMENT_COMPLEX, now_tick=503,
        )

        inventor_id = adults[10]
        application = await charter.patent_office.file_application(
            inventor_id, "auto-harvest", "harvests crops automatically", ["harvest"],
            1000, LOCATION_PATENT_OFFICE, now_tick=504,
        )
        patent = await charter.patent_office.approve_application(
            application.application_id, pm_agent_id, LOCATION_GOVERNMENT_COMPLEX, now_tick=505,
        )

        second_election = await charter.elections.schedule_election(now_tick=506)
        await charter.elections.declare_candidacy(
            second_election.election_id, candidates[0], adult_facts(candidates[0]),
            now_tick=506, location=LOCATION_TOWN_HALL,
        )

        charter_history_before = list(charter.store.charter_history)
        government_before = await charter.store.get_government_state()
        elections_before = dict(charter.store.elections)
        proposals_before = dict(charter.amendments.proposals)
        organizations_before = dict(charter.store.organizations)
        patent_applications_before = dict(charter.store.patent_applications)
        patents_before = dict(charter.store.patents)

        await first.shutdown()

        second = make_runner(tmp_path)
        await second.boot(resume=True)
        restored = second.world_state.charter

        assert restored.store.charter_history == charter_history_before
        assert (await restored.store.get_government_state()) == government_before
        assert (await restored.store.get_government_state()).pm_agent_id == pm_agent_id
        assert restored.store.elections == elections_before
        assert restored.amendments.proposals == proposals_before
        assert restored.store.organizations == organizations_before
        assert restored.store.patent_applications == patent_applications_before
        assert restored.store.patents == patents_before

        restored_org = await restored.organizations.load_organization(org.org_id)
        assert restored_org.name == "Midflight Party"
        restored_patent = await restored.store.get_patent(patent.patent_id)
        assert restored_patent is not None
        assert restored_patent.owner_agent_id == inventor_id
        restored_second_election = await restored.store.get_election(second_election.election_id)
        assert restored_second_election is not None
        assert restored_second_election.phase != ElectionPhase.TALLIED
        assert candidates[0] in restored_second_election.candidates

        await second.shutdown()

    @pytest.mark.asyncio
    async def test_charter_component_is_registered_for_checkpointing(
        self, tmp_path: Path
    ) -> None:
        runner = await make_launched_runner(tmp_path)
        assert "charter" in runner.tick_engine.checkpoints.components
        assert isinstance(
            runner.tick_engine.checkpoints.components["charter"], CharterCheckpointAdapter
        )
        await runner.shutdown()


class TestMockCostSettlement:
    @pytest.mark.asyncio
    async def test_a_single_awake_outcome_never_overshoots_a_whole_cent_alone(
        self, tmp_path: Path
    ) -> None:
        runner = make_runner(tmp_path, retrieval_depth_fraction=1.0)
        await runner.boot(resume=False)
        agent_id = next(iter(runner.world_state.agents))
        runner.gateway = MockAgentGateway(runner.depth, __import__("random").Random(1))

        outcome = TickOutcome(
            agent_id=agent_id,
            reply="x",
            learning_ran=False,
            is_sleeping=False,
            input_tokens=5000,
            output_tokens=291,
            cost_usd_cents=0,
            succeeded=True,
        )
        burned_before = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        await runner.settle_outcome_cost(agent_id, outcome, tick=0)
        burned_after = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        assert burned_after == burned_before
        assert runner.counters.mock_cost_remainder_usd == pytest.approx(
            runner.depth.estimated_cost_per_call_usd
        )
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_repeated_calls_eventually_burn_a_whole_cent(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path, retrieval_depth_fraction=1.0)
        await runner.boot(resume=False)
        agent_id = next(iter(runner.world_state.agents))
        runner.gateway = MockAgentGateway(runner.depth, __import__("random").Random(1))

        outcome = TickOutcome(
            agent_id=agent_id,
            reply="x",
            learning_ran=False,
            is_sleeping=False,
            input_tokens=5000,
            output_tokens=291,
            cost_usd_cents=0,
            succeeded=True,
        )
        burned_before = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        for tick in range(5):
            await runner.settle_outcome_cost(agent_id, outcome, tick=tick)
        burned_after = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        assert burned_after > burned_before
        assert 0.0 <= runner.counters.mock_cost_remainder_usd < 0.01
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_sleeping_outcome_never_burns(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path, retrieval_depth_fraction=1.0)
        await runner.boot(resume=False)
        agent_id = next(iter(runner.world_state.agents))
        runner.gateway = MockAgentGateway(runner.depth, __import__("random").Random(1))
        outcome = TickOutcome(
            agent_id=agent_id,
            reply="",
            learning_ran=False,
            is_sleeping=True,
            input_tokens=0,
            output_tokens=0,
            cost_usd_cents=0,
            succeeded=True,
        )
        burned_before = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        for tick in range(50):
            await runner.settle_outcome_cost(agent_id, outcome, tick=tick)
        assert runner.ledger.usd_balance(BURNED_ACCOUNT_ID) == burned_before
        assert runner.counters.mock_cost_remainder_usd == 0.0
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_failed_outcome_never_burns(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path, retrieval_depth_fraction=1.0)
        await runner.boot(resume=False)
        agent_id = next(iter(runner.world_state.agents))
        outcome = TickOutcome(
            agent_id=agent_id,
            reply="",
            learning_ran=False,
            is_sleeping=False,
            input_tokens=0,
            output_tokens=0,
            cost_usd_cents=0,
            succeeded=False,
            error="dispatch failed",
        )
        burned_before = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        await runner.settle_outcome_cost(agent_id, outcome, tick=0)
        assert runner.ledger.usd_balance(BURNED_ACCOUNT_ID) == burned_before
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_real_gateway_never_burns_through_settlement(self, tmp_path: Path) -> None:
        """HttpAgentGateway's real cost is burned by broker hitting the
        embedded /ledger/debit route, never by settle_outcome_cost - this
        guards against ever double-charging real spend if a future outcome
        accidentally carried a nonzero cost_usd_cents."""
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        agent_id = next(iter(runner.world_state.agents))
        runner.gateway = HttpAgentGateway({agent_id: 9})
        outcome = TickOutcome(
            agent_id=agent_id,
            reply="x",
            learning_ran=False,
            is_sleeping=False,
            input_tokens=1000,
            output_tokens=100,
            cost_usd_cents=5,
            succeeded=True,
        )
        burned_before = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        await runner.settle_outcome_cost(agent_id, outcome, tick=0)
        assert runner.ledger.usd_balance(BURNED_ACCOUNT_ID) == burned_before
        await runner.gateway.aclose()
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_full_burst_at_max_depth_holds_ledger_invariant(self, tmp_path: Path) -> None:
        """End-to-end: run real ticks (not a hand-built TickOutcome) at
        max retrieval depth long enough to guarantee multiple whole-cent
        burns, and check the $1000 invariant still holds throughout -
        burn_for_inference enforces it internally on every call, so this
        would raise (not silently drift) if the accumulator ever broke it."""
        runner = await make_launched_runner(tmp_path, retrieval_depth_fraction=1.0)
        await runner.run_burst(40)
        assert runner.ledger.usd_balance(BURNED_ACCOUNT_ID) > 0

        wallets_total = sum(
            runner.ledger.usd_balance(agent_id) for agent_id in runner.world_state.agents
        )
        wallets_total += sum(
            runner.ledger.usd_balance(business_id)
            for business_id in runner.world_state.businesses
        )
        treasury = runner.ledger.usd_balance(TREASURY_ACCOUNT_ID)
        burned = runner.ledger.usd_balance(BURNED_ACCOUNT_ID)
        assert wallets_total + treasury + burned == TOTAL_USD_CENTS
        await runner.shutdown()


class TestLiveRetrievalDepth:
    @pytest.mark.asyncio
    async def test_changing_depth_mid_run_changes_next_tick_hearing_window(
        self, tmp_path: Path
    ) -> None:
        runner = await make_launched_runner(tmp_path, retrieval_depth_fraction=0.0)
        await runner.run_tick()
        assert runner.tick_engine.perception.hearing_window_ticks == runner.depth.hearing_window_ticks

        runner.depth.set_fraction(1.0)
        await runner.run_tick()
        assert runner.tick_engine.perception.hearing_window_ticks == runner.depth.hearing_window_ticks
        assert runner.depth.hearing_window_ticks > RetrievalDepthConfig(fraction=0.0).hearing_window_ticks
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_higher_depth_burns_faster_in_mock_mode(self, tmp_path: Path) -> None:
        shallow = await make_launched_runner(tmp_path / "shallow", retrieval_depth_fraction=0.0)
        await shallow.run_burst(30)
        shallow_burned = shallow.ledger.usd_balance(BURNED_ACCOUNT_ID)
        await shallow.shutdown()

        deep = await make_launched_runner(tmp_path / "deep", retrieval_depth_fraction=1.0)
        await deep.run_burst(30)
        deep_burned = deep.ledger.usd_balance(BURNED_ACCOUNT_ID)
        await deep.shutdown()

        assert deep_burned >= shallow_burned


class TestEmbeddedWorldApp:
    @pytest.mark.asyncio
    async def test_health_and_registry_and_ledger_routes(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        app = build_embedded_world_app(
            runner.world_state, runner.registry, runner.ledger, runner.metrics
        )
        client = TestClient(app)

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        registry = client.get("/tools/registry")
        assert registry.status_code == 200
        assert len(registry.json()) > 0
        assert all("name" in spec for spec in registry.json())

        agent_id = next(iter(runner.world_state.agents))
        payer = client.get(f"/ledger/payer/{agent_id}")
        assert payer.status_code == 200
        assert payer.json()["agent_id"] == agent_id

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        body = metrics.json()
        for key in (
            "spend_usd_cents",
            "burn_rate_usd_per_hour",
            "population",
            "births",
            "deaths",
            "elections",
            "crimes",
            "businesses",
        ):
            assert key in body
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_ledger_debit_route_burns_and_reports_balance(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        app = build_embedded_world_app(
            runner.world_state, runner.registry, runner.ledger, runner.metrics
        )
        client = TestClient(app)
        agent_id = next(iter(runner.world_state.agents))
        balance_before = runner.ledger.usd_balance(agent_id)

        response = client.post(
            "/ledger/debit",
            json={"agent_id": agent_id, "amount_cents": 1, "purpose": "test"},
        )
        assert response.status_code == 200
        assert response.json()["balance_cents_after"] == balance_before - 1
        assert runner.ledger.usd_balance(BURNED_ACCOUNT_ID) == 1
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_ledger_debit_route_rejects_insufficient_funds(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        app = build_embedded_world_app(
            runner.world_state, runner.registry, runner.ledger, runner.metrics
        )
        client = TestClient(app)
        agent_id = next(iter(runner.world_state.agents))
        balance = runner.ledger.usd_balance(agent_id)

        response = client.post(
            "/ledger/debit",
            json={"agent_id": agent_id, "amount_cents": balance + 1_000_000, "purpose": "test"},
        )
        assert response.status_code == 402
        await runner.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_tool_call_returns_400(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        app = build_embedded_world_app(
            runner.world_state, runner.registry, runner.ledger, runner.metrics
        )
        client = TestClient(app)
        agent_id = next(iter(runner.world_state.agents))

        response = client.post(
            f"/agents/{agent_id}/tool_calls", json={"tool": "no_such_tool", "input": {}}
        )
        assert response.status_code == 400


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_signals_should_exit_not_a_hard_cancel(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        await runner.start_embedded_world_server()
        assert runner.embedded_world_server is not None
        assert runner.embedded_world_server.should_exit is False

        await runner.shutdown()

        assert runner.embedded_world_server.should_exit is True
        assert runner.embedded_world_server_task is not None
        assert runner.embedded_world_server_task.done()
        assert runner.embedded_world_server_task.exception() is None

    @pytest.mark.asyncio
    async def test_shutdown_writes_a_final_checkpoint(self, tmp_path: Path) -> None:
        runner = await make_launched_runner(tmp_path)
        await runner.run_burst(3)
        await runner.shutdown()
        assert runner.checkpoint_store.latest() is not None
        assert runner.checkpoint_store.latest().tick_index == 3

    @pytest.mark.asyncio
    async def test_shutdown_drains_learning_before_it_kills_the_engines(
        self, tmp_path: Path
    ) -> None:
        """The end-of-run learning drain, and its ordering.

        A run that stops at noon leaves the whole morning's signals sitting
        in each engine's learning_signals table with processed = 0, and the
        process is killed seconds later - so the last stretch of every run
        this project has made was thrown away unconsolidated. The engine has
        had trigger_extra() since the learning gate was written and nothing
        outside a test ever called it.

        Ordering is the substance: a killed engine cannot consolidate
        anything, so the drain has to happen while the supervisor still has
        units running.
        """
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        await runner.start_agents()

        order: list[str] = []

        class RecordingGateway:
            async def send_tick(self, agent_id, tick, is_sleeping, perception):  # noqa: ANN001
                raise AssertionError("no ticks in this test")

            async def drain_learning(self) -> dict[str, bool]:
                order.append("drain")
                statuses = await runner.supervisor.list()
                assert all(s.state is UnitState.RUNNING for s in statuses)
                return {"n01": True, "n02": True, "n03": False}

            async def aclose(self) -> None:
                order.append("aclose")

        runner.gateway = RecordingGateway()

        await runner.shutdown()

        assert order[0] == "drain"
        assert runner.learning_drained == 2

    @pytest.mark.asyncio
    async def test_a_dead_engine_does_not_stop_the_others_draining(
        self, tmp_path: Path
    ) -> None:
        """Best-effort by construction. An engine that has already died must
        not keep the other forty-nine from consolidating, or keep the runner
        from shutting down at all."""
        gateway = HttpAgentGateway({"n01": 59991, "n02": 59992})

        drained = await gateway.drain_learning()

        assert drained == {"n01": False, "n02": False}
        await gateway.aclose()

    @pytest.mark.asyncio
    async def test_shutdown_stops_every_supervised_unit(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        await runner.start_agents()
        assert isinstance(runner.supervisor, MockSupervisor)
        statuses_before = await runner.supervisor.list()
        assert all(s.state is UnitState.RUNNING for s in statuses_before)

        await runner.shutdown()

        statuses_after = await runner.supervisor.list()
        assert all(s.state is UnitState.STOPPED for s in statuses_after)


class TestTickPacing:
    @pytest.mark.asyncio
    async def test_interval_is_derived_from_real_broker_capacity(self, tmp_path: Path) -> None:
        """interval = agents * fraction * CALLS PER TURN * 60 / capacity.

        CORE-12: this used to read `agents * fraction * 60 / capacity` and
        assert 22.5s, which prices a resident whole turn at ONE request. A
        turn makes up to MAX_CALLS_PER_TICK model calls plus a post-turn
        summariser; run 23 measured 2 to 12. So the real demand at 250
        residents and 0.3 due is 75 turns x ~5 calls = 375 requests a tick
        against 200 RPM, and the old number was out by that whole factor -
        the town was paced to outrun the broker it was talking to, and the
        queueing that produced looked like Bedrock being slow.
        """
        runner = make_runner(tmp_path, mock_mode=False, scheduler_target_fraction=0.3)
        runner.probe_broker_capacity = lambda: _done(200.0)  # type: ignore[assignment]

        interval = await runner.resolve_tick_interval(250)

        assert interval == pytest.approx(22.5 * DEFAULT_CALLS_PER_TURN)

    @pytest.mark.asyncio
    async def test_fewer_agents_are_allowed_to_tick_faster(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path, mock_mode=False, scheduler_target_fraction=0.3)
        runner.probe_broker_capacity = lambda: _done(200.0)  # type: ignore[assignment]

        assert await runner.resolve_tick_interval(10) == pytest.approx(
            0.9 * DEFAULT_CALLS_PER_TURN
        )

    @pytest.mark.asyncio
    async def test_a_measured_town_is_paced_on_what_it_actually_costs(
        self, tmp_path: Path
    ) -> None:
        """CORE-12 second half: once turns have run, the estimate is the
        engines own reported `steps_used` rather than the constant. A town
        of quiet residents must not be paced as though everybody thinks
        twelve times."""
        runner = make_runner(tmp_path, mock_mode=False, scheduler_target_fraction=0.3)
        runner.probe_broker_capacity = lambda: _done(200.0)  # type: ignore[assignment]
        runner.recent_steps_used.extend([1, 1, 1])

        assert await runner.resolve_tick_interval(10) == pytest.approx(0.9 * 2.0)

    @pytest.mark.asyncio
    async def test_explicit_override_is_obeyed_even_when_too_fast(self, tmp_path: Path) -> None:
        """An operator throttling a run deliberately must not be second-
        guessed - but the mismatch has to be said out loud, because the
        symptom otherwise (queueing, circuit-breaker trips) shows up a long
        way from the cause."""
        runner = make_runner(
            tmp_path, mock_mode=False, scheduler_target_fraction=0.3, tick_interval_s=0.1
        )
        runner.probe_broker_capacity = lambda: _done(200.0)  # type: ignore[assignment]

        assert await runner.resolve_tick_interval(250) == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_mock_mode_never_probes_and_never_paces(self, tmp_path: Path) -> None:
        """No Bedrock behind mock mode, so there is no capacity to respect.
        Also keeps the suite fast: probing a broker that is not running cost
        this file 8s -> 99s before the early return."""
        runner = make_runner(tmp_path, mock_mode=True)

        def explode():
            raise AssertionError("mock mode must not probe the broker")

        runner.probe_broker_capacity = explode  # type: ignore[assignment]

        assert await runner.resolve_tick_interval(250) == 0.0

    @pytest.mark.asyncio
    async def test_unreachable_broker_leaves_pacing_alone(self, tmp_path: Path) -> None:
        """Guessing a cadence from an unknown capacity would be worse than
        running unpaced and saying so."""
        runner = make_runner(tmp_path, mock_mode=False)
        runner.probe_broker_capacity = lambda: _done(None)  # type: ignore[assignment]

        assert await runner.resolve_tick_interval(250) == 0.0


async def _done_coro(value):
    return value


def _done(value):
    return _done_coro(value)


class TestGenesisGivesTheTownItsWorkshops:
    """TOOL-02, on the real boot path rather than a hand-built world.

    The `world/tools` tests construct their businesses directly, so they
    prove the mechanism and not the seeding. This proves the seeding: the
    town that actually boots has a mill that is a mill.
    """

    @pytest.mark.asyncio
    async def test_the_default_towns_cafe_is_a_kitchen(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        by_type = {b.business_type: b for b in world.businesses.values()}
        assert by_type["cafe"].provides_workshops == {"kitchen"}
        assert by_type["grocery_store"].provides_workshops == set()
        assert by_type["pharmacy"].provides_workshops == set()
        assert by_type["construction_company"].provides_workshops == {
            "carpentry",
            "sawmill",
        }

    @pytest.mark.asyncio
    async def test_the_series_cast_has_a_mill_and_a_bakery(self, tmp_path: Path) -> None:
        """genesis_20 is the cast runs 19-23 actually used."""
        runner = make_runner(tmp_path, genesis_path=Path("world/genesis_20.json"))
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        by_type = {b.business_type: b for b in world.businesses.values()}
        assert by_type["mill"].provides_workshops == {"mill"}
        assert by_type["bakery"].provides_workshops == {"kitchen"}
        assert by_type["grocery"].provides_workshops == set()

    @pytest.mark.asyncio
    async def test_the_miller_can_mill_where_genesis_put_her(
        self, tmp_path: Path
    ) -> None:
        """Run 23: `craft: requires location 'mill', actor is at
        'business:b_mill'`, twice, for the town's only miller."""
        from world.tools.place_aliases import satisfies_gate_in

        runner = make_runner(tmp_path, genesis_path=Path("world/genesis_20.json"))
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        mills = [b for b in world.businesses.values() if b.business_type == "mill"]
        assert mills, "genesis_20 seeds a mill"
        assert satisfies_gate_in(world, mills[0].location_id, "mill")

    @pytest.mark.asyncio
    async def test_the_brief_tells_a_resident_what_it_is_carrying(
        self, tmp_path: Path
    ) -> None:
        """TOOL-04, on the real brief the runner renders each tick."""
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        agent_id = next(iter(world.agents))
        world.inventory.credit(agent_id, "timber", 7)

        brief = runner.standing_brief(agent_id)
        assert brief["holding"]["timber"] == 7


class TestASentenceEnds:
    """GOV-16 / TOOL-22: a jail sentence that nothing ever released.

    `rule(verdict="jail")` sets `world.confined[agent]["until_tick"]` and
    the only other mention of that field anywhere in the tree is the
    refusal message `move` prints. So a sentence ran until a seat holder
    or an officer remembered to call `release` - and in a town whose
    government has never been seated (GOV-01), that is forever.
    `world/crime/outcomes.py` has this sweep, written and unwired, in the
    package no run constructs.

    A sentence that does not end is not a sentence. It is a
    disappearance, and it silently removes a resident from every
    measurement the run makes.
    """

    @pytest.mark.asyncio
    async def test_a_served_sentence_releases_itself(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        agent_id = next(iter(world.agents))
        world.confined[agent_id] = {
            "case_id": "case-x", "since_tick": 10, "by": "n99", "until_tick": 20,
        }

        await runner.release_served_sentences(timestamp_for(21))

        assert agent_id not in world.confined

    @pytest.mark.asyncio
    async def test_a_sentence_still_running_is_not_cut_short(
        self, tmp_path: Path
    ) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        agent_id = next(iter(world.agents))
        world.confined[agent_id] = {
            "case_id": "case-x", "since_tick": 10, "by": "n99", "until_tick": 500,
        }

        await runner.release_served_sentences(timestamp_for(21))

        assert agent_id in world.confined

    @pytest.mark.asyncio
    async def test_an_indefinite_hold_is_left_alone(self, tmp_path: Path) -> None:
        """An arrest with no sentence yet still waits for a person."""
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        agent_id = next(iter(world.agents))
        world.confined[agent_id] = {"case_id": "case-x", "since_tick": 10, "by": "n99"}

        await runner.release_served_sentences(timestamp_for(9_999))

        assert agent_id in world.confined

    @pytest.mark.asyncio
    async def test_the_release_is_on_the_record(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        agent_id = next(iter(world.agents))
        world.confined[agent_id] = {
            "case_id": "case-x", "since_tick": 10, "by": "n99", "until_tick": 20,
        }

        await runner.release_served_sentences(timestamp_for(21))

        released = [
            e for e in world.event_bus.recent()
            if e.type == "crime.release.recorded"
        ]
        assert released and released[-1].payload["agent_id"] == agent_id
        assert released[-1].payload["released_by"] == "sentence_served"

    @pytest.mark.asyncio
    async def test_the_sweep_is_actually_called_by_the_tick(
        self, tmp_path: Path
    ) -> None:
        """The four tests above call `release_served_sentences` directly, so
        every one of them passes with the call site deleted. That is this
        project's most common defect wearing a test as a disguise - a
        mechanism that is complete, correct, covered, and invoked by
        nothing (`world/crime/outcomes.py` has this exact sweep, written
        and unwired). This one drives the life tick instead.
        """
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        agent_id = next(iter(world.agents))
        world.confined[agent_id] = {
            "case_id": "case-x", "since_tick": 10, "by": "n99", "until_tick": 20,
        }

        await runner.run_life_ticks(timestamp_for(21))

        assert agent_id not in world.confined


class TestTheElectionConcludes:
    """GOV-01: the single missing call that removed politics from the town.

    `seed_genesis` schedules an election at tick 0. `tally()` has no caller
    anywhere in the tree outside the charter's own tests. So no election
    has ever reached TALLIED, `GovernmentState.pm_agent_id` has been None
    since genesis, and `registry.is_visible` hides - and
    `check_actor_requirements` refuses - all eleven GOVERNMENT-namespace
    tools for every resident, in every run ever made.

    The run reports read that as "the town never governed itself". It was
    one missing call.
    """

    @pytest.mark.asyncio
    async def test_a_vote_that_closes_seats_a_government(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        elections = await world.charter.store.list_elections()
        election = elections[0]
        residents = [
            a for a, rec in world.agents.items() if rec.age_years >= 18.0
        ][:3]
        candidate = residents[0]

        election.candidates[candidate] = Candidacy(
            agent_id=candidate, declared_tick=election.candidacy_opens_tick
        )
        election.phase = ElectionPhase.VOTING_OPEN
        for voter in residents[1:]:
            election.ballots[voter] = candidate
        await world.charter.store.put_election(election)

        await runner.charter_tick(timestamp_for(election.voting_closes_tick + 1))

        state = await world.charter.store.get_government_state()
        assert state.pm_agent_id == candidate

    @pytest.mark.asyncio
    async def test_the_cycle_keeps_turning(self, tmp_path: Path) -> None:
        """DESIGN section 7's "every 3 game-days" needs a second election.

        Only `seed_genesis` ever called `schedule_election`, so the town
        had exactly one election in its entire history, permanently stuck
        before its own vote.
        """
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        first = (await world.charter.store.list_elections())[0]
        first.phase = ElectionPhase.VOTING_OPEN
        await world.charter.store.put_election(first)

        await runner.charter_tick(timestamp_for(first.voting_closes_tick + 1))

        elections = await world.charter.store.list_elections()
        assert len(elections) == 2
        successor = next(e for e in elections if e.election_id != first.election_id)
        assert successor.voting_closes_tick > first.voting_closes_tick

    @pytest.mark.asyncio
    async def test_the_tick_loop_actually_calls_it(self, tmp_path: Path) -> None:
        """The two tests above call `charter_tick` directly and would pass
        with its call site deleted - the defect this repository has more of
        than any other. This drives the life tick instead."""
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        election = (await world.charter.store.list_elections())[0]
        election.phase = ElectionPhase.VOTING_OPEN
        await world.charter.store.put_election(election)

        await runner.run_life_ticks(timestamp_for(election.voting_closes_tick + 1))

        refreshed = await world.charter.store.get_election(election.election_id)
        assert refreshed is not None and refreshed.phase is ElectionPhase.TALLIED

    @pytest.mark.asyncio
    async def test_a_resident_is_told_the_election_exists(
        self, tmp_path: Path
    ) -> None:
        """GOV-17: `vote` and `declare_candidacy` both require an
        `election_id`, and nothing in the town would ever tell a resident
        one. Both civic verbs were unusable even where the seats were
        filled."""
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        await runner.charter_tick(timestamp_for(1))
        agent_id = next(iter(world.agents))
        notice = runner.civic_notice_for(agent_id)

        assert notice is not None
        election = (await world.charter.store.list_elections())[0]
        assert notice["election_id"] == election.election_id
        assert notice["you_have_voted"] is False
        assert notice["tax_rate_bps"] > 0

    @pytest.mark.asyncio
    async def test_the_notice_says_when_nobody_governs(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        assert runner.world_state is not None

        await runner.charter_tick(timestamp_for(1))
        notice = runner.civic_notice_for(next(iter(runner.world_state.agents)))

        assert notice is not None and notice.get("seats_vacant") is True


class TestTheVetoWindowCloses:
    """GOV-03, operator decision 2026-09-03: the President is a veto
    window, not a hard gate.

    `sign_amendment` is reachable only from `admin_api.py`'s operator
    route. As a hard gate that means an unattended run can never amend its
    own charter - the only signature is the operator's and the operator is
    not there - so "what does a town do with its own constitution" becomes
    a question about human availability rather than about the town.
    """

    @pytest.mark.asyncio
    async def test_an_unvetoed_amendment_signs_itself(self, tmp_path: Path) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        seats = list(world.agents)[:5]
        await world.charter.store.set_government_state(
            GovernmentState(pm_agent_id=seats[0], minister_agent_ids=seats[1:])
        )
        proposal = await world.charter.amendments.propose_amendment(
            field_name="election_interval_days",
            new_value=6.0,
            title="Longer terms",
            rationale="because",
            proposed_by_agent_id=seats[0],
            now_tick=10,
        )
        charter = await world.charter.store.get_charter()
        for seat in seats[: charter.amendment_votes_required]:
            await world.charter.amendments.cast_amendment_vote(
                proposal.proposal_id, seat, True, 11
            )
        assert proposal.status is AmendmentStatus.PASSED_AWAITING_PRESIDENT

        await runner.close_the_veto_window(timestamp_for(12))

        assert proposal.status is AmendmentStatus.ENACTED
        assert (await world.charter.store.get_charter()).election_interval_days == 6.0

    @pytest.mark.asyncio
    async def test_a_long_window_still_waits_for_a_person(
        self, tmp_path: Path
    ) -> None:
        runner = make_runner(tmp_path, veto_window_ticks=1_000)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        seats = list(world.agents)[:5]
        await world.charter.store.set_government_state(
            GovernmentState(pm_agent_id=seats[0], minister_agent_ids=seats[1:])
        )
        proposal = await world.charter.amendments.propose_amendment(
            field_name="election_interval_days", new_value=6.0, title="t",
            rationale="r", proposed_by_agent_id=seats[0], now_tick=10,
        )
        charter = await world.charter.store.get_charter()
        for seat in seats[: charter.amendment_votes_required]:
            await world.charter.amendments.cast_amendment_vote(
                proposal.proposal_id, seat, True, 11
            )

        await runner.close_the_veto_window(timestamp_for(12))

        assert proposal.status is AmendmentStatus.PASSED_AWAITING_PRESIDENT

    @pytest.mark.asyncio
    async def test_a_proposal_the_seats_rejected_is_never_signed(
        self, tmp_path: Path
    ) -> None:
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        seats = list(world.agents)[:5]
        await world.charter.store.set_government_state(
            GovernmentState(pm_agent_id=seats[0], minister_agent_ids=seats[1:])
        )
        proposal = await world.charter.amendments.propose_amendment(
            field_name="election_interval_days", new_value=6.0, title="t",
            rationale="r", proposed_by_agent_id=seats[0], now_tick=10,
        )
        for seat in seats:
            await world.charter.amendments.cast_amendment_vote(
                proposal.proposal_id, seat, False, 11
            )
        assert proposal.status is AmendmentStatus.REJECTED

        await runner.close_the_veto_window(timestamp_for(9_999))

        assert proposal.status is AmendmentStatus.REJECTED

    @pytest.mark.asyncio
    async def test_the_tick_loop_actually_closes_it(self, tmp_path: Path) -> None:
        """As ever: the tests above would pass with the call site deleted."""
        runner = make_runner(tmp_path)
        await runner.boot(resume=False)
        world = runner.world_state
        assert world is not None

        seats = list(world.agents)[:5]
        await world.charter.store.set_government_state(
            GovernmentState(pm_agent_id=seats[0], minister_agent_ids=seats[1:])
        )
        proposal = await world.charter.amendments.propose_amendment(
            field_name="term_limit_terms", new_value=4, title="t",
            rationale="r", proposed_by_agent_id=seats[0], now_tick=10,
        )
        charter = await world.charter.store.get_charter()
        for seat in seats[: charter.amendment_votes_required]:
            await world.charter.amendments.cast_amendment_vote(
                proposal.proposal_id, seat, True, 11
            )

        await runner.run_life_ticks(timestamp_for(12))

        assert proposal.status is AmendmentStatus.ENACTED
