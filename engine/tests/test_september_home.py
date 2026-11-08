"""SEPTEMBER_HOME — the one root that holds everything the engine owns.

Every path the engine writes derives from this root: the database, config,
credentials, skills, workspaces, sidechains, caches, logs. One directory to
place on boot, one to capture on teardown, one to point at on a laptop.

Resolution follows codex's ``find_codex_home`` discipline (utils/home-dir):
an explicitly-set root that is missing or is not a directory is FATAL and names
the offending path, because that is a misconfigured mount or a typo and the
alternative is an engine that silently writes a user's memory somewhere nobody
will look. An unset root defaults under the user's home and is created.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.infra.september_home import (
    DEFAULT_HOME_DIRNAME,
    SEPTEMBER_HOME_ENV,
    ensure_layout,
    resolve_september_home,
    september_paths,
)


# ── Resolution ────────────────────────────────────────────────────


class TestResolve:
    def test_explicit_existing_directory_is_used(self, tmp_path):
        assert resolve_september_home(str(tmp_path)) == tmp_path.resolve()

    def test_result_is_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "rel").mkdir()
        assert resolve_september_home("rel").is_absolute()

    def test_tilde_is_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "elsewhere").mkdir()
        assert resolve_september_home("~/elsewhere") == (tmp_path / "elsewhere").resolve()

    def test_env_var_is_read_when_no_argument_given(self, tmp_path, monkeypatch):
        monkeypatch.setenv(SEPTEMBER_HOME_ENV, str(tmp_path))
        assert resolve_september_home() == tmp_path.resolve()

    def test_unset_defaults_under_user_home_and_is_created(self, tmp_path, monkeypatch):
        monkeypatch.delenv(SEPTEMBER_HOME_ENV, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        got = resolve_september_home()
        assert got == (tmp_path / DEFAULT_HOME_DIRNAME).resolve()
        assert got.is_dir()

    def test_empty_env_value_is_treated_as_unset(self, tmp_path, monkeypatch):
        monkeypatch.setenv(SEPTEMBER_HOME_ENV, "   ")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_september_home() == (tmp_path / DEFAULT_HOME_DIRNAME).resolve()


# ── Fails loudly ──────────────────────────────────────────────────


class TestFailsLoudly:
    def test_explicit_missing_path_is_fatal(self, tmp_path):
        """A set-but-missing root is a bad mount or a typo, never a fresh install."""
        missing = tmp_path / "not-there"
        with pytest.raises(FileNotFoundError) as excinfo:
            resolve_september_home(str(missing))
        assert str(missing) in str(excinfo.value)
        assert SEPTEMBER_HOME_ENV in str(excinfo.value)

    def test_explicit_path_that_is_a_file_is_fatal(self, tmp_path):
        f = tmp_path / "a-file"
        f.write_text("not a directory", encoding="utf-8")
        with pytest.raises(NotADirectoryError) as excinfo:
            resolve_september_home(str(f))
        assert str(f) in str(excinfo.value)

    def test_error_names_the_path_not_just_the_variable(self, tmp_path):
        """An operator needs to see WHICH path was wrong, not that one was."""
        missing = tmp_path / "typo-here"
        with pytest.raises(FileNotFoundError) as excinfo:
            resolve_september_home(str(missing))
        assert "typo-here" in str(excinfo.value)


# ── Layout ────────────────────────────────────────────────────────


EXPECTED_SUBDIRS = (
    "config",
    "credentials",
    "skills",
    "plugins",
    "workspaces",
    "sidechains",
    "results",
    "cache",
    "tmp",
    "log",
)


class TestLayout:
    def test_paths_all_live_under_the_root(self, tmp_path):
        paths = september_paths(tmp_path)
        for name, path in paths.items():
            assert tmp_path in path.parents or path == tmp_path, f"{name} escaped the root"

    def test_database_sits_at_the_root(self, tmp_path):
        assert september_paths(tmp_path)["db"] == tmp_path / "brain.sqlite"

    def test_every_expected_subdir_is_named(self, tmp_path):
        paths = september_paths(tmp_path)
        for sub in EXPECTED_SUBDIRS:
            assert sub in paths, f"{sub} missing from the layout"

    def test_ensure_layout_creates_every_directory(self, tmp_path):
        ensure_layout(tmp_path)
        for sub in EXPECTED_SUBDIRS:
            assert (tmp_path / sub).is_dir(), f"{sub} not created"

    def test_ensure_layout_is_idempotent(self, tmp_path):
        ensure_layout(tmp_path)
        (tmp_path / "skills" / "keep-me").mkdir()
        ensure_layout(tmp_path)
        assert (tmp_path / "skills" / "keep-me").is_dir()

    def test_ensure_layout_does_not_create_the_database_file(self, tmp_path):
        """Directories are ours to make. The database is SQLite's."""
        ensure_layout(tmp_path)
        assert not (tmp_path / "brain.sqlite").exists()

    def test_ensure_layout_returns_the_root(self, tmp_path):
        assert ensure_layout(tmp_path) == tmp_path.resolve()


# ── Wired to config ───────────────────────────────────────────────


class TestSettingsDeriveFromHome:
    def test_db_path_defaults_under_the_home(self, tmp_path, monkeypatch):
        from src.config import Settings

        monkeypatch.setenv(SEPTEMBER_HOME_ENV, str(tmp_path))
        settings, errors = Settings.build_from_env({})
        assert errors == []
        assert Path(settings.sqlite_db_path) == tmp_path.resolve() / "brain.sqlite"

    def test_permission_store_defaults_under_the_home(self, tmp_path, monkeypatch):
        from src.config import Settings

        monkeypatch.setenv(SEPTEMBER_HOME_ENV, str(tmp_path))
        settings, _ = Settings.build_from_env({})
        assert (
            Path(settings.permission_store_user_path)
            == tmp_path.resolve() / "config" / "permissions.json"
        )

    def test_explicit_db_path_still_wins(self, tmp_path, monkeypatch):
        """The root sets defaults; it does not override an operator's choice."""
        from src.config import Settings

        monkeypatch.setenv(SEPTEMBER_HOME_ENV, str(tmp_path))
        settings, _ = Settings.build_from_env({"SQLITE_DB_PATH": "/tmp/elsewhere.sqlite"})
        assert settings.sqlite_db_path == "/tmp/elsewhere.sqlite"

    def test_september_home_is_documented_in_env_example(self):
        """test_config asserts every env var is documented; hold this one too."""
        text = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
        assert SEPTEMBER_HOME_ENV in text


# ── No stray absolute paths left behind ───────────────────────────


class TestNoHardcodedRoots:
    def test_no_source_file_hardcodes_the_old_data_dir(self):
        """/data was the docker volume. Nothing may assume it any more."""
        src = Path(__file__).parent.parent / "src"
        offenders = []
        for path in src.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if '"/data/' in line or "'/data/" in line:
                    offenders.append(f"{path.name}:{lineno}")
        assert offenders == [], f"hardcoded /data paths remain: {offenders}"

    def test_sidechain_default_lands_under_the_home(self, tmp_path, monkeypatch):
        from src.engine_core.fork_subagent import sidechain_dir_default

        monkeypatch.setenv(SEPTEMBER_HOME_ENV, str(tmp_path))
        got = Path(sidechain_dir_default())
        # Under the configured root — not the system temp dir, wherever that is.
        # (pytest's tmp_path itself lives under /tmp, so a literal prefix check
        # here would pass for the wrong reason.)
        assert got == tmp_path.resolve() / "sidechains"

    def test_engine_key_hash_lands_under_the_home(self, tmp_path, monkeypatch):
        from src.auth import persisted_key_hash_path

        monkeypatch.setenv(SEPTEMBER_HOME_ENV, str(tmp_path))
        got = Path(persisted_key_hash_path())
        assert got == tmp_path.resolve() / "credentials" / "engine_key_hash"
        assert not str(got).startswith("/data")


# ── Environment isolation for the rest of the suite ───────────────


def test_env_var_name_is_stable():
    """Renaming this breaks every deployment. Pin it."""
    assert SEPTEMBER_HOME_ENV == "SEPTEMBER_HOME"
    assert DEFAULT_HOME_DIRNAME == ".september"
    assert os.sep in str(Path.home())
