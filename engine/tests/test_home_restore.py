"""Restore proof: a fresh engine plus one directory is the whole user.

This is the claim the managed filesystem exists to make. A runtime places one
tree and starts an engine; the user's memory, skills, credentials and permission
grants come back. Nothing the engine owns lives outside that tree, so nothing is
silently lost when it moves between machines.

The test proves it the only way that counts: write real state through live call
sites into one root, throw the engine away, point a brand-new engine at the same
root, and read the state back.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.auth import persisted_key_hash_path
from src.catalog import SkillRoot, create_user_skill, load_skills_from_roots
from src.infra.september_home import ensure_layout, september_paths
from src.infra.sqlite_pool import SqlitePool
from src.sandbox.permission_store import PermissionProvenance, PermissionStore
from src.sandbox.permissions import PermissionBehavior, PermissionRule


async def migrated_pool(db_path) -> SqlitePool:
    pool = SqlitePool(str(db_path))
    await pool.connect()
    migrations = Path(__file__).parent.parent / "migrations"
    for sql_file in sorted(migrations.glob("*.sql")):
        await pool.executescript(sql_file.read_text(encoding="utf-8"))
    await pool.commit()
    return pool


@pytest.mark.asyncio
async def test_a_root_carries_the_whole_user(tmp_path, monkeypatch):
    """Write state into one root, discard everything, restore from the root alone."""
    home = tmp_path / "home"
    monkeypatch.setenv("SEPTEMBER_HOME", str(home))
    home.mkdir()
    ensure_layout(home)
    paths = september_paths(home)

    pool = await migrated_pool(paths["db"])
    await pool.execute(
        "INSERT INTO conversations (thread_id, summary) VALUES (?, ?)",
        ("thread-1", "Remembered conversation"),
    )
    await pool.commit()
    await pool.close()

    create_user_skill(home, {"name": "kept-skill", "description": "Survives", "prompt": "Body"})

    store = PermissionStore(user_path=paths["config"] / "permissions.json")
    store.add_rule(
        PermissionRule(
            tool="Bash",
            pattern="npm test",
            behavior=PermissionBehavior.ALLOW,
            source=PermissionProvenance.USER.value,
        )
    )
    store.save(PermissionProvenance.USER)

    key_file = paths["credentials"] / "engine_key_hash"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text("a" * 64, encoding="utf-8")

    relocated = tmp_path / "relocated"
    shutil.copytree(home, relocated)
    shutil.rmtree(home)
    monkeypatch.setenv("SEPTEMBER_HOME", str(relocated))
    new_paths = september_paths(relocated)

    pool2 = SqlitePool(str(new_paths["db"]))
    await pool2.connect()
    summary = await pool2.fetchval(
        "SELECT summary FROM conversations WHERE thread_id = ?", ("thread-1",)
    )
    await pool2.close()
    assert summary == "Remembered conversation"

    skills = load_skills_from_roots([SkillRoot(path=new_paths["skills"], source="user")])
    assert "kept-skill" in skills
    assert skills["kept-skill"].data["description"] == "Survives"

    store2 = PermissionStore(user_path=new_paths["config"] / "permissions.json")
    store2.load()
    assert [r.tool for r in store2.get_rules()] == ["Bash"]
    assert store2.get_rules()[0].source == PermissionProvenance.USER.value

    assert persisted_key_hash_path() == str(new_paths["credentials"] / "engine_key_hash")
    assert (new_paths["credentials"] / "engine_key_hash").read_text(encoding="utf-8") == "a" * 64


@pytest.mark.asyncio
async def test_nothing_the_engine_writes_escapes_the_root(tmp_path, monkeypatch):
    """Every derived path must resolve inside the root, or capture misses it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SEPTEMBER_HOME", str(home))
    ensure_layout(home)

    from src.engine_core.fork_subagent import sidechain_dir_default
    from src.sandbox.executor import workspace_root

    resolved = home.resolve()
    for label, path in {
        "key hash": persisted_key_hash_path(),
        "sidechains": sidechain_dir_default(),
        "workspaces": str(workspace_root()),
    }.items():
        assert str(path).startswith(str(resolved)), f"{label} escaped the root: {path}"


@pytest.mark.asyncio
async def test_settings_derive_every_path_from_the_root(tmp_path, monkeypatch):
    """Boot config points at the root without a per-path env var."""
    from src.config import Settings

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SEPTEMBER_HOME", str(home))

    settings, errors = Settings.build_from_env({})
    assert errors == []
    resolved = home.resolve()
    assert Path(settings.sqlite_db_path).parent == resolved
    assert Path(settings.permission_store_user_path).parent == resolved / "config"


def test_a_root_that_is_a_file_stops_boot(tmp_path, monkeypatch):
    """A typo'd mount must fail at construction, not at first write."""
    from src.config import Settings

    bad = tmp_path / "not-a-dir"
    bad.write_text("oops", encoding="utf-8")
    monkeypatch.setenv("SEPTEMBER_HOME", str(bad))

    with pytest.raises(NotADirectoryError):
        Settings.build_from_env({})


@pytest.mark.asyncio
async def test_boot_materializes_the_whole_layout(tmp_path, monkeypatch):
    """A real boot must leave the full tree behind, not just what it used.

    Without this, the layout is whatever the last run happened to touch, and a
    runtime capturing the root sees a different shape per engine.
    """
    from src.bootstrap import bootstrap, shutdown
    from src.config import Settings
    from src.infra.september_home import LAYOUT

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SEPTEMBER_HOME", str(home))

    settings = Settings(
        cdt_url="http://citadel-test",
        cdt_key="sk-sept-cit-test-not-real",
        llm_model="claude-haiku-4-5",
        sqlite_db_path=":memory:",
        catalog_dir=str(Path(__file__).parent.parent / "catalog"),
        ad_encryption_key="",
    )
    state, _report = await bootstrap(settings=settings)
    try:
        for rel in LAYOUT.values():
            assert (home / rel).is_dir(), f"boot did not create {rel}/"
    finally:
        await shutdown(state)


def test_prod_image_points_the_root_at_a_writable_path(tmp_path):
    """The prod rootfs is read-only, so an unset root would fail at boot.

    The Dockerfile must pin SEPTEMBER_HOME at the durable volume. Tests run in
    a writable container and never hit this, so assert it against the file.
    """
    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV SEPTEMBER_HOME=" in dockerfile, "prod image does not pin the engine root"
    line = next(ln for ln in dockerfile.splitlines() if ln.startswith("ENV SEPTEMBER_HOME="))
    root = line.split("=", 1)[1].strip()
    assert root.startswith("/"), "engine root must be absolute"
    assert f"VOLUME {root}" in dockerfile, f"{root} is not declared as a writable volume"


def test_permissions_file_written_is_valid_json(tmp_path, monkeypatch):
    """The restored file has to be readable by something other than us."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SEPTEMBER_HOME", str(home))
    ensure_layout(home)
    paths = september_paths(home)

    store = PermissionStore(user_path=paths["config"] / "permissions.json")
    store.add_rule(
        PermissionRule(
            tool="Bash",
            pattern="ls",
            behavior=PermissionBehavior.DENY,
            source=PermissionProvenance.USER.value,
        )
    )
    store.save(PermissionProvenance.USER)

    data = json.loads((paths["config"] / "permissions.json").read_text(encoding="utf-8"))
    assert data == {"deny": ["Bash(ls)"]}
