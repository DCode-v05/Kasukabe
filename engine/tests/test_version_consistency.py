"""The engine version is declared in four places; they must all agree.

Written because they did not. ``pyproject.toml`` had been bumped to 3.0.0 while
``src/__init__.py`` still said 2.4.0, two majors behind, and nothing noticed for
the simple reason that nothing reads ``__version__``: it is exported, never
imported. A constant with no consumer has no failure mode short of someone
reading it and believing it.

So the consumer is this test. Bump the version and every one of these has to move
together or the suite goes red naming the ones that lagged.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def declared_versions() -> dict[str, str]:
    """Every place the engine states its own version, and what each one says."""
    found: dict[str, str] = {}

    pyproject = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert m, "pyproject.toml has no top-level version"
    found["pyproject.toml"] = m.group(1)

    init = (ROOT / "src" / "__init__.py").read_text()
    m = re.search(r'^__version__ = "([^"]+)"', init, re.M)
    assert m, "src/__init__.py has no __version__"
    found["src/__init__.py"] = m.group(1)

    llms = (ROOT / "llms.txt").read_text()
    m = re.search(r"runtime \(v([0-9]+\.[0-9]+\.[0-9]+)", llms)
    assert m, "llms.txt no longer states the version in the form 'runtime (vX.Y.Z'"
    found["llms.txt"] = m.group(1)

    atlas = (ROOT / "architecture" / "sections" / "00_overview.md").read_text()
    m = re.search(r"packaged `september-engine ([0-9]+\.[0-9]+\.[0-9]+)`", atlas)
    assert m, "architecture/sections/00_overview.md no longer states the packaged version"
    found["architecture/sections/00_overview.md"] = m.group(1)

    return found


def test_all_declared_versions_agree():
    found = declared_versions()
    distinct = set(found.values())
    assert len(distinct) == 1, (
        "the engine version disagrees between files: "
        + ", ".join(f"{where}={ver}" for where, ver in sorted(found.items()))
    )


def test_the_atlas_generation_label_matches_the_major():
    """The prose calls it "engine vN"; N has to be the major that ships.

    This is its own check because the two live in the same sentence and drifted
    apart there once already: the package said 3.0.0 while the sentence around it
    still said v2.
    """
    versions = declared_versions()
    major = versions["pyproject.toml"].split(".")[0]
    atlas = (ROOT / "architecture" / "sections" / "00_overview.md").read_text()
    m = re.search(r"September intelligence engine v([0-9]+)", atlas)
    assert m, "00_overview.md no longer carries a 'September intelligence engine vN' label"
    assert m.group(1) == major, (
        f"the atlas calls this engine v{m.group(1)} but the package ships {versions['pyproject.toml']}"
    )


@pytest.mark.parametrize("where", sorted(declared_versions()))
def test_each_declaration_is_a_release_version(where):
    """No trailing '-dev', no '+local': what ships is what is written down."""
    value = declared_versions()[where]
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value), (
        f"{where} declares {value!r}, which is not a bare X.Y.Z release version"
    )
