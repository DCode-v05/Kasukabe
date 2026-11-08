"""A completed run can be told apart from another one.

`INFRA-09` / `EVAL-24`. The trace recorded a run's results in detail and
almost nothing about the run. Present: `population_seed` buried in a
checkpoint's `rng_state`, per-row model and region, and a `run_id` that
appeared in no table. Absent: the git commit of `app/` and `engine/`
(a grep for `git`, `commit`, `code_hash` or `__version__` across
`world/*.py` and `engine/src/sim/*.py` found nothing), the genesis file's
hash, the price table in force, the spend cap, and every `KASUKABE_*`
knob that changes what a run means.

The cost is not hypothetical. Runs 19-24 are already uncomparable to each
other in ways nobody can now reconstruct, because the builds differed and
nothing wrote down how. Every cost figure in those traces is a number
whose price table is a guess.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.run_manifest import RECORDED_ENV, build_manifest, file_digest, git_commit

APP = Path(__file__).resolve().parents[2]
SCHEMA = APP / "supabase" / "sqlite_schema.sql"


def _manifest(tmp_path, **over):
    config = GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        **over,
    )
    return build_manifest(
        run_id="run-under-test",
        config=config,
        app_root=APP,
        engine_root=None,
        started_at=1.0,
        environ={"SPEND_CAP_USD": "2.50", "IRRELEVANT": "x"},
    )


def test_it_records_which_code_ran(tmp_path) -> None:
    """The single largest gap: nothing anywhere recorded the commit."""
    manifest = _manifest(tmp_path)
    assert "app_commit" in manifest
    if git_commit(APP) is not None:
        assert manifest["app_commit"], (
            "INFRA-09: this checkout has git metadata and the manifest "
            "recorded no commit, so nothing says which code produced the run"
        )


def test_an_edited_tree_says_so(tmp_path) -> None:
    """`--dirty` matters more than the hash: a run made from an edited
    working tree is not reproducible from ANY commit, and that is the fact
    a reader needs."""
    import inspect

    source = inspect.getsource(git_commit)
    assert "--dirty" in source


def test_the_cast_is_identified_by_content_not_by_name(tmp_path) -> None:
    """`genesis_20.json` has been regenerated more than once in this
    project's history, so a filename says nothing about which cast ran."""
    genesis = tmp_path / "g.json"
    genesis.write_text('{"population": 2}', encoding="utf-8")
    manifest = _manifest(tmp_path, genesis_path=genesis)

    assert manifest["genesis_sha256"] == file_digest(genesis)
    genesis.write_text('{"population": 3}', encoding="utf-8")
    assert _manifest(tmp_path, genesis_path=genesis)["genesis_sha256"] != manifest["genesis_sha256"]


def test_the_price_table_in_force_is_recorded(tmp_path) -> None:
    """Without it, every cost figure in the trace is a number with no
    units - a run priced at one table cannot be compared with a run priced
    at another."""
    from broker.config import MODEL_ALLOWLIST

    prices = _manifest(tmp_path)["model_prices_usd_per_million"]
    assert set(prices) == set(MODEL_ALLOWLIST)
    for model, pair in prices.items():
        assert pair["input"] > 0 and pair["output"] > 0, model


def test_only_the_knobs_that_were_set_appear(tmp_path) -> None:
    """A key present with a null value would say "explicitly unset", which
    is not the same as "left at its default"."""
    env = _manifest(tmp_path)["env"]
    assert env == {"SPEND_CAP_USD": "2.50"}
    assert "IRRELEVANT" not in env


def test_the_knob_list_covers_what_the_review_named(tmp_path) -> None:
    for name in ("SPEND_CAP_USD", "KASUKABE_CONTEXT_WINDOW",
                 "KASUKABE_LOCAL_TOOL_SCHEMAS", "KASUKABE_TRACE_BODY_LIMIT",
                 "KASUKABE_TICK_WALLET_FRACTION_BPS", "KASUKABE_ABLATION_ARM"):
        assert name in RECORDED_ENV, f"INFRA-09 names {name} and it is not recorded"


def test_which_service_tier_was_paid_for_is_recorded(tmp_path) -> None:
    """Flex is half of Standard and may be slower. A run on one is neither
    the same bill nor the same latency profile as a run on the other."""
    assert "BEDROCK_SERVICE_TIER" in RECORDED_ENV


def test_how_deep_tools_may_stand_is_recorded(tmp_path) -> None:
    """A run where an invented tool may stand on two others is a different
    experiment from one where it may stand on none (TOOL-31)."""
    assert "KASUKABE_MAX_TOOL_DEPTH" in RECORDED_ENV


def test_how_long_a_turn_was_allowed_to_be_is_recorded(tmp_path) -> None:
    """A tick capped at 5 model calls and one capped at 12 are different
    experiments: the 50-resident run averaged 4.85 tool calls a turn, and
    a quarter of its calls sat above a ceiling of 5."""
    assert "KASUKABE_MAX_CALLS_PER_TICK" in RECORDED_ENV
    assert "KASUKABE_MIN_CALLS_PER_TICK" in RECORDED_ENV


def test_whether_old_perceptions_were_resent_is_recorded(tmp_path) -> None:
    """A run that re-sends six superseded pictures of the world on every call
    and one that does not have different bills and different prompts."""
    assert "KASUKABE_OLD_PERCEPTIONS_IN_FULL" in RECORDED_ENV


def test_where_a_turn_record_sits_is_recorded(tmp_path) -> None:
    """Run 28b's records sat in the resident's slot and 43% of first calls
    copied one. A run that moved them and a run that did not are different
    experiments (engine/src/sim/resident_voice.py)."""
    assert "KASUKABE_RECORDS_IN_WORLD_VOICE" in RECORDED_ENV


def test_an_unknown_fact_is_null_not_a_guess(tmp_path) -> None:
    """"Unknown" and "the default" are different answers, and a manifest
    that confuses them is worse than no manifest."""
    manifest = _manifest(tmp_path, genesis_path=tmp_path / "not-here.json")
    assert manifest["genesis_sha256"] is None
    assert manifest["engine_commit"] is None, (
        "no engine repo was given, and a hash invented for one would be "
        "worse than admitting the run cannot say which engine it used"
    )


def test_the_table_exists(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert "run_manifest" in tables


@pytest_asyncio.fixture
async def booted(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    await runner.attach_persistence()
    return runner, tmp_path


@pytest.mark.asyncio
async def test_a_booted_run_writes_its_manifest(booted) -> None:
    """The whole finding: the mechanism must have a caller. A test of
    `build_manifest` alone would pass before the fix and after it."""
    _runner, tmp_path = booted

    written = list((tmp_path / "checkpoints").glob("manifest-*.json"))
    assert written, (
        "INFRA-09: the run booted and left no manifest, so what it was "
        "cannot be recovered afterwards"
    )
    manifest = json.loads(written[0].read_text(encoding="utf-8"))
    assert manifest["run_id"]
    assert manifest["model_prices_usd_per_million"]


@pytest.mark.asyncio
async def test_it_is_written_at_boot_not_at_stop(booted) -> None:
    """The runs that most need explaining are the ones that did not
    finish; a manifest written on the way out is missing from exactly
    those."""
    runner, tmp_path = booted
    assert list((tmp_path / "checkpoints").glob("manifest-*.json")), (
        "the manifest only appears after a clean stop"
    )


@pytest.mark.asyncio
async def test_a_manifest_failure_does_not_stop_the_run(tmp_path, monkeypatch) -> None:
    """Describing a run must not be able to end it."""
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()

    import world.run_manifest as rm

    monkeypatch.setattr(rm, "build_manifest", lambda **kw: (_ for _ in ()).throw(RuntimeError("no")))
    await runner.attach_persistence()
    assert runner.world_state is not None
