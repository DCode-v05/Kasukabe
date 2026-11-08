"""SimProfile -> env vars -> Settings, and the fork-block deny rules."""

from __future__ import annotations

from src.sim.config import (
    DEFAULT_LEARN_AWAKE_INTERVAL_TICKS,
    FORK_DENIED_TOOLS,
    SimProfile,
    build_settings,
    disable_tool_use_summary,
    ablation_arm,
    fork_deny_rules,
    learn_awake_interval_ticks,
    pinned_thread_id,
    raw_process_env,
    resident_learns,
    sim_env,
    validate_sim_settings,
)
from src.sim.model_profile import MODEL_ID


def make_profile(tmp_path, agent_id: str = "agent-007") -> SimProfile:
    return SimProfile(
        agent_id=agent_id,
        broker_base_url="http://broker.test:8200",
        home_root=str(tmp_path / "agents"),
    )


def test_pinned_thread_id_is_deterministic():
    assert pinned_thread_id("agent-1") == pinned_thread_id("agent-1")
    assert pinned_thread_id("agent-1") != pinned_thread_id("agent-2")


def test_sim_env_points_at_the_broker_and_pins_every_role_to_the_one_model(tmp_path):
    profile = make_profile(tmp_path)
    env = sim_env(profile)

    assert env["CDT_URL"] == "http://broker.test:8200"
    assert env["LLM_MODEL"] == MODEL_ID
    assert env["PLANNER_MODEL"] == MODEL_ID
    assert env["LIGHT_MODEL"] == MODEL_ID
    assert env["DISPATCH_MODEL"] == MODEL_ID
    assert env["DISPATCH_MODEL_ALTERNATIVE"] == MODEL_ID
    assert env["LC_MODEL"] == MODEL_ID
    assert env["FALLBACK_MODEL"] == ""
    # FORK_SUBAGENT and FEATURE_TOOL_USE_SUMMARY are NOT here: they bypass
    # Settings entirely (raw os.environ reads), so sim_env()'s dict - which
    # only ever reaches Settings.build_from_env - cannot carry them. See
    # raw_process_env() and test_raw_process_env_disables_fork_and_the_ui_only_tool_summary.
    assert "FORK_SUBAGENT" not in env


def test_every_model_role_in_sim_env_resolves_to_the_same_single_id(tmp_path):
    """DESIGN.md section 4: no model-choice mechanic - every role Settings
    can resolve for this agent is literally the same string, not merely
    "an allowed" string each."""
    profile = make_profile(tmp_path)
    env = sim_env(profile)

    role_values = {
        env[key]
        for key in (
            "LLM_MODEL",
            "PLANNER_MODEL",
            "LIGHT_MODEL",
            "DISPATCH_MODEL",
            "DISPATCH_MODEL_ALTERNATIVE",
            "LC_MODEL",
        )
    }
    assert role_values == {MODEL_ID}


def test_raw_process_env_disables_fork_and_the_ui_only_tool_summary(tmp_path):
    profile = make_profile(tmp_path)
    raw = raw_process_env(profile)

    assert raw == {
        "FORK_SUBAGENT": "false",
        "FEATURE_TOOL_USE_SUMMARY": "false",
        # ENG-02, 2026-09-04: history is bounded in the seam, so the engine
        # must stop rewriting the prefix. Without this every call took the
        # emergency-compaction path - see sim/bounded_history.py.
        "DISABLE_AUTO_COMPACT": "1",
    }


def test_disable_tool_use_summary_patches_the_already_imported_module(monkeypatch):
    """The env var alone is too late (tool_use_summary.ENABLED is cached at
    import time - see raw_process_env's docstring); this must patch the
    live module attribute so it takes effect regardless of when the module
    was first imported in this process."""
    from src.engine_core import tool_use_summary

    monkeypatch.setattr(tool_use_summary, "ENABLED", True)
    assert tool_use_summary.ENABLED is True

    disable_tool_use_summary()

    assert tool_use_summary.ENABLED is False


def test_build_settings_disables_tool_use_summary_regardless_of_import_order(tmp_path, monkeypatch):
    from src.engine_core import tool_use_summary

    monkeypatch.setattr(tool_use_summary, "ENABLED", True)
    profile = make_profile(tmp_path)

    build_settings(profile)

    assert tool_use_summary.ENABLED is False


def test_fork_deny_rules_cover_every_forking_tool():
    rules = fork_deny_rules()
    named = {r["tool"] for r in rules}
    assert named == set(FORK_DENIED_TOOLS)
    assert "spawn_sub_agent" in named
    for rule in rules:
        assert rule["pattern"] == ""  # matches unconditionally, not a subset of inputs


def test_build_settings_wires_deny_rules_and_creates_september_home(tmp_path):
    profile = make_profile(tmp_path)
    settings = build_settings(profile)

    assert settings.cdt_url == "http://broker.test:8200"
    assert settings.llm_model == MODEL_ID
    assert settings.lc_model == MODEL_ID
    denied = {r["tool"] for r in settings.deny_rules}
    assert denied == set(FORK_DENIED_TOOLS)
    assert (tmp_path / "agents" / "agent-007").is_dir()


def test_validate_sim_settings_passes_for_a_clean_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = make_profile(tmp_path)
    settings = build_settings(profile)
    assert validate_sim_settings(settings) == []


def test_validate_sim_settings_flags_a_model_outside_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = make_profile(tmp_path)
    settings = build_settings(profile)
    settings.llm_model = "claude-opus-4-8"

    errors = validate_sim_settings(settings)
    assert any("claude-opus-4-8" in e for e in errors)


def test_validate_sim_settings_flags_the_bare_kimi_id_without_the_provider_prefix(
    tmp_path, monkeypatch
):
    """"kimi-k2.5" (no "moonshotai." prefix) is a different string than
    MODEL_ID and must fail the same as any other disallowed id - the broker's
    own allowlist check (app/broker/allowlist.py) is an exact string match,
    so a near-miss here would 403 at the wire instead of failing fast at
    boot."""
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = make_profile(tmp_path)
    settings = build_settings(profile)
    settings.lc_model = "kimi-k2.5"

    errors = validate_sim_settings(settings)
    assert any("kimi-k2.5" in e for e in errors)


def test_build_settings_forces_fork_subagent_false_even_if_ambient_env_disagrees(tmp_path, monkeypatch):
    """build_settings() is self-healing: it does not just hope the ambient
    process environment already says FORK_SUBAGENT=false, it sets it -
    raw_process_env() is applied to os.environ unconditionally."""
    monkeypatch.setenv("FORK_SUBAGENT", "true")
    profile = make_profile(tmp_path)

    build_settings(profile)

    import os

    assert os.environ["FORK_SUBAGENT"] == "false"


def test_validate_sim_settings_flags_fork_subagent_enabled(tmp_path, monkeypatch):
    """validate_sim_settings is defense-in-depth against drift AFTER boot
    (a hook, a later env mutation) - build_settings() itself always leaves
    FORK_SUBAGENT correctly set, so drift has to be simulated after the fact
    to exercise this check at all."""
    profile = make_profile(tmp_path)
    settings = build_settings(profile)
    monkeypatch.setenv("FORK_SUBAGENT", "true")

    errors = validate_sim_settings(settings)
    assert any("FORK_SUBAGENT" in e for e in errors)


def test_validate_sim_settings_flags_missing_deny_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = make_profile(tmp_path)
    settings = build_settings(profile)
    settings.deny_rules = []  # simulate a caller that skipped build_settings' wiring

    errors = validate_sim_settings(settings)
    assert any("deny_rules" in e for e in errors)


def test_validate_sim_settings_aggregates_every_problem_at_once(tmp_path, monkeypatch):
    """CONVENTIONS.md: aggregate errors instead of failing on the first one."""
    profile = make_profile(tmp_path)
    settings = build_settings(profile)
    monkeypatch.setenv("FORK_SUBAGENT", "true")
    settings.llm_model = "claude-opus-4-8"
    settings.deny_rules = []

    errors = validate_sim_settings(settings)
    assert len(errors) >= 3


# ── the waking interval, and the call site that never passed it ────────


def test_the_waking_interval_defaults_to_something_that_actually_runs():
    """The whole defect in one assertion.

    `SimLearningGate.awake_interval_ticks` has existed since the cadence
    work, defaulting to 0 - sleep-onset only - and pinned_boot constructed
    the gate without it. Every run begins at dawn and the spend cap ends it
    before dusk, so the wake->asleep edge never came, `learning_ran` was
    zero in every session log, and three run reports read that as "the
    residents did not learn". A default of 0 here would restore that
    silently.
    """
    assert learn_awake_interval_ticks({}) == DEFAULT_LEARN_AWAKE_INTERVAL_TICKS
    assert DEFAULT_LEARN_AWAKE_INTERVAL_TICKS > 0


def test_the_interval_can_be_turned_off_for_a_control_arm():
    assert learn_awake_interval_ticks({"KASUKABE_LEARN_AWAKE_TICKS": "0"}) == 0


def test_a_typo_falls_back_to_the_default_rather_than_off():
    """Off is the failure this constant exists to end, so an unparseable
    value must never mean off."""
    assert (
        learn_awake_interval_ticks({"KASUKABE_LEARN_AWAKE_TICKS": "six"})
        == DEFAULT_LEARN_AWAKE_INTERVAL_TICKS
    )
    assert (
        learn_awake_interval_ticks({"KASUKABE_LEARN_AWAKE_TICKS": ""})
        == DEFAULT_LEARN_AWAKE_INTERVAL_TICKS
    )


def test_an_explicit_interval_is_honoured():
    assert learn_awake_interval_ticks({"KASUKABE_LEARN_AWAKE_TICKS": "6"}) == 6


def _gate_construction(source: str) -> str:
    """The argument list of the SimLearningGate(...) call, paren-balanced.

    Splitting on the first ")" does not work: the arguments themselves
    contain calls with their own parentheses, and the first draft of this
    helper cut the block in half at `learn_awake_interval_ticks(`.
    """
    start = source.index("SimLearningGate(") + len("SimLearningGate(")
    depth = 1
    for offset, char in enumerate(source[start:]):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[start:start + offset]
    raise AssertionError("SimLearningGate( is never closed")


def test_the_boot_path_passes_the_interval_to_the_gate():
    """A field is not a fix until a call site passes it.

    Reads the source of the one line that was wrong rather than booting a
    whole engine, because what broke was not the gate's logic (it was
    correct and tested) but the single construction site that used the
    default. A test that exercises the gate directly would have passed
    against the broken tree, and did.
    """
    import inspect

    from src.sim import pinned_boot

    source = inspect.getsource(pinned_boot.boot_sim_engine)
    assert "SimLearningGate(" in source
    assert "awake_interval_ticks" in _gate_construction(source)


# ── the ablation arm reaching the engine ──────────────────────────────


def test_an_engine_told_nothing_is_in_the_full_arm():
    """Allow-by-default. A resident whose arm nobody recorded is not a
    control for anything, and treating it as one would quietly put
    untreated residents in the treated group."""
    assert ablation_arm({}) == "full"
    assert resident_learns({}) is True


def test_the_no_learning_arm_is_recognised():
    assert resident_learns({"KASUKABE_ABLATION_ARM": "no_learning"}) is False
    assert resident_learns({"KASUKABE_ABLATION_ARM": "  NO_LEARNING  "}) is False


def test_the_other_arms_still_learn():
    """`no_reflection` and `deepseek_locked` ablate something else. Only
    `no_learning` is about the learning centre."""
    for arm in ("full", "no_reflection", "deepseek_locked"):
        assert resident_learns({"KASUKABE_ABLATION_ARM": arm}) is True, arm


def test_a_typo_leaves_the_resident_learning():
    """Silently switching learning off because of a misspelled env var
    would put an untreated resident in the treated group with nothing in
    the trace to show it."""
    assert resident_learns({"KASUKABE_ABLATION_ARM": "no-learning"}) is True
    assert resident_learns({"KASUKABE_ABLATION_ARM": ""}) is True


def test_the_boot_path_passes_the_arm_to_the_gate():
    """The same check as the waking interval, for the same reason: the
    gate's logic was never what was broken."""
    import inspect

    from src.sim import pinned_boot

    assert "learns" in _gate_construction(
        inspect.getsource(pinned_boot.boot_sim_engine)
    )
