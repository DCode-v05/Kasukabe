"""Sim profile: one agent's env, pointed at the local broker.

Turns a small, per-agent SimProfile into the exact env-var map
src.config.Settings already knows how to parse (CDT_URL, CDT_KEY, LLM_MODEL,
...) plus the one code-only field env vars cannot carry (Settings.deny_rules,
see src/config.py's ENVLESS_FIELDS) - the rule that makes spawn_sub_agent
structurally unreachable (task 3, checked in pinned_boot.py's docstring and
tests/test_sim_fake_broker.py).

Every field here maps to a REAL Settings field by its REAL env var name.
This module never invents new config surface: it is a value factory for
existing knobs, not a second config system.
"""

from __future__ import annotations

import os

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from src.config import Settings
from src.sim.model_profile import ALLOWED_MODEL_IDS, MODEL_ID

# Tools a pinned sim thread must never be able to call. spawn_sub_agent and
# spawn_thread are the two names src/engine_core/thread.py's own
# PARENT_STATE_TOOLS frozenset flags as enabling "recursive fan-out";
# collect/resume only operate on an already-spawned agent, so denying the
# spawn itself makes them moot, but they are listed too as defense in depth
# and because they cost nothing to deny.
FORK_DENIED_TOOLS: tuple[str, ...] = (
    "spawn_sub_agent",
    "spawn_thread",
    "collect_sub_agent_result",
    "resume_sub_agent",
)


def pinned_thread_id(agent_id: str) -> str:
    """The one thread id this engine instance will ever execute a turn on.

    Deterministic from agent_id (not a random uuid like coordinator.py mints
    for triage-opened threads) so a restarted engine reattaches to the same
    thread_id its checkpoint / conversation row already used.
    """
    return f"kasukabe-agent-{agent_id}"


def fork_deny_rules() -> list[dict[str, str]]:
    """The Settings.deny_rules value that makes spawn_sub_agent impossible.

    Settings.deny_rules is a list of {tool, pattern, reason} dicts (see
    src/config.py: "code/test-only, no env var" - ENVLESS_FIELDS). thread.py's
    initiate() loads it into self.deny_rules; execute_tool_call's
    check_permission() runs DENY rules (step 2 of its pipeline, see
    src/sandbox/permissions.py's check_permission docstring) BEFORE the tool
    handler ever runs - before register_spawn_sub_agent_tool's handler
    constructs a SubAgent, resolves a model tier, or calls create_llm. An
    empty pattern matches every input for the named tool (see match_rule):
    the rule denies the tool outright, not a subset of its arguments.
    """
    return [
        {"tool": name, "pattern": "", "reason": "Kasukabe sim: coordinator/sub-agent forking is disabled (DESIGN.md section 4, task FORK_SUBAGENT=false)"}
        for name in FORK_DENIED_TOOLS
    ]


@dataclass
class SimProfile:
    """One agent's identity + broker coordinates for a Kasukabe engine boot."""

    agent_id: str
    broker_base_url: str
    broker_api_key: str = "kasukabe-sim-broker"
    # Root directory under which this agent's SEPTEMBER_HOME
    # (<home_root>/<agent_id>) lives. Each agent gets its own root: engines
    # run one microVM each (DESIGN.md section 4) and must never share a
    # SQLite file.
    home_root: str = "/tmp/kasukabe-agents"
    # Extra deny rules beyond FORK_DENIED_TOOLS (e.g. a world-specific
    # lockout). Merged after the fork-block rules.
    extra_deny_rules: list[dict[str, str]] = field(default_factory=list)

    @property
    def september_home(self) -> str:
        return str(Path(self.home_root).expanduser() / self.agent_id)

    @property
    def thread_id(self) -> str:
        return pinned_thread_id(self.agent_id)


# How many tokens any single model response may run to.
#
# MEASURED, iteration 5 of the autonomous loop, and again in run 19. The
# engine default is 4096, roughly 16,000 characters - enough for a short
# story, and residents were writing one. Corwin Thorne averaged 18,547
# characters a turn across thirteen turns and acted on two of them, while a
# live offer of 175 sim for five units of timber sat unanswered in his
# perception. Across that run 90 of 115 ticks produced no action at all.
#
# Run 19 says the same in money: 387 calls ended without a tool call, at
# $1.95 - 48% of everything the town spent - and ten more were cut off
# mid-sentence at the 4096 ceiling, paid for and unusable.
#
# The budget is PER MODEL CALL, not per tick, so this bounds how long one
# response may be without limiting how many times the agent loop may
# iterate: a turn that genuinely needs eleven tool calls still gets eleven
# model calls to make them. The floor is set well above a few tool calls'
# worth of tokens so that bounding a monologue never truncates real work.
#
# WHY THIS AND NOT A PROMPT. The prompt levers are already in - law rule 6
# says narrated words reach nobody, and every perception closes with
# "nothing you have written above has happened yet". What neither can do is
# make an agent FEEL the cost of a 4,000-token monologue, and DESIGN.md's
# economics say it should: thinking is billed per token against the agent's
# own wallet. This is that economics made structural rather than advisory.
# Halved again, doc 01 fix 6. 1536 is roughly 6,000 characters and run 23
# still returned 152 one-turn prose sessions out of 349. The second cost is
# ENG-21's: a monologue that reaches the cap triggers a MAX_TOKENS
# continuation, which is a second billed call, which exhausts a two-call
# budget - so the resident is told it "ran out of energy for thinking after
# 2 steps" having attempted nothing at all.
#
# The doc says "around 600". 768 is where this file's own floor sits, with
# a measured reason behind it (a world tool call serialises to a couple of
# hundred tokens; bounding a monologue must not truncate real work). Where
# the doc's round number and the measured floor disagree the floor wins,
# and closing the last 168 tokens is the operator's call, not this edit's.
SIM_MAX_RESPONSE_TOKENS = 768


# How many WAKING ticks may pass before the learning batch runs anyway.
#
# `SimLearningGate` has carried an `awake_interval_ticks` field since the
# learning-cadence work, defaulting to 0 - sleep-onset only - and NOTHING
# EVER SET IT. pinned_boot.py constructed the gate with the default, so the
# waking interval was dead code and `learning_ran` stayed zero for exactly
# the reason it always had: every run this project has made begins at dawn
# and the spend cap ends it before dusk, so the wake->asleep edge the gate
# waits for has never once occurred. Three run reports read that as "the
# residents did not learn". A field is not a fix until a call site passes it.
#
# 18 ticks is three game-hours, so a six-hour morning consolidates twice.
# That is the learning-cadence spec's 30-minute snapshot expressed in the
# only clock this sim has: fast enough that memory does not drift across a
# run, slow enough that fifty engines are not each running a batch every
# tick. It does NOT replace the night - falling asleep still consolidates
# whatever happened since the last batch.
#
# KASUKABE_LEARN_AWAKE_TICKS=0 restores sleep-onset-only exactly, which is
# the control arm for any claim that the interval changed what was learned.
DEFAULT_LEARN_AWAKE_INTERVAL_TICKS = 18


def ablation_arm(env: "Mapping[str, str] | None" = None) -> str:
    """Which research arm this engine's resident is in.

    Set per engine by the runner from the genesis cast
    (`GenesisRunner.ablation_arm_for`). "full" when nobody said, which is
    the honest default - a resident whose arm nobody recorded is not a
    control for anything.
    """
    source = os.environ if env is None else env
    return (source.get("KASUKABE_ABLATION_ARM") or "full").strip().lower() or "full"


def resident_learns(env: "Mapping[str, str] | None" = None) -> bool:
    """False only for the `no_learning` arm.

    Deliberately an allow-by-default: an unrecognised arm name learns.
    Silently switching a resident's learning off because of a typo in an
    env var would put an untreated resident in the treated group and there
    would be nothing in the trace to show it.
    """
    return ablation_arm(env) != "no_learning"


def learn_awake_interval_ticks(env: "Mapping[str, str] | None" = None) -> int:
    """Waking ticks between learning batches. 0 disables the interval."""
    source = os.environ if env is None else env
    raw = source.get("KASUKABE_LEARN_AWAKE_TICKS")
    if raw is None or not raw.strip():
        return DEFAULT_LEARN_AWAKE_INTERVAL_TICKS
    try:
        return max(0, int(raw))
    except ValueError:
        # A typo must not silently turn learning off - that is the failure
        # this whole constant exists to end.
        return DEFAULT_LEARN_AWAKE_INTERVAL_TICKS


def sim_env(profile: SimProfile) -> dict[str, str]:
    """The env-var map for this agent, as Settings.build_from_env expects it.

    Every model role Settings can resolve (src/herald/defaults.py's
    ROLE_SETTINGS_FIELD) - LLM, PLANNER, LIGHT, DISPATCH,
    DISPATCH_ALTERNATIVE, LC - is pinned to the ONE allowed id, MODEL_ID.
    That is the task's "Kimi K2.5 is the ONLY model for EVERY herald
    consumer" made concrete: main thread (LLM/PLANNER/LIGHT), learning
    centre (LC), and the dispatch roles, all the same string. FALLBACK_MODEL
    is left empty on purpose - a fallback target is itself a second model to
    fall back TO, which is exactly the "model-choice mechanic" DESIGN.md
    section 4 rules out; an empty fallback means a transient overload is
    surfaced as an error, not silently retried on a different model.

    EMBEDDING is left unset: pinned_boot.py never builds an
    embedding_service (no embedder is wired into working memory), so
    ModelRole.EMBEDDING is never resolved by anything the sim boots - same
    reasoning as ModelRole.REASONING (see pinned_boot.py's module
    docstring). Setting it to a real id here would be documentation for a
    code path that does not exist.

    DISPATCH / DISPATCH_ALTERNATIVE are set even though pinned_boot.py never
    calls Coordinator.set_router (so ThreadRouter is never constructed and
    these ids are never resolved either): keeping them pinned to MODEL_ID is
    a second line of defense if that ever changes, at zero cost today - and,
    unlike leaving them at their production defaults, it cannot introduce a
    second model id into the process even latently.
    """
    return {
        "CDT_URL": profile.broker_base_url,
        "CDT_KEY": profile.broker_api_key,
        "SEPTEMBER_HOME": profile.september_home,
        "LLM_MODEL": MODEL_ID,
        "PLANNER_MODEL": MODEL_ID,
        "LIGHT_MODEL": MODEL_ID,
        "DISPATCH_MODEL": MODEL_ID,
        "DISPATCH_MODEL_ALTERNATIVE": MODEL_ID,
        "FALLBACK_MODEL": "",
        "LC_MODEL": MODEL_ID,
        # How long any ONE model response may be. See
        # SIM_MAX_RESPONSE_TOKENS for the measurement behind the number.
        "MAX_TOKENS": str(SIM_MAX_RESPONSE_TOKENS),
        # A sim host has no per-user settings files to trust; loading them
        # would make the sim's tool behavior depend on host filesystem state
        # instead of only on this profile.
        "HOOKS_SETTINGS_LOAD_ENABLED": "false",
    }


def raw_process_env(profile: SimProfile) -> dict[str, str]:
    """Process env vars that bypass Settings entirely - read straight off
    ``os.environ`` by the modules that own them, on every call, with no
    caching. Settings.build_from_env cannot set these: it only reads keys
    that match one of Settings' own declared ``env=`` field names (see
    src/config.py's field loop), so putting either of these in sim_env()'s
    dict would silently do nothing - build_settings() applies this dict to
    the real process environment instead.

        FORK_SUBAGENT              src/engine_core/fork_subagent.
                                    is_fork_subagent_enabled(). "false" is
                                    already its hardcoded default; set
                                    explicitly so the profile is
                                    self-documenting and validate_sim_settings
                                    can check it, rather than depending on
                                    that default never changing.
        FEATURE_TOOL_USE_SUMMARY   src/engine_core/tool_use_summary.py fires
                                    a fire-and-forget background LLM call
                                    after every tool batch to caption a
                                    mobile-app UI row ("Fixed NPE in
                                    UserService") that does not exist in this
                                    headless simulation. Disabled: it is pure
                                    unbudgeted spend on the SAME kimi-k2.5
                                    adapter for a feature this deployment has
                                    no UI to show, and its own docstring
                                    ("fire-and-forget... concurrent with the
                                    next agent turn") makes it a source of
                                    nondeterministic ordering with no
                                    corresponding benefit here.

    Setting this dict into os.environ is necessary but NOT sufficient for
    FEATURE_TOOL_USE_SUMMARY: src/engine_core/tool_use_summary.py reads it
    into a module-level ``ENABLED`` constant ONCE, at import time (unlike
    FORK_SUBAGENT's is_fork_subagent_enabled(), which re-reads os.environ on
    every call - see fork_subagent.py). If anything in this process ever
    imported tool_use_summary.py before build_settings() runs - which
    Coordinator/ExecutionThread's own import chain guarantees will already
    have happened by the time boot_sim_engine gets here, since pinned_boot.py
    imports Coordinator at module scope - setting the env var here is too
    late to change that constant. See disable_tool_use_summary() below for
    the actual fix; this dict is kept in sync anyway so a fresh process that
    genuinely imports tool_use_summary.py for the first time AFTER this call
    (sim_main.py, run as its own process) still gets the correct value from
    the environment too, belt and suspenders.
    """
    return {
        # ENG-02. The engine's compaction preflight is
        #     post + sys + tools + RESERVE_FLOOR >= context_window
        # and RESERVE_FLOOR is an absolute 25,000. At any window below
        # ~45K that is true with zero messages, so every model call in
        # every run took the EMERGENCY path: tool results gutted, the
        # day-0 06:20 perception pinned into the prefix forever,
        # assistant-genre "[Post-compact tool reminder]" text in a
        # villager's prompt, and a cold prompt cache every single call.
        #
        # History is bounded in the seam now (sim/bounded_history.py), so
        # the engine must stop rewriting the prefix underneath it.
        "DISABLE_AUTO_COMPACT": "1",
        "FORK_SUBAGENT": "false",
        "FEATURE_TOOL_USE_SUMMARY": "false",
    }


def disable_tool_use_summary() -> None:
    """Force src.engine_core.tool_use_summary.ENABLED False on the already-
    imported module object, not just the environment.

    Why this exists (see raw_process_env's docstring): ENABLED is computed
    once, at import time, from os.environ. By the time build_settings() runs
    inside boot_sim_engine, src/engine_core/coordinator.py (imported at
    pinned_boot.py's module scope) has already pulled in thread.py, which
    imports tool_use_summary.py lazily inside its own
    generate_tool_use_summary() wrapper the first time a tool-bearing turn
    runs - but Python caches that import in sys.modules the moment ANY
    caller in the process reaches it first (a test file's own top-level
    imports, another engine test that ran earlier in the same pytest
    session, or this very function). Relying on env-var timing to win that
    race is fragile; setting the already-imported module's attribute
    directly is not - ``if not ENABLED`` at the call site is a fresh
    attribute lookup every time, so patching it here takes effect
    immediately regardless of import order. A quiet import failure (the
    module truly not loaded yet, e.g. a future engine refactor that defers
    it further) is swallowed on purpose: there is nothing to patch yet, and
    the env var set above will do the job correctly for that case.
    """
    try:
        from src.engine_core import tool_use_summary

        tool_use_summary.ENABLED = False
    except ImportError:
        pass


def build_settings(profile: SimProfile) -> Settings:
    """Build a real Settings for this agent: env-parsed fields + deny_rules.

    Creates SEPTEMBER_HOME on disk first - src/infra/september_home.py's
    resolve_september_home() treats a set-but-missing root as FATAL by
    design (a bad mount must not silently write a user's memory nowhere),
    and Settings.__post_init__ calls it during construction.

    Also applies raw_process_env() to the real process environment (see its
    docstring for why: those two flags bypass Settings entirely) and, for
    FEATURE_TOOL_USE_SUMMARY specifically, patches the already-imported
    module too (see disable_tool_use_summary()). Each engine instance is its
    own OS process (DESIGN.md section 4: one microVM per agent), so mutating
    this process's os.environ at boot is the sim equivalent of a deployment
    setting them before launch - not a global side effect shared across
    agents.
    """
    import os

    home = Path(profile.september_home)
    home.mkdir(parents=True, exist_ok=True)

    os.environ.update(raw_process_env(profile))
    disable_tool_use_summary()

    settings, errors = Settings.build_from_env(sim_env(profile))
    if errors:
        from src.config import ConfigError

        raise ConfigError(errors)

    settings.deny_rules = [*fork_deny_rules(), *profile.extra_deny_rules]
    return settings


def validate_sim_settings(settings: Settings) -> list[str]:
    """Full validation for a sim Settings: base semantics + sim-only rules.

    Returns every error at once (CONVENTIONS.md: "aggregate errors instead
    of failing on the first one"), mirroring Settings.validate() and
    src/herald/model_validation.py's own style. Stands in for
    model_validation.validate_resolved_models(), which pinned_boot.py
    deliberately never calls (it needs a live catalog sync and would check
    the unreachable REASONING role - see pinned_boot.py).
    """
    import os

    from src.herald.defaults import ModelRole, resolve_model

    errors = list(settings.validate())

    checked_roles = (
        ModelRole.PRIMARY,
        ModelRole.PLANNER,
        ModelRole.LIGHT,
        ModelRole.DISPATCH,
        ModelRole.DISPATCH_ALTERNATIVE,
        ModelRole.LC,
    )
    for role in checked_roles:
        model_id = resolve_model(role, settings)
        if model_id not in ALLOWED_MODEL_IDS:
            errors.append(
                f"{role.value} resolves to {model_id!r}, which is not the "
                f"Kasukabe sim model ({MODEL_ID!r})"
            )

    if settings.fallback_model and settings.fallback_model not in ALLOWED_MODEL_IDS:
        errors.append(
            f"fallback resolves to {settings.fallback_model!r}, which is not the "
            f"Kasukabe sim model ({MODEL_ID!r})"
        )

    if os.environ.get("FORK_SUBAGENT", "false").strip().lower() != "false":
        errors.append(
            "FORK_SUBAGENT must be \"false\" for a Kasukabe sim engine "
            "(DESIGN.md section 4); it is read from the environment, not Settings"
        )

    denied_tools = {rule.get("tool") for rule in (settings.deny_rules or [])}
    missing = [t for t in FORK_DENIED_TOOLS if t not in denied_tools]
    if missing:
        errors.append(
            f"settings.deny_rules is missing a deny rule for {missing} - "
            "sub-agent forking would be reachable; use "
            "src.sim.config.build_settings() instead of constructing Settings directly"
        )

    return errors
