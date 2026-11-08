"""Dangerous removal path detection.

Pure functions that classify a path as a catastrophic removal target.
Used by the bash permission pipeline to hard-deny ``rm``/``rmdir``/
``unlink`` on system-critical paths regardless of user permission rules.
Symlink resolution is intentionally NOT performed inside the core
detector — dangerous paths like ``/tmp`` must be caught even though they
symlink to ``/private/tmp`` on macOS, and the user-typed intent ("delete
/etc") is what matters.

Threat model
============
Prevent catastrophic data loss from ``rm -rf /`` and its equivalents.
Even authorised users in an interactive context must not be able to
silently approve operations like "remove root" or "remove /etc". A
hard-deny tier exists specifically to defeat approval-fatigue attacks:
when every dangerous action is an "ask", users develop muscle memory for
clicking allow, and a single distracted click ends the session.

Why dual-check (raw + resolved)
===============================
``os.path.realpath`` canonicalises paths in ways that defeat naive
string comparisons:

* **macOS:** ``/tmp`` → ``/private/tmp``. A resolved-only check for
  ``== '/tmp'`` silently fails even though the user's intent is clear.
* **Windows:** ``/`` → ``C:\\``. A resolved-only check for ``== '/'``
  fails even though the user typed "remove root".
* **Symlink attacks:** ``ln -s /etc ~/foo && rm -rf ~/foo`` — a raw-only
  check sees ``~/foo`` (safe) and passes. A resolved-only check sees
  ``/etc`` (dangerous) and catches it.

Running both checks catches both failure modes.

Src parity
==========
Ports the following TypeScript source:

* ``src/utils/permissions/pathValidation.ts:318-367`` — the 7 shapes
* ``src/tools/PowerShellTool/pathValidation.ts:831-846`` — raw variant
* ``src/tools/BashTool/pathValidation.ts:65-108`` — the integration helper
"""

from __future__ import annotations

import logging
import os
import re

from src.sandbox.command_path_extractor import PATH_EXTRACTORS, resolve_removal_command

logger = logging.getLogger(__name__)


SLASH_COLLAPSE_RE: re.Pattern[str] = re.compile(r"[\\/]+")

WIN_DRIVE_ROOT_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:/?$")

WIN_DRIVE_CHILD_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:/[^/]+$")


def normalize_slashes(path: str) -> str:
    """Collapse runs of slashes to a single ``/``.

    Defends against Windows path-separator variation (``C:\\Windows``,
    ``C:\\\\Windows``, ``C:/Windows`` all become ``C:/Windows``) and
    double-slash injection (``//etc`` becomes ``/etc``).
    """
    return SLASH_COLLAPSE_RE.sub("/", path)


HOME: str = normalize_slashes(os.path.expanduser("~"))


def is_dangerous_removal_path(resolved_path: str) -> bool:
    """Return ``True`` if the path is a catastrophic removal target.

    Detects the seven dangerous shapes from the src reference:

    1. Wildcard alone (``*``) — removes every entry in cwd
    2. Path ending with ``/*`` (e.g. ``/home/*``)
    3. POSIX root (``/``)
    4. Windows drive root (``C:``, ``C:\\``)
    5. Home directory itself (``~`` after expansion)
    6. Direct child of POSIX root (``/etc``, ``/usr``, ``/opt``, ``/home``,
       ``/var``, ``/boot``, ``/lib``, ``/lib64``, ``/srv``, ``/mnt``,
       ``/media``, ``/dev``, ``/proc``, ``/sys``, ``/tmp`` — and any
       future addition, without enumeration)
    7. Windows drive child (``C:\\Windows``, ``C:\\Users``, …)

    Symlinks are NOT followed here. Callers should run both this check
    and the resolved path through ``os.path.realpath`` separately — see
    ``check_dangerous_removal`` for the dual-check loop.
    """
    if not resolved_path:
        return False

    forward = normalize_slashes(resolved_path)

    if forward == "*" or forward.endswith("/*"):
        return True

    normalized = forward if forward == "/" else forward.rstrip("/")

    if normalized == "/":
        return True

    if WIN_DRIVE_ROOT_RE.match(normalized):
        return True

    if normalized == HOME:
        return True

    parent = os.path.dirname(normalized)
    if parent == "/":
        return True

    if WIN_DRIVE_CHILD_RE.match(normalized):
        return True

    return False


def is_dangerous_removal_raw_path(file_path: str) -> bool:
    """Check the user-typed path before realpath canonicalisation.

    ``safeResolvePath``/``os.path.realpath`` rewrites dangerous shapes
    into forms that defeat ``is_dangerous_removal_path``:

    * Windows: ``/`` → ``C:\\`` (fails the ``== '/'`` check)
    * macOS: ``/var/...`` → ``/private/var/...`` (fails the homedir
      check when ``HOME`` lives under ``/var``)

    Checking the tilde-expanded, slash-normalised form catches dangerous
    shapes (``/``, ``~``, ``/etc``, ``/usr``) as the user typed them.
    Used in combination with the resolved-form check in
    ``check_dangerous_removal`` for full coverage.

    Edge case: when ``HOME`` is unset, ``os.path.expanduser('~')``
    returns the literal ``'~'`` — no trailing slash, no components — and
    would otherwise fall through the shape table. The explicit ``'~'``
    check below catches that deliberately.
    """
    if not file_path:
        return False
    cleaned = file_path.strip().strip("\"'")
    if not cleaned:
        return False

    if cleaned == "~":
        return True

    expanded = os.path.expanduser(cleaned)
    return is_dangerous_removal_path(normalize_slashes(expanded))


def check_dangerous_removal(
    cmd_name: str,
    args: list[str],
    cwd: str,
) -> str | None:
    """Check an ``rm``/``rmdir``/``unlink`` command for dangerous targets.

    Parameters
    ----------
    cmd_name:
        The basename of the command binary as it appears on the bash line
        (e.g. ``rm``, ``RM``, ``unlink``). Case-insensitive. Callers are
        responsible for ``os.path.basename`` stripping on absolute paths
        like ``/usr/bin/rm``.
    args:
        The argv tail — everything after the command name. Flags and
        paths intermixed; the extractor handles POSIX ``--``.
    cwd:
        Working directory to resolve relative paths against.

    Returns
    -------
    ``None``
        No dangerous path detected; caller may continue through the
        normal permission pipeline.
    ``str``
        A human-readable reason describing the offending path. Caller
        should return a hard-deny ``PermissionResult``.

    Dual check
    ----------
    Every extracted path is checked twice:

    1. Raw (post-tilde-expansion, pre-realpath) — catches user intent
       and macOS ``/tmp`` → ``/private/tmp`` bypass.
    2. Resolved (post-realpath) — catches symlink attacks like
       ``ln -s /etc ~/foo && rm -rf ~/foo``.

    A ``realpath`` failure (permission denied, path too long, non-UTF8)
    falls back to the pre-realpath absolute form so a broken path cannot
    crash the pipeline.
    """
    canonical = resolve_removal_command(cmd_name)
    if canonical is None:
        return None

    extractor = PATH_EXTRACTORS.get(canonical)
    if extractor is None:
        return None

    paths = extractor(args)
    if not paths:
        return None

    for raw_path in paths:
        if is_dangerous_removal_raw_path(raw_path):
            logger.warning(
                "Dangerous removal blocked (raw): cmd=%s path=%s",
                canonical,
                raw_path,
            )
            return f"{canonical} on critical path: {raw_path}"

        cleaned = raw_path.strip().strip("\"'")
        if not cleaned:
            continue
        expanded = os.path.expanduser(cleaned)
        absolute = expanded if os.path.isabs(expanded) else os.path.join(cwd, expanded)

        try:
            resolved = os.path.realpath(absolute)
        except (OSError, ValueError):
            resolved = absolute

        if is_dangerous_removal_path(resolved):
            logger.warning(
                "Dangerous removal blocked (resolved): cmd=%s raw=%s resolved=%s",
                canonical,
                raw_path,
                resolved,
            )
            return f"{canonical} on critical path: {raw_path} (resolves to {resolved})"

    return None
