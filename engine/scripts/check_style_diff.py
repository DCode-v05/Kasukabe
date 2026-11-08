#!/usr/bin/env python3
"""Style gate for changed Python code.

Enforces two house rules on *added* lines only (diff scoped, so it judges what a
change introduces, never the pre-existing tree):

  1. No em dashes. The character U+2014 must not appear on any added line.
  2. No new leading-underscore identifiers. A newly-defined name that starts
     with a single underscore is rejected. Dunders (``__x__``) and a small
     documented allowlist are exempt. Only *definitions/bindings* are judged
     (function and class names, assignment targets including ``self._x = ...``,
     annotated assignments, function parameters, loop/with/comprehension
     targets), so reading an already-existing private name never trips the gate.

Scope: ``*.py`` under ``src/`` and ``tests/`` only.

Usage:
    python scripts/check_style_diff.py [--base <git-ref>]

``--base`` defaults to ``origin/main``; the diff is taken from the merge base of
that ref and HEAD so changes already on the base branch are ignored. Pass
``--base HEAD`` to judge the current working tree (uncommitted edits plus new
files). Untracked ``*.py`` files under the covered roots count as fully added.

Exit status is 1 when any violation is found, 0 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys

EM_DASH = "—"

COVERED_ROOTS = ("src/", "tests/")

ALLOWLIST: frozenset[str] = frozenset(
    {
        "_",
        "_exit",
        "_fields",
        "_fields_",
        "_source",
        "_skill_dir",
        "_db",
        "_single_user",
        "_struggle",
        "_start",
        "_end",
        "_vec",
        "_fts",
    }
)

NOQA = "noqa: style"

DUNDER = re.compile(r"^__.+__$")


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def resolve_diff_base(base: str) -> str:
    """Return the ref to diff from. For a branch/commit other than HEAD, use the
    merge base with HEAD so base-branch history is excluded."""
    if base == "HEAD":
        return "HEAD"
    merge_base = run(["git", "merge-base", base, "HEAD"]).strip()
    return merge_base or base


def covered(path: str) -> bool:
    return path.endswith(".py") and path.startswith(COVERED_ROOTS)


def added_lines(base: str) -> dict[str, dict[int, str]]:
    """Map each covered file to {new_line_number: added_text} for its added lines."""
    out: dict[str, dict[int, str]] = {}
    diff = run(["git", "diff", "--unified=0", "--no-color", base, "--", *COVERED_ROOTS])
    path: str | None = None
    new_line = 0
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for line in diff.splitlines():
        if line.startswith("+++ "):
            raw = line[4:]
            path = raw[2:] if raw.startswith("b/") else raw
            path = None if path == "/dev/null" or not covered(path) else path
            continue
        if path is None:
            continue
        m = hunk.match(line)
        if m:
            new_line = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(path, {})[new_line] = line[1:]
            new_line += 1

    for path in run(["git", "ls-files", "--others", "--exclude-standard", "--", *COVERED_ROOTS]).splitlines():
        if not covered(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                out[path] = {i: text for i, text in enumerate(fh.read().splitlines(), start=1)}
        except OSError:
            continue
    return out


def defined_underscore_names(source: str) -> list[tuple[int, str]]:
    """Return (lineno, name) for every leading-underscore name that a definition
    or binding introduces. Returns [] if the file cannot be parsed."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []

    def consider(name: str, lineno: int) -> None:
        if not name.startswith("_") or DUNDER.match(name) or name in ALLOWLIST:
            return
        found.append((lineno, name))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            consider(node.name, node.lineno)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            consider(node.id, node.lineno)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            consider(node.attr, node.lineno)
        elif isinstance(node, ast.arg):
            consider(node.arg, node.lineno)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()

    base = resolve_diff_base(args.base)
    per_file = added_lines(base)

    violations: list[str] = []
    for path, lines in sorted(per_file.items()):
        for lineno, text in sorted(lines.items()):
            if EM_DASH in text and NOQA not in text:
                violations.append(f"{path}:{lineno}: em dash (U+2014) is not allowed")

        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except OSError:
            continue
        for lineno, name in defined_underscore_names(source):
            text = lines.get(lineno)
            if text is None or NOQA in text:
                continue
            violations.append(
                f"{path}:{lineno}: new leading-underscore name '{name}' is not allowed"
            )

    if violations:
        print("Style gate failed on changed Python lines:\n")
        for v in sorted(set(violations)):
            print(f"  {v}")
        print(
            f"\n{len(set(violations))} issue(s). Rules: no em dashes; no new "
            f"leading-underscore names (dunders/allowlist exempt). "
            f"Append '# {NOQA}' to a line to suppress a false positive."
        )
        return 1

    print("Style gate passed: no em dashes or new leading-underscore names on changed Python lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
