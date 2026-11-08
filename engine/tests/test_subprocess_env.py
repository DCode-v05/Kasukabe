"""Tests for src.utility_directory.subprocess_env.

Covers tier semantics, allowlist behavior, INPUT_ duplicate sweep,
proxy-env registration, overrides, os.environ immutability, and
the settings fallback for mode resolution.
"""

from __future__ import annotations

import os

import pytest

from src.utility_directory import subprocess_env as se_mod
from src.utility_directory.subprocess_env import (
    SubprocessEnvResult,
    ALLOWLIST,
    DEFAULT_TIER_VARS,
    STRICT_SCRUB,
    STRICT_TIER_VARS,
    register_proxy_env_fn,
    register_spawn_observer,
    set_session_mode,
    subprocess_env,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset module-level proxy fn, session mode, and observer between tests."""
    yield
    register_proxy_env_fn(None)
    register_spawn_observer(None)
    se_mod.session_mode = None


@pytest.fixture
def secret_env(monkeypatch):
    """Populate os.environ with one secret from every category + the allowlist."""
    seeded = {
        # engine_core
        "ANTHROPIC_API_KEY": "anthropic-sk-sentinel",
        "DATABASE_URL": "postgres://sentinel",
        "FERNET_KEY": "fernet-sentinel",
        # anthropic_secondary
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-sentinel",
        # otlp_telemetry (strict only)
        "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer otlp-sentinel",
        # cloud_creds (strict only)
        "AWS_SECRET_ACCESS_KEY": "aws-sentinel",
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/gcp.json",
        # gha_oidc (strict only)
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-sentinel",
        # gha_runtime (strict only)
        "ACTIONS_RUNTIME_TOKEN": "runtime-sentinel",
        # action_duplicates (strict only)
        "ALL_INPUTS": '{"anthropic_api_key":"leaked"}',
        # INPUT_ duplicates (GHA auto-generated)
        "INPUT_ANTHROPIC_API_KEY": "input-sentinel",
        "INPUT_MY_CUSTOM_FLAG": "true",  # not a scrubbed var base → should survive
        # Allowlist
        "GITHUB_TOKEN": "gh-token-sentinel",
        "GH_TOKEN": "gh-alias-sentinel",
        # Innocuous — should survive every mode
        "PATH": "/usr/bin",
        "HOME": "/home/test",
    }
    for k, v in seeded.items():
        monkeypatch.setenv(k, v)
    return seeded


# ── Tier semantics ───────────────────────────────────────────────


class TestOffMode:
    def test_off_returns_fresh_copy_not_reference(self, secret_env):
        result = subprocess_env(mode="off")
        assert isinstance(result, SubprocessEnvResult)
        # Fresh dict — mutating the returned env must not touch os.environ.
        result.env["NEW_KEY"] = "1"
        assert "NEW_KEY" not in os.environ

    def test_off_keeps_every_secret(self, secret_env):
        result = subprocess_env(mode="off")
        for key, expected in secret_env.items():
            assert result.env.get(key) == expected, f"{key} missing in off mode"

    def test_off_scrubbed_count_is_zero(self, secret_env):
        result = subprocess_env(mode="off")
        assert result.scrubbed_count == 0
        assert result.mode == "off"


class TestDefaultMode:
    def test_default_strips_engine_core(self, secret_env):
        result = subprocess_env(mode="default")
        for var in DEFAULT_TIER_VARS["engine_core"]:
            assert var not in result.env, f"{var} should be stripped in default"

    def test_default_strips_anthropic_auth(self, secret_env):
        result = subprocess_env(mode="default")
        for var in DEFAULT_TIER_VARS["anthropic_auth"]:
            assert var not in result.env

    def test_default_preserves_strict_only_vars(self, secret_env):
        result = subprocess_env(mode="default")
        # Strict-only vars should still be present in default mode
        assert result.env.get("AWS_SECRET_ACCESS_KEY") == "aws-sentinel"
        assert result.env.get("OTEL_EXPORTER_OTLP_HEADERS") == "Authorization=Bearer otlp-sentinel"
        assert result.env.get("ACTIONS_RUNTIME_TOKEN") == "runtime-sentinel"
        assert result.env.get("ALL_INPUTS") == '{"anthropic_api_key":"leaked"}'

    def test_default_scrub_count_matches_stripped_vars(self, secret_env):
        result = subprocess_env(mode="default")
        # engine_core seeded: DATABASE_URL, FERNET_KEY (2)
        # anthropic_auth seeded: ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN (2)
        # INPUT_ duplicates seeded: INPUT_ANTHROPIC_API_KEY (1)
        assert result.scrubbed_count == 5


class TestStrictMode:
    def test_strict_strips_all_categories(self, secret_env):
        result = subprocess_env(mode="strict")
        all_strict_cats = {**DEFAULT_TIER_VARS, **STRICT_TIER_VARS}
        for category, cat_vars in all_strict_cats.items():
            for var in cat_vars:
                assert var not in result.env, (
                    f"{var} in category {category} should be stripped in strict mode"
                )

    def test_strict_strips_seeded_secrets(self, secret_env):
        """Every seeded secret from secret_env fixture should be gone in strict mode."""
        result = subprocess_env(mode="strict")
        seeded_secrets = [
            "ANTHROPIC_API_KEY",
            "DATABASE_URL",
            "FERNET_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "AWS_SECRET_ACCESS_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
            "ACTIONS_RUNTIME_TOKEN",
            "ALL_INPUTS",
            "INPUT_ANTHROPIC_API_KEY",
        ]
        for var in seeded_secrets:
            assert var not in result.env, f"{var} leaked in strict mode"

    def test_strict_preserves_innocuous_vars(self, secret_env):
        result = subprocess_env(mode="strict")
        assert result.env.get("PATH") == "/usr/bin"
        assert result.env.get("HOME") == "/home/test"


class TestAllowlist:
    def test_github_token_survives_all_modes(self, secret_env):
        for mode in ("off", "default", "strict"):
            result = subprocess_env(mode=mode)  # type: ignore[arg-type]
            assert result.env.get("GITHUB_TOKEN") == "gh-token-sentinel", (
                f"GITHUB_TOKEN stripped in {mode} mode"
            )
            assert result.env.get("GH_TOKEN") == "gh-alias-sentinel"

    def test_allowlist_keys_have_rationale(self):
        """Every allowlist entry must document why it's kept."""
        for key, rationale in ALLOWLIST.items():
            assert rationale, f"{key} has no rationale comment"
            assert len(rationale) > 20, f"{key} rationale is too short to be meaningful"


class TestInputDuplicateSweep:
    def test_input_duplicate_of_scrubbed_var_is_stripped(self, secret_env):
        result = subprocess_env(mode="default")
        assert "INPUT_ANTHROPIC_API_KEY" not in result.env

    def test_input_prefix_with_non_scrubbed_base_is_preserved(self, secret_env):
        result = subprocess_env(mode="strict")
        # MY_CUSTOM_FLAG is not in any scrub set → its INPUT_ shadow must survive.
        assert result.env.get("INPUT_MY_CUSTOM_FLAG") == "true"


class TestOverrides:
    def test_overrides_applied_after_scrub(self, secret_env):
        result = subprocess_env(mode="default", overrides={"ANTHROPIC_API_KEY": "override-value"})
        # Override takes precedence because it merges AFTER the scrub.
        assert result.env.get("ANTHROPIC_API_KEY") == "override-value"

    def test_overrides_can_add_new_vars(self, secret_env):
        result = subprocess_env(mode="strict", overrides={"MCP_SERVER_TOKEN": "x"})
        assert result.env.get("MCP_SERVER_TOKEN") == "x"

    def test_overrides_preserve_allowlist(self, secret_env):
        result = subprocess_env(mode="strict", overrides={"GITHUB_TOKEN": "override-gh"})
        assert result.env.get("GITHUB_TOKEN") == "override-gh"


class TestProxyEnvFn:
    def test_proxy_env_merged_on_each_call(self, secret_env):
        call_count = {"n": 0}

        def proxy():
            call_count["n"] += 1
            return {"HTTPS_PROXY": "http://proxy.corp:3128"}

        register_proxy_env_fn(proxy)
        r1 = subprocess_env(mode="default")
        r2 = subprocess_env(mode="default")

        assert call_count["n"] == 2, "proxy fn should be called on every invocation"
        assert r1.env["HTTPS_PROXY"] == "http://proxy.corp:3128"
        assert r2.env["HTTPS_PROXY"] == "http://proxy.corp:3128"
        assert r1.has_proxy_env is True

    def test_proxy_fn_raising_does_not_break_subprocess_env(self, secret_env):
        def bad_proxy():
            raise RuntimeError("network is down")

        register_proxy_env_fn(bad_proxy)
        result = subprocess_env(mode="default")
        # Scrub still happened; proxy not applied; no exception.
        assert "ANTHROPIC_API_KEY" not in result.env
        assert result.has_proxy_env is False

    def test_register_is_one_shot(self, secret_env):
        def first():
            return {"PROXY_ID": "first"}

        def second():
            return {"PROXY_ID": "second"}

        register_proxy_env_fn(first)
        register_proxy_env_fn(second)
        result = subprocess_env(mode="off")
        assert result.env.get("PROXY_ID") == "second"

    def test_register_none_clears(self, secret_env):
        register_proxy_env_fn(lambda: {"PROXY_ID": "x"})
        register_proxy_env_fn(None)
        result = subprocess_env(mode="off")
        assert "PROXY_ID" not in result.env
        assert result.has_proxy_env is False

    def test_has_proxy_env_false_when_fn_returns_empty(self, secret_env):
        register_proxy_env_fn(lambda: {})
        result = subprocess_env(mode="default")
        assert result.has_proxy_env is False


class TestOsEnvironImmutability:
    def test_os_environ_unchanged_after_call(self, secret_env):
        snapshot = dict(os.environ)
        subprocess_env(mode="strict")
        # Same keys, same values, still in os.environ.
        assert dict(os.environ) == snapshot
        # Secrets still accessible on the parent.
        assert os.environ.get("ANTHROPIC_API_KEY") == "anthropic-sk-sentinel"
        assert os.environ.get("AWS_SECRET_ACCESS_KEY") == "aws-sentinel"

    def test_mutating_returned_env_does_not_affect_os_environ(self, secret_env):
        result = subprocess_env(mode="off")
        result.env.clear()
        result.env["ONLY_KEY"] = "x"
        assert os.environ.get("ANTHROPIC_API_KEY") == "anthropic-sk-sentinel"
        assert "ONLY_KEY" not in os.environ


class TestInvalidMode:
    def test_invalid_mode_string_raises(self):
        with pytest.raises(ValueError, match="invalid mode"):
            subprocess_env(mode="super_strict")  # type: ignore[arg-type]

    def test_invalid_mode_message_lists_valid_values(self):
        with pytest.raises(ValueError) as exc_info:
            subprocess_env(mode="bogus")  # type: ignore[arg-type]
        msg = str(exc_info.value)
        assert "off" in msg and "default" in msg and "strict" in msg


class TestSessionMode:
    def test_session_mode_used_when_mode_is_none(self, secret_env):
        set_session_mode("strict")
        result = subprocess_env(mode=None)
        assert result.mode == "strict"
        assert "AWS_SECRET_ACCESS_KEY" not in result.env

    def test_session_mode_overridden_by_explicit_mode(self, secret_env):
        set_session_mode("strict")
        result = subprocess_env(mode="off")
        assert result.mode == "off"
        assert result.env.get("AWS_SECRET_ACCESS_KEY") == "aws-sentinel"

    def test_session_mode_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid mode"):
            set_session_mode("super_strict")  # type: ignore[arg-type]

    def test_none_mode_reads_env_var_when_session_unset(self, monkeypatch, secret_env):
        monkeypatch.setenv("SUBPROCESS_SCRUB_MODE", "strict")
        result = subprocess_env(mode=None)
        assert result.mode == "strict"
        assert "AWS_SECRET_ACCESS_KEY" not in result.env

    def test_none_mode_defaults_to_default_when_nothing_set(self, monkeypatch, secret_env):
        monkeypatch.delenv("SUBPROCESS_SCRUB_MODE", raising=False)
        result = subprocess_env(mode=None)
        assert result.mode == "default"

    def test_none_mode_invalid_env_falls_back_to_default(self, monkeypatch, secret_env):
        monkeypatch.setenv("SUBPROCESS_SCRUB_MODE", "garbage")
        result = subprocess_env(mode=None)
        assert result.mode == "default"


class TestSpawnObserver:
    def test_observer_called_with_result_and_label(self, secret_env):
        captured: list[tuple[SubprocessEnvResult, str]] = []
        register_spawn_observer(lambda r, s: captured.append((r, s)))

        result = subprocess_env(mode="strict", spawn_site="sandbox_exec")
        assert len(captured) == 1
        observed_result, observed_site = captured[0]
        assert observed_site == "sandbox_exec"
        assert observed_result is result
        assert observed_result.mode == "strict"
        assert observed_result.scrubbed_count > 0

    def test_observer_skipped_when_spawn_site_empty(self, secret_env):
        captured: list[tuple] = []
        register_spawn_observer(lambda r, s: captured.append((r, s)))
        subprocess_env(mode="strict")  # no spawn_site
        assert captured == []

    def test_observer_raising_does_not_break_subprocess_env(self, secret_env):
        def bad_observer(r, s):
            raise RuntimeError("audit pipeline down")

        register_spawn_observer(bad_observer)
        result = subprocess_env(mode="default", spawn_site="sandbox_exec")
        # Spawn still got its scrubbed env.
        assert "ANTHROPIC_API_KEY" not in result.env

    def test_observer_register_none_clears(self, secret_env):
        captured: list = []
        register_spawn_observer(lambda r, s: captured.append((r, s)))
        register_spawn_observer(None)
        subprocess_env(mode="default", spawn_site="sandbox_exec")
        assert captured == []


class TestCategoryCompleteness:
    """Iterate every category and every var to catch drift from the scrub list."""

    def test_every_default_tier_var_stripped_in_default(self, monkeypatch):
        for cat_vars in DEFAULT_TIER_VARS.values():
            for var in cat_vars:
                monkeypatch.setenv(var, f"sentinel-{var}")
        result = subprocess_env(mode="default")
        for cat_vars in DEFAULT_TIER_VARS.values():
            for var in cat_vars:
                assert var not in result.env, f"{var} not stripped in default"

    def test_every_strict_tier_var_stripped_in_strict(self, monkeypatch):
        for cat_vars in STRICT_TIER_VARS.values():
            for var in cat_vars:
                monkeypatch.setenv(var, f"sentinel-{var}")
        result = subprocess_env(mode="strict")
        for cat_vars in STRICT_TIER_VARS.values():
            for var in cat_vars:
                assert var not in result.env, f"{var} not stripped in strict"

    def test_scrub_set_union_size_matches_declared_vars(self):
        declared = sum(len(v) for v in DEFAULT_TIER_VARS.values()) + sum(
            len(v) for v in STRICT_TIER_VARS.values()
        )
        # Union can only be smaller than the sum if a var is declared in two
        # categories; we want the lists disjoint so audits stay unambiguous.
        assert len(STRICT_SCRUB) == declared, (
            "scrub categories have overlapping vars — each var should appear in exactly one category"
        )
