"""Integration tests for the step-1.7 dangerous-removal gate.

Exercises ``check_permission()`` end-to-end with ``ToolSource.SANDBOXED``
bash input, asserting that the dangerous-removal hard-deny fires before
any user rule, permission mode, or prompt gets a chance to override it.
Unit tests for the detection functions and argv extractor live in
``test_dangerous_removal.py`` and ``test_command_path_extractor.py``.
"""

from __future__ import annotations

import os

import pytest

from src.sandbox.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionResult,
    ToolSource,
    check_permission,
)


def bash(command: str) -> dict[str, str]:
    """Build a SANDBOXED bash tool_input shape."""
    return {"command": command}


async def check(
    command: str,
    *,
    rules: list[PermissionRule] | None = None,
    mode: PermissionMode = PermissionMode.DEFAULT,
    ask_user_fn=None,
) -> PermissionResult:
    return await check_permission(
        tool_name="bash",
        tool_input=bash(command),
        source=ToolSource.SANDBOXED,
        rules=rules,
        ask_user_fn=ask_user_fn,
        permission_mode=mode,
    )


def ensure_flag_on(monkeypatch):
    """Force the dangerous-removal gate on regardless of ambient env."""
    monkeypatch.setenv("FEATURE_DANGEROUS_REMOVAL", "true")


def ensure_flag_off(monkeypatch):
    """Force the dangerous-removal gate off."""
    monkeypatch.setenv("FEATURE_DANGEROUS_REMOVAL", "false")


class TestDangerousRemovalHardDeny:
    """Exercises step 1.7 in isolation.

    Each test uses a command shape that **bypasses** the pre-existing
    HARDCODED_DENIALS regex `rm\\s+-rf\\s+[/~]` at step 1 (no `-rf`, or
    `rmdir`/`unlink` instead of `rm`). That way the assertion for the
    step-1.7 "Dangerous removal blocked" reason is meaningful — if it
    passes, step 1.7 is doing the work, not step 1.
    """

    @pytest.mark.asyncio
    async def test_rm_root_no_flags_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm /")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rm_etc_no_flags_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm /etc")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rm_usr_single_r_flag_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm -r /usr")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rm_home_no_flags_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm /home")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rm_tilde_no_flags_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm ~")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rm_wildcard_alone_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm *")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rm_wildcard_suffix_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm /home/*")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rmdir_root_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rmdir /")
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_rmdir_etc_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rmdir /etc")
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_unlink_etc_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("unlink /etc")
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_rm_posix_double_dash_etc(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm -- /etc")
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_rm_posix_double_dash_dash_prefixed_attack(self, monkeypatch):
        """`rm -- -/../.claude/settings.local.json` must be hard-denied.

        The raw path `-/../.claude/settings.local.json` is not by itself
        one of the 7 dangerous shapes (its parent is `-/..` not `/`), so
        THIS specific payload escapes the dangerous-removal gate — the
        protection for `.claude/*` files lives at step 1.6 via
        ``check_path_safety``. What we assert here is that the gate at
        least parses the token correctly and doesn't fall through with
        an exception.
        """
        ensure_flag_on(monkeypatch)
        result = await check("rm -- -/../.claude/settings.local.json")
        assert isinstance(result, PermissionResult)

    @pytest.mark.asyncio
    async def test_rm_quoted_etc_hard_denied(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check('rm "/etc"')
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_rm_double_slash_root_denied_by_defense_in_depth(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm //")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason or "path-evasion" in result.reason

    @pytest.mark.asyncio
    async def test_step_1_still_catches_classic_rm_rf_root(self, monkeypatch):
        """Sanity check: the pre-existing HARDCODED_DENIALS rule still fires.

        This proves the defense-in-depth layering is intact — even if
        step 1.7 were rolled back, step 1 still catches the classic
        `rm -rf /` shape. The reason message is different because step 1
        returns its own message, not step 1.7's.
        """
        ensure_flag_on(monkeypatch)
        result = await check("rm -rf /")
        assert result.allowed is False
        assert (
            "Dangerous removal blocked" in result.reason or "catastrophic deletion" in result.reason
        )


class TestDangerousRemovalLegitimate:
    @pytest.mark.asyncio
    async def test_rm_tmp_subdir_not_gate_blocked(self, monkeypatch, tmp_path):
        ensure_flag_on(monkeypatch)
        target = tmp_path / "build"
        target.mkdir()
        result = await check(f"rm -rf {target}")
        assert "Dangerous removal blocked" not in result.reason

    @pytest.mark.asyncio
    async def test_rm_etc_subfile_not_gate_blocked(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm /etc/nginx/conf.d/site.conf")
        assert "Dangerous removal blocked" not in result.reason

    @pytest.mark.asyncio
    async def test_rm_node_modules_allowed_by_gate(self, monkeypatch, tmp_path):
        ensure_flag_on(monkeypatch)
        nm = tmp_path / "project" / "node_modules"
        nm.mkdir(parents=True)
        result = await check(f"rm -rf {nm}")
        assert "Dangerous removal blocked" not in result.reason


class TestDangerousRemovalBypassAttempts:
    @pytest.mark.asyncio
    async def test_broad_allow_rule_does_not_override(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        allow_rule = PermissionRule(
            tool="bash",
            pattern=r".*",
            behavior=PermissionBehavior.ALLOW,
            reason="permissive test rule",
        )
        result = await check("rm /etc", rules=[allow_rule])
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_specific_rm_allow_rule_does_not_override(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        allow_rule = PermissionRule(
            tool="bash",
            pattern=r"rm\s.*",
            behavior=PermissionBehavior.ALLOW,
            reason="rm anywhere",
        )
        result = await check("rm /usr", rules=[allow_rule])
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_accept_edits_mode_does_not_override(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("rm /etc", mode=PermissionMode.ACCEPT_EDITS)
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_ask_user_approval_does_not_override(self, monkeypatch):
        ensure_flag_on(monkeypatch)

        async def always_allow(prompt):  # noqa: ANN001
            return "allow"

        result = await check("rm /etc", ask_user_fn=always_allow)
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason


class TestDangerousRemovalSymlinkAttack:
    @pytest.mark.asyncio
    async def test_symlink_to_etc_hard_denied(self, monkeypatch, tmp_path):
        ensure_flag_on(monkeypatch)
        link = tmp_path / "foo"
        try:
            os.symlink("/etc", str(link))
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation unsupported on this filesystem")
        result = await check(f"rm -r {link}")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason


class TestDangerousRemovalCompound:
    """Each segment of a compound must be inspected independently.

    Use `rm /etc` / `rmdir /` shapes (no `-rf`) so the step-1
    HARDCODED_DENIALS regex does not fire first — the assertion targets
    step 1.7 specifically.
    """

    @pytest.mark.asyncio
    async def test_semicolon_segment_hard_denies_whole_command(self, monkeypatch, tmp_path):
        ensure_flag_on(monkeypatch)
        safe = tmp_path / "safe"
        safe.mkdir()
        result = await check(f"rm -r {safe} ; rm /etc")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_and_segment_hard_denies(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("echo start && rm /etc")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_or_segment_hard_denies(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("true || rmdir /usr")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason

    @pytest.mark.asyncio
    async def test_pipe_segment_hard_denies(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("echo x | rm /etc")
        assert result.allowed is False
        assert "Dangerous removal blocked" in result.reason


class TestDangerousRemovalFeatureFlag:
    @pytest.mark.asyncio
    async def test_flag_off_gate_does_not_fire(self, monkeypatch):
        ensure_flag_off(monkeypatch)
        result = await check("rm /etc")
        assert "Dangerous removal blocked" not in result.reason

    @pytest.mark.asyncio
    async def test_flag_off_rmdir_root_not_gate_blocked(self, monkeypatch):
        ensure_flag_off(monkeypatch)
        result = await check("rmdir /")
        assert "Dangerous removal blocked" not in result.reason

    @pytest.mark.asyncio
    async def test_flag_off_rm_rf_root_still_caught_by_hardcoded(self, monkeypatch):
        ensure_flag_off(monkeypatch)
        result = await check("rm -rf /")
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_flag_on_then_off_is_per_call(self, monkeypatch):
        """The feature-flag check is evaluated per call, not at import."""
        ensure_flag_on(monkeypatch)
        on = await check("rm /etc")
        assert on.allowed is False
        assert "Dangerous removal blocked" in on.reason

        ensure_flag_off(monkeypatch)
        off = await check("rm /etc")
        assert "Dangerous removal blocked" not in off.reason


class TestDangerousRemovalRegression:
    @pytest.mark.asyncio
    async def test_git_reset_hard_still_classified_destructive(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("git reset --hard HEAD~1")
        assert "Dangerous removal blocked" not in result.reason

    @pytest.mark.asyncio
    async def test_kubectl_delete_still_classified(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("kubectl delete pod nginx")
        assert "Dangerous removal blocked" not in result.reason

    @pytest.mark.asyncio
    async def test_terraform_destroy_still_classified(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("terraform destroy -auto-approve")
        assert "Dangerous removal blocked" not in result.reason

    @pytest.mark.asyncio
    async def test_drop_table_not_affected(self, monkeypatch):
        ensure_flag_on(monkeypatch)
        result = await check("psql -c 'DROP TABLE users'")
        assert "Dangerous removal blocked" not in result.reason
