"""Bypass-immune dangerous path protection.

Canonical source of DANGEROUS_FILES / DANGEROUS_DIRS constants and the
`check_path_safety()` gate used by the permission pipeline in permissions.py.

This module exists so dangerous-target checks run BEFORE user rule evaluation
in `check_permission()`, making them immune to allow-rule, permission-mode, and
hook overrides. Two categories of danger are distinguished via the
`classifier_approvable` flag on `PathSafetyResult`:

    - classifier_approvable=False  -> always block (Windows path evasion,
      null bytes). A future AUTO mode MUST NOT override these.
    - classifier_approvable=True   -> normal dangerous file/dir. A future
      AUTO mode MAY consult an AI classifier to override.

The pipeline encodes the distinction in the deny reason using stable prefixes
("path-evasion: " and "dangerous-target: ") so future consumers can grep.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Canonical constants — imported by permissions.py for defense-in-depth reuse
# ---------------------------------------------------------------------------

# Shell config files — LLM writes here gain persistent code execution on the
# next shell launch. Treat as dangerous targets.
DANGEROUS_FILES: frozenset[str] = frozenset(
    {
        ".bashrc",
        ".bash_profile",
        ".zshrc",
        ".zprofile",
        ".profile",
        ".gitconfig",
        ".gitmodules",
        ".ripgreprc",
        ".mcp.json",
        ".claude.json",
    }
)

# Directories that contain config, hooks, or tool state the LLM must not touch.
DANGEROUS_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".vscode",
        ".idea",
        ".claude",
    }
)

# Pre-lowercased for case-insensitive matching on Windows-style and POSIX paths.
DANGEROUS_FILES_LOWER: frozenset[str] = frozenset(f.lower() for f in DANGEROUS_FILES)
DANGEROUS_DIRS_LOWER: frozenset[str] = frozenset(d.lower() for d in DANGEROUS_DIRS)


# ---------------------------------------------------------------------------
# Feature flag — gates NEW checks only; existing checks remain always-on.
# ---------------------------------------------------------------------------


def enhanced_evasion_enabled() -> bool:
    return os.environ.get("FEATURE_WINDOWS_PATH_EVASION", "true").lower() != "false"


# ---------------------------------------------------------------------------
# Windows-evasion detection patterns
# ---------------------------------------------------------------------------

# 8.3 short names: any component matching NAME~<digit> (e.g. GIT~1, PROGRA~1).
SHORT_NAME_RE: re.Pattern[str] = re.compile(r"~\d")

# Windows drive letter at the start of a path, e.g. "C:" or "D:".
WIN_DRIVE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:$")

# Drive-relative path: "C:file.txt" (no separator after colon). Resolves
# relative to CWD on that drive — ambiguous target the LLM can exploit.
DRIVE_RELATIVE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:[^/\\]")

# DOS reserved device names — Windows intercepts these regardless of extension.
# CON.txt doesn't create a file; it writes to the console device.
DOS_DEVICE_NAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# Mixed-separator UNC paths: catches /\\server, \\//server, etc.
MIXED_UNC_RE: re.Pattern[str] = re.compile(r"^[/\\]{2,}[^\s/\\]")

# WebDAV SSL/port UNC patterns: \\server@SSL@8443\path
WEBDAV_SSL_PORT_RE: re.Pattern[str] = re.compile(r"@SSL(?:@\d+)?[/\\]", re.IGNORECASE)
WEBDAV_PORT_SSL_RE: re.Pattern[str] = re.compile(r"@\d+@SSL[/\\]", re.IGNORECASE)

# Windows WebDAV redirector marker — \\server\DavWWWRoot\path
DAVWWWROOT_RE: re.Pattern[str] = re.compile(r"DavWWWRoot", re.IGNORECASE)

# POSIX device files that produce infinite output or block on read/write.
BLOCKED_DEVICE_PATHS: frozenset[str] = frozenset(
    {
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/full",
        "/dev/stdin",
        "/dev/tty",
        "/dev/console",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/fd/0",
        "/dev/fd/1",
        "/dev/fd/2",
    }
)

# /proc/<pid>/fd/{0,1,2} — stdio aliases via procfs.
PROC_FD_RE: re.Pattern[str] = re.compile(r"^/proc/\d+/fd/[012]$")


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PathSafetyResult:
    """Outcome of a bypass-immune dangerous-path check.

    reason is prefixed with one of:
      - "path-evasion: <detail>"     -> classifier_approvable=False
      - "dangerous-target: <detail>" -> classifier_approvable=True
    """

    is_dangerous: bool
    reason: str
    classifier_approvable: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def split_components(path: str) -> list[str]:
    """Split a path into components using both POSIX and Windows separators.

    The leading slash on absolute POSIX paths produces an empty first element,
    which we drop so downstream per-component checks don't misfire.

    Returns raw (un-normalized) components. Use ``nfkc_components`` when
    NFKC-normalized components are needed for dangerous-target matching.
    """
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    return [p for p in parts if p]


def nfkc_components(components: list[str]) -> list[str]:
    """Return NFKC-normalized copies of path components.

    NFKC decomposes compatibility characters (fullwidth, ligatures) to their
    ASCII equivalents: ``ﬁ`` → ``fi``, fullwidth ``Ａ`` → ``A``.
    Used for dangerous-target matching only — evasion checks (ADS, 8.3,
    trailing dots) must run on raw components to avoid false positives
    (e.g. fullwidth colon U+FF1A normalizes to ASCII ``:`` which would
    trip the ADS check on legitimate POSIX filenames).
    """
    if not enhanced_evasion_enabled():
        return components
    return [unicodedata.normalize("NFKC", c) for c in components]


def evasion(reason: str) -> PathSafetyResult:
    """Shorthand for a bypass-immune evasion result (classifier_approvable=False)."""
    return PathSafetyResult(is_dangerous=True, reason=reason, classifier_approvable=False)


def detect_evasion(original_path: str, components: list[str]) -> PathSafetyResult | None:
    """Return a result when `original_path` matches a Windows-evasion pattern.

    Windows-evasion checks run on the original path only — the resolver in
    `os.path.realpath` normalizes most of these away, so checking the resolved
    path would miss the attack.
    """
    enhanced = enhanced_evasion_enabled()

    if original_path.startswith("\\\\?\\"):
        return evasion(r"path-evasion: long-path prefix \\?\ not allowed")

    # UNC paths — credential-leak vector on Windows. Also caught by
    # validate_path later, but hoisting the check here closes the gap for
    # SANDBOXED tools that never reach validate_path.
    if original_path.startswith("\\\\") or original_path.startswith("//"):
        return evasion("path-evasion: UNC paths not allowed")

    if enhanced:
        if MIXED_UNC_RE.match(original_path):
            return evasion("path-evasion: UNC paths not allowed")
        if WEBDAV_SSL_PORT_RE.search(original_path) or WEBDAV_PORT_SSL_RE.search(original_path):
            return evasion("path-evasion: WebDAV UNC path not allowed")
        if DAVWWWROOT_RE.search(original_path):
            return evasion("path-evasion: WebDAV DavWWWRoot path not allowed")

    # Drive-relative path — "C:file.txt" (no separator after colon) resolves
    # relative to CWD on that drive. The ambiguity itself is the threat.
    if enhanced and DRIVE_RELATIVE_RE.match(original_path):
        return evasion("path-evasion: drive-relative path (ambiguous target)")

    for part in components:
        # Skip drive letter — it's neither ADS nor short name, and the
        # trailing-dot check would misread it.
        if WIN_DRIVE_RE.match(part):
            continue

        if ":" in part:
            return evasion(f"path-evasion: NTFS alternate data stream detected in {part!r}")

        if SHORT_NAME_RE.search(part):
            return evasion(f"path-evasion: Windows 8.3 short name detected in {part!r}")

        # Trailing dots/spaces — Windows silently strips on open(). Skip `.`
        # and `..` (legitimate POSIX markers); `...` and longer fall through.
        if part in (".", ".."):
            continue
        if part.rstrip(". ") != part:
            return evasion(f"path-evasion: trailing dot/space in component {part!r}")

        # DOS reserved device names — Windows intercepts regardless of
        # extension. Leading dots stripped (Windows ignores them too).
        if enhanced:
            stripped = part.lstrip(".")
            stem = stripped.split(".")[0].upper() if stripped else ""
            if stem in DOS_DEVICE_NAMES:
                return evasion(f"path-evasion: DOS reserved device name {stem!r}")

    return None


def detect_dangerous_target(components: list[str]) -> PathSafetyResult | None:
    """Return a result when any component matches DANGEROUS_FILES / DANGEROUS_DIRS.

    Case-insensitive. Checks the basename against DANGEROUS_FILES and every
    component against DANGEROUS_DIRS.
    """
    if not components:
        return None

    basename_lower = components[-1].lower()
    if basename_lower in DANGEROUS_FILES_LOWER:
        return PathSafetyResult(
            is_dangerous=True,
            reason=f"dangerous-target: write to shell/config file {basename_lower!r}",
            classifier_approvable=True,
        )

    for part in components:
        if part.lower() in DANGEROUS_DIRS_LOWER:
            return PathSafetyResult(
                is_dangerous=True,
                reason=f"dangerous-target: write inside protected directory {part.lower()!r}",
                classifier_approvable=True,
            )

    return None


# ---------------------------------------------------------------------------
# Public gate
# ---------------------------------------------------------------------------


def check_path_safety(
    original_path: str,
    resolved_path: str | None = None,
) -> PathSafetyResult | None:
    """Return a dangerous-path verdict, or None when the path is safe.

    This is the bypass-immune gate — called from `check_permission()` BEFORE
    user-rule evaluation. Allow rules, permission modes, and hooks cannot
    override a non-None result.

    - Non-string or empty `original_path` -> None. Empty-path gating is the
      job of `validate_path()` in the standard pipeline; we don't second-guess
      it here.
    - Null byte in `original_path` -> always-block (classifier_approvable=False).
    - Windows evasion patterns checked on `original_path` only.
    - Dangerous file/dir patterns checked on BOTH `original_path` and
      `resolved_path` to catch symlink attacks (ln -s ~/.bashrc /tmp/safe then
      write /tmp/safe).
    - When `resolved_path` is None, compute it internally so callers may pass
      just the original path.
    """
    # Guardrail: non-string or empty — not our layer's concern.
    if not isinstance(original_path, str) or not original_path.strip():
        return None

    # Null bytes: Python os.path happily passes NULs to syscalls on some
    # platforms. Treat any NUL as an always-block evasion attempt.
    if "\x00" in original_path:
        return evasion("path-evasion: null byte in path")

    # POSIX device files — infinite output (/dev/zero, /dev/random) or
    # blocking on stdin (/dev/stdin, /dev/tty). Also catches /proc/*/fd/0-2.
    if enhanced_evasion_enabled():
        normalized_for_device = os.path.normpath(original_path)
        # POSIX spec says // at path start is implementation-defined and
        # normpath preserves it. Collapse to single / so //dev/zero matches.
        if normalized_for_device.startswith("//") and not normalized_for_device.startswith("///"):
            normalized_for_device = normalized_for_device[1:]
        if normalized_for_device in BLOCKED_DEVICE_PATHS or PROC_FD_RE.match(normalized_for_device):
            return evasion(f"path-evasion: blocked device file {normalized_for_device!r}")

    raw_components = split_components(original_path)

    # Windows evasion — original path only (resolver normalizes these away).
    # Uses raw (non-NFKC) components so evasion checks (ADS colon, 8.3,
    # trailing dots) don't false-positive on NFKC-transformed characters.
    evasion_hit = detect_evasion(original_path, raw_components)
    if evasion_hit is not None:
        return evasion_hit

    # Dangerous target on the original path — NFKC-normalized so fullwidth
    # variants of .bashrc / .git are caught.
    hit = detect_dangerous_target(nfkc_components(raw_components))
    if hit is not None:
        return hit

    # Dangerous target on the resolved path, to defend against symlink attacks.
    if resolved_path is None:
        try:
            resolved_path = os.path.realpath(os.path.abspath(os.path.expanduser(original_path)))
        except (OSError, ValueError):
            resolved_path = original_path

    if resolved_path and resolved_path != original_path:
        resolved_components = split_components(resolved_path)
        return detect_dangerous_target(nfkc_components(resolved_components))

    return None
