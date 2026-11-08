"""Repo-wide invariant tests for the app monorepo.

These are not module tests - they check laws that app/CONVENTIONS.md
declares binding across every module. They live at the top level because
no single module owns them (the world/broker/research suites each cover
only their own package).

Today this enforces the boto3 import boundary: "broker owns ALL Bedrock
access; nothing else may import boto3" (app/CONVENTIONS.md, "Module
boundaries"). Once the ledger exists, the $1000 invariant
(sum of wallets + treasury + burned == $1000) belongs here too.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

# Only broker/ may import boto3 (DESIGN.md section 9: "Broker is the ONLY
# path to Bedrock").
BOTO3_ALLOWED_TOP_LEVEL_DIRS = {"broker"}

# Never walk into virtualenvs, caches, or build output looking for source.
SKIP_DIR_NAMES = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "build",
    "dist",
    "node_modules",
    "*.egg-info",
}


def iter_python_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
            continue
        files.append(path)
    return files


def imports_boto3(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "boto3" or alias.name.startswith("boto3.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "boto3" or node.module.startswith("boto3.")):
                return True
    return False


def test_only_broker_imports_boto3() -> None:
    violations = []
    for path in iter_python_files(APP_ROOT):
        top_level_dir = path.relative_to(APP_ROOT).parts[0]
        if top_level_dir in BOTO3_ALLOWED_TOP_LEVEL_DIRS:
            continue
        if imports_boto3(path):
            violations.append(str(path.relative_to(APP_ROOT)))

    assert not violations, (
        "boto3 imported outside broker/, breaking the module boundary in "
        f"app/CONVENTIONS.md: {violations}"
    )


# ── game/ never imports world/ ─────────────────────────────────────────
#
# UI-14. app/CONVENTIONS.md: "no package imports another package's
# internals across this diagram", and game/README.md makes game/
# presentation only - "possibly a separately deployed process", which an
# import of world/ makes impossible.
#
# The file above enforced exactly one boundary, boto3, so this one was
# crossed and nothing said anything: `game/realsim.py` imported
# `world.make_genesis_subset` and wrote its output INTO `app/world/`, and
# three tests in `game/tests/test_realsim.py` imported `world.life` and
# `world.make_genesis_subset` besides. A boundary with no test is a
# comment.
#
# game/ talks to world/ over HTTP and, for the real-sim controller, by
# spawning it as a process. Both are still fine; a Python import is not.

GAME_MAY_NOT_IMPORT = "world"

# One exemption, named, with a reason, and asserted to stay exactly one.
#
# `build_rules.py` is a BUILD-TIME script under `static/`, run by hand
# ("regenerate: python3 game/static/js/township/build_rules.py") and
# imported by nothing. It bundles `world/rules/village_rules.json` into a
# JS constant because `township-v2.html` is openable straight off disk and
# `fetch()` cannot read a `file://` path. It is not part of the deployed
# game package, so it does not make game/ undeployable - which is what
# this boundary exists to protect.
#
# Anything else that appears here is a real violation, which is why the
# set is pinned rather than pattern-matched: a second file quietly joining
# a wildcard is how a boundary stops meaning anything.
BUILD_TIME_EXEMPT = {"game/static/js/township/build_rules.py"}


def imports_package(path: Path, package: str) -> list[str]:
    """Every `import <package>...` / `from <package>... import` in a file.

    AST rather than a substring search: the string "world" appears in
    prose all over this package, and `import worldly_things` is not a
    violation.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(package + "."):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # `from . import x` inside game/ is level>0 and never crosses.
            if node.level == 0 and (module == package or module.startswith(package + ".")):
                hits.append(module)
    return hits


def test_game_does_not_import_world() -> None:
    violations: dict[str, list[str]] = {}
    for path in iter_python_files(APP_ROOT / "game"):
        relative = path.relative_to(APP_ROOT).as_posix()
        if relative in BUILD_TIME_EXEMPT:
            continue
        hits = imports_package(path, GAME_MAY_NOT_IMPORT)
        if hits:
            violations[relative] = hits

    assert not violations, (
        "UI-14: game/ imports world/, which app/CONVENTIONS.md forbids and "
        "which makes game/ undeployable on its own. Talk to world over HTTP, "
        "or spawn it as a process the way realsim.py does: "
        f"{violations}"
    )


def test_game_does_not_write_into_another_package() -> None:
    """The other half of UI-14: `realsim.py` wrote its generated genesis
    into `app/world/`. Reaching into a sibling package's directory is the
    same boundary crossing as importing it, and leaves files that package
    did not put there."""
    source = (APP_ROOT / "game" / "realsim.py").read_text(encoding="utf-8")
    assert 'self.app_root / "world"' not in source, (
        "UI-14: game/ writes files into app/world/"
    )


def test_the_exemption_list_has_not_grown() -> None:
    """One build-time script, and it still exists. An exemption for a file
    that has moved or been deleted is a hole nobody is watching."""
    assert len(BUILD_TIME_EXEMPT) == 1
    for relative in BUILD_TIME_EXEMPT:
        assert (APP_ROOT / relative).exists(), (
            f"{relative} is exempt from the game->world boundary and no "
            "longer exists - remove the exemption"
        )
