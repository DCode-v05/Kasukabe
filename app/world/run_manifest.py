"""What a run WAS: the facts you need to repeat it, written down once.

`INFRA-09` / `EVAL-24`. A run recorded its own results in detail and
almost nothing about itself. Present in the trace: `population_seed`
inside a checkpoint's `rng_state`, per-row `model`/`requested_model`/
`region`, and a `run_id` the report reads back off `/world/state` and
which appears in no table. Absent:

  * the git commit of `app/` and of `engine/` - a grep for `git`,
    `commit`, `code_hash` or `__version__` across `world/*.py` and
    `engine/src/sim/*.py` returned nothing at all;
  * the genesis file's hash, so "the same cast" is a claim about a
    filename;
  * the price table in force, which turns every cost figure in the trace
    into a number with no units;
  * the spend cap, the context window, the tick interval, the local-tool
    -schema switch, the learn-awake window, the trace body limit, the
    tick wallet fraction, the ablation arm;
  * the inference-profile ARN actually invoked. `bedrock.py` swaps
    `modelId` for a profile ARN before the call and the trace keeps the
    logical id, so the row says which model was asked for and not which
    endpoint answered.

Reproducibility is the whole claim the roadmap makes for this trace
store, and none of the above could be recovered from it afterwards. The
cost is not hypothetical: runs 19-24 are already uncomparable to each
other in ways nobody can now reconstruct, because the builds differed and
nothing said how.

WHAT THIS IS NOT. It is not a checkpoint and it does not make a run
resumable - the engines' own `brain.sqlite` files are still not archived
(that is the second half of INFRA-09, and it belongs to the runner's stop
path). This is the smaller, cheaper half: enough to say what a completed
run was, so two runs can be compared honestly or a third can be set up
the same way.

Every value is READ, never guessed. A fact this module cannot establish
is recorded as null, because "unknown" and "the default" are different
answers and a manifest that confuses them is worse than no manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# The knobs that change what a run MEANS, as opposed to where it puts its
# files. A run that differs from another in any of these is not a repeat
# of it, and the trace could not previously tell you which.
#
# Deliberately a list rather than "every KASUKABE_* in the environment":
# the environment of a developer's shell carries a great deal that has
# nothing to do with the run, and a manifest full of noise is one nobody
# reads.
RECORDED_ENV = (
    "SPEND_CAP_USD",
    "KASUKABE_BUDGET_CAP_CENTS",
    "KASUKABE_CONTEXT_WINDOW",
    # How many stored messages a turn re-reads. With no prompt caching every
    # one is paid for on every call, so two runs with different windows have
    # different bills for the same behaviour (docs/decisions/NO-PROMPT-CACHE-COST.md).
    "KASUKABE_HISTORY_MESSAGES",
    # Whether a stored turn record reaches the model in the resident's slot or
    # in the world's (engine/src/sim/resident_voice.py). Run 28b: in the
    # resident's slot, 43% of first calls after one copied it.
    "KASUKABE_RECORDS_IN_WORLD_VOICE",
    # Whether older perceptions reach the model in full or cut to their first
    # line (engine/src/sim/resident_voice.py). Run 29: 61% of the world's
    # message late in the run was superseded perceptions.
    "KASUKABE_OLD_PERCEPTIONS_IN_FULL",
    # How often the run read itself into the checkpoint directory while it
    # ran. A run that captured a roster every game-day and one that only
    # tried on the way out are not equally recoverable.
    "KASUKABE_READING_EVERY_TICKS",
    # Who pays for learning and summarising when a resident's wallet is
    # empty. The 50-resident run refused 58 such calls; a run that let the
    # treasury cover them keeps memories that run threw away.
    "KASUKABE_HOUSEKEEPING_PAYER",
    # Whether a sale needs buyer and seller to be talking. A town where you
    # can buy from a stranger across the square, or from an empty shop, is a
    # different economy from one where you cannot.
    "KASUKABE_SALES_NEED_CONVERSATION",
    # TOOL-31: how many layers of invented tools a filing may stand on. A
    # run where tools could compound three deep is not a repeat of one
    # where they could not (docs/decisions/TOOLS-THAT-STAND-ON-TOOLS.md).
    "KASUKABE_MAX_TOOL_DEPTH",
    "KASUKABE_LOCAL_TOOL_SCHEMAS",
    "KASUKABE_LEARN_AWAKE_TICKS",
    "KASUKABE_TRACE_BODY_LIMIT",
    "KASUKABE_TICK_WALLET_FRACTION_BPS",
    # The floor and ceiling on model calls in one resident tick
    # (world/call_budget.py). The 50-resident run averaged 4.85 tool calls
    # a turn against a ceiling of 12; a run at 5 and a run at 12 are
    # different experiments and nothing recorded which had been set.
    "KASUKABE_MIN_CALLS_PER_TICK",
    "KASUKABE_MAX_CALLS_PER_TICK",
    "KASUKABE_RETRIEVAL_DEPTH",
    "KASUKABE_ABLATION_ARM",
    "KASUKABE_TICK_INTERVAL_S",
    "BEDROCK_INFERENCE_PROFILES",
    # Which Bedrock service tier the run asked for. Flex is half the price
    # of Standard and may take longer, so two runs on different tiers are
    # neither the same bill nor the same latency profile.
    "BEDROCK_SERVICE_TIER",
    # INFRA-15: how many Bedrock streams may be read at once. Changes
    # throughput, so it changes what a run means.
    "KASUKABE_STREAM_POOL_WORKERS",
)


def git_commit(repo: Path) -> str | None:
    """The commit this code is, or None.

    None is a real answer and is recorded as one: a deployment from a
    tarball has no git metadata, and writing "unknown" there is honest
    where inventing a hash would not be. `--dirty` matters more than the
    hash: a run made from an edited tree is not reproducible from any
    commit, and that is exactly the thing a reader needs told.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "describe", "--always", "--dirty", "--abbrev=40"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def file_digest(path: Path | None) -> str | None:
    """sha256 of a file, or None when there is no file.

    The genesis hash is the one that decides whether two runs had the same
    cast. A filename does not: `genesis_20.json` has been regenerated
    more than once in this project's history.
    """
    if path is None or not Path(path).is_file():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _recorded_env(env: "dict[str, str]", config: Any) -> "dict[str, str]":
    """The knobs that were SET, plus where this run's engine homes are when
    somebody chose them.

    Only what was set: a key present for a knob left at its default would
    say "explicitly chosen", which is a different answer
    (`test_only_the_knobs_that_were_set_appear`).

    The home root is the one knob that can be set without being exported.
    Run 26 was booted with `/tmp/kasukabe-agents-run26` and its report,
    finding nothing in the manifest, fell back to `/tmp/kasukabe-agents` and
    described run 25's cast - "0 of 23 residents acted" over six residents
    and 452 tool calls. So a home root that differs from the config's own
    default is recorded even when only the config carries it; one left at
    the default is not, and the report's fallback is then already right.
    The default is read off the config's field, not typed here, so the two
    cannot drift.
    """
    import dataclasses

    recorded = {name: env[name] for name in RECORDED_ENV if name in env}
    home_root = env.get("KASUKABE_HOME_ROOT")
    if home_root is None and config is not None and dataclasses.is_dataclass(config):
        defaults = {f.name: f.default for f in dataclasses.fields(config)}
        default_root = defaults.get("home_root", dataclasses.MISSING)
        chosen = str(config.home_root)
        if default_root is not dataclasses.MISSING and chosen != str(default_root):
            home_root = chosen
    if home_root:
        recorded["KASUKABE_HOME_ROOT"] = home_root
    return recorded


def build_manifest(
    *,
    run_id: str,
    config: Any,
    app_root: Path,
    engine_root: Path | None = None,
    started_at: float,
    environ: "dict[str, str] | None" = None,
) -> dict[str, Any]:
    """One row's worth of "what this run is"."""
    from broker.config import MODEL_ALLOWLIST, MODEL_PRICES_USD_PER_MILLION

    env = dict(os.environ if environ is None else environ)
    # Read directly, not via getattr with a default.
    #
    # `test_no_silent_defaults` is right to object to the three-argument
    # form here and the reason is this module's own rule: a renamed field
    # would make every one of these record `None`, and `None` in this
    # manifest means "could not be established". A manifest that reports
    # a rename as "unknown" is exactly the confusion the docstring above
    # says it exists to prevent.
    genesis_path = config.genesis_path

    return {
        "run_id": run_id,
        "started_at": started_at,
        # Where this run put its files. The one fact in the trace that lets a
        # report written after the stop find the run's checkpoints, its engine
        # -home archive and the final reading taken on the way down - without
        # which the 50-agent run's report said "The world reported no roster"
        # about 50 residents it had been watching a second earlier.
        "checkpoint_dir": str(config.checkpoint_dir),
        # ── what code ran ──────────────────────────────────────────────
        "app_commit": git_commit(app_root),
        "engine_commit": git_commit(engine_root) if engine_root else None,
        "python_version": sys.version.split()[0],
        # ── what cast ──────────────────────────────────────────────────
        "genesis_path": str(genesis_path) if genesis_path else None,
        "genesis_sha256": file_digest(genesis_path),
        "population_seed": config.population_seed,
        # ── what it was allowed to spend, and at what prices ───────────
        "budget_cap_cents": config.budget_cap_cents,
        "model_allowlist": list(MODEL_ALLOWLIST),
        "model_prices_usd_per_million": {
            model: {"input": prices[0], "output": prices[1]}
            for model, prices in MODEL_PRICES_USD_PER_MILLION.items()
        },
        # ── the knobs ──────────────────────────────────────────────────
        # Only the ones actually set. A key present with a null value
        # would say "explicitly unset", which is not the same as "left at
        # its default", and the difference is the whole point.
        "env": _recorded_env(env, config),
        "tick_interval_s": config.tick_interval_s,
        "mock_mode": config.mock_mode,
        "world_mode": config.world_mode,
        "supervisor_backend": config.supervisor_backend,
    }


def write_manifest(manifest: dict[str, Any], directory: Path) -> Path:
    """Beside the run's other output, as JSON, immediately at boot.

    At BOOT rather than at stop, because the runs that most need
    explaining are the ones that did not finish - a manifest written on
    the way out is missing from exactly those.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"manifest-{manifest['run_id']}.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path
