"""The freeze is a fact about this checkout, not a promise in a document.

`ENG-25`. ROADMAP Law 1 freezes `engine/src/engine_core/`, `memory/`,
`herald/` and `learning_centre/`. The review found that the freeze had
already been breached and that nothing recorded it, which makes the law
a fiction - and a fiction is worse than an exception, because an
exception can be audited.

`docs/policy/LAW-1-MANIFEST.md` is the audit. This is what makes it one:
a manifest nothing checks decays into a document, and the next edit
inside the frozen tree lands silently exactly as the last one did.

The check is a `git diff` against the vendored base, so it cannot be
satisfied by editing the manifest - only by not editing the frozen tree,
or by listing what you did there.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "docs" / "policy" / "LAW-1-MANIFEST.md"

VENDORED_BASE = "95e8788"

FROZEN = (
    "engine/src/engine_core",
    "engine/src/memory",
    "engine/src/herald",
    "engine/src/learning_centre",
)

DECLARED = {
    "engine/src/learning_centre/pipeline.py",
    "engine/src/learning_centre/signals.py",
}


def _git(*args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        pytest.skip(f"git unavailable or history rewritten: {done.stderr.strip()[:120]}")
    return done.stdout


def _changed_since_vendoring() -> set[str]:
    """Every frozen file that differs from the vendored base RIGHT NOW.

    `<base>` and not `<base>..HEAD`: the two-dot form compares commit to
    commit, so an uncommitted edit inside the freeze is invisible to it -
    which is precisely when this guard is most useful, since the whole
    point is to stop the breach before it lands. Written the wrong way
    first, and caught by making a breach and watching the test pass.
    """
    out = _git("diff", "--name-only", VENDORED_BASE, "--", *FROZEN)
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_the_vendored_base_is_still_in_this_history():
    """If it is not, every claim below is unverifiable and says so."""
    assert _git("cat-file", "-t", VENDORED_BASE).strip() == "commit"


def test_nothing_in_the_frozen_tree_changed_that_the_manifest_does_not_list():
    changed = _changed_since_vendoring()
    undeclared = changed - DECLARED
    assert not undeclared, (
        "ENG-25: these files inside the Law 1 freeze changed after the "
        f"vendoring and are not in {MANIFEST.name}: {sorted(undeclared)}. "
        "Either move the change into engine/src/sim/ (the seam Law 1 leaves "
        "writable) or add a row to the manifest saying why it could not live "
        "there. A freeze nobody checks is not a freeze."
    )


def test_the_manifest_does_not_claim_an_exception_that_is_not_there():
    """The other direction. A manifest listing a breach that was reverted
    would overstate the damage, and the point of the file is to be exact."""
    changed = _changed_since_vendoring()
    phantom = DECLARED - changed
    assert not phantom, (
        f"{MANIFEST.name} lists {sorted(phantom)} as a Law 1 exception, but "
        "nothing in those files differs from the vendored base."
    )


def test_the_manifest_names_the_base_it_is_measured_against():
    """A manifest whose baseline is unstated cannot be re-derived, which
    is the state the review found it in - the table was empty because
    nobody had gone and looked the commit up."""
    text = MANIFEST.read_text(encoding="utf-8")
    assert VENDORED_BASE in text, (
        "the manifest does not name the vendored base, so its table cannot "
        "be checked by anyone who did not write it"
    )
    assert "engine/src/learning_centre/pipeline.py" in text


def test_every_exception_is_still_marked_in_the_code_itself():
    """Belt and braces: each breach carries its own label at the site, so a
    reader of the file learns it is an exception without knowing the
    manifest exists.

    Derived from DECLARED rather than naming pipeline.py alone, which is
    how this test stayed green through a second breach that carried no
    marker at all."""
    for relative in sorted(DECLARED):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "AUTHORISED LAW 1 EXCEPTION" in text, (
            f"{relative} is a declared Law 1 exception and carries no marker "
            "at the site. A reader of the file cannot tell it is one."
        )

    pipeline = ROOT / "engine" / "src" / "learning_centre" / "pipeline.py"
    assert pipeline.read_text(encoding="utf-8").count("AUTHORISED LAW 1 EXCEPTION") >= 3, (
        "pipeline.py has three edited sites and they no longer all carry a marker"
    )
