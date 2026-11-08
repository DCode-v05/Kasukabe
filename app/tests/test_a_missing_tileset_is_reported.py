"""A run that could not check something says so.

`GROW-27`. The Ville tileset is a gitignored external asset directory,
and the tests that need it behaved two different wrong ways without it.
`world/map/tests/test_the_town_is_walkable.py` FAILED - five hard
failures on `town.movement.world_map` being `None`, which reads exactly
like a broken map and is nothing of the kind. The end-to-end construction
chain in `world/tools/tests/test_construction.py` SKIPPED silently: in
CI, the only proof that a resident can petition, be granted a plot,
build, and have that building become a real map sector was one 's' in a
field of dots, and the suite reported green.

This host HAS the tileset, so neither state reproduces here by running
the suite. It is reproduced by pointing the gate at a directory that does
not exist - which is precisely what CI looks like.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import ville_assets

APP = Path(__file__).resolve().parents[1]


def test_the_gate_skips_rather_than_fails(monkeypatch) -> None:
    monkeypatch.setattr(ville_assets, "VILLE_DIR", Path("/nowhere/the_ville"))
    monkeypatch.setattr(ville_assets, "SKIPPED_FOR_ASSETS", set())
    with pytest.raises(pytest.skip.Exception):
        ville_assets.require_ville_assets("the town is walkable")


def test_the_gate_remembers_what_went_unproven(monkeypatch) -> None:
    monkeypatch.setattr(ville_assets, "VILLE_DIR", Path("/nowhere/the_ville"))
    monkeypatch.setattr(ville_assets, "SKIPPED_FOR_ASSETS", set())
    with pytest.raises(pytest.skip.Exception):
        ville_assets.require_ville_assets("the full construction chain")
    assert ville_assets.SKIPPED_FOR_ASSETS == {"the full construction chain"}


def test_both_asset_dependent_suites_go_through_the_gate() -> None:
    """The point of one gate is that there is only one."""
    for rel in (
        "world/map/tests/test_the_town_is_walkable.py",
        "world/tools/tests/test_construction.py",
    ):
        source = (APP / rel).read_text(encoding="utf-8")
        assert "require_ville_assets(" in source, (
            f"GROW-27: {rel} still decides for itself what an absent tileset "
            "means, which is how the same missing directory produced a fail "
            "in one file and a silent skip in the other"
        )


def test_a_run_that_could_not_check_says_so() -> None:
    """The whole finding, end to end: a pytest run whose asset-dependent
    test skipped must print what went unproven, not just a dot."""
    scratch = APP / "_grow27_scratch"
    scratch.mkdir(exist_ok=True)
    test_file = scratch / "test_needs_assets.py"
    test_file.write_text(
        textwrap.dedent(
            '''
            from pathlib import Path
            import ville_assets

            def test_the_town_is_walkable(monkeypatch):
                monkeypatch.setattr(ville_assets, "VILLE_DIR", Path("/nowhere"))
                ville_assets.require_ville_assets("the town is one connected walkable place")
            '''
        ),
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q", "-p", "no:cacheprovider"],
            cwd=APP,
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        test_file.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)
    assert "NOT PROVEN IN THIS RUN" in proc.stdout, (
        "GROW-27: the run skipped a test for a missing tileset and reported "
        f"nothing but a green summary:\n{proc.stdout[-2000:]}"
    )
    assert "the town is one connected walkable place" in proc.stdout, (
        "the summary names no specific claim, so a reader learns only that "
        "something was skipped"
    )
