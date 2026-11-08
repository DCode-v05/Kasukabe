"""SEPTEMBER_HOME — the single root for everything the engine owns.

One directory holds the database, config, credentials, skills, plugins,
workspaces, sidechains, tool results, caches, logs. Nothing the engine writes
lives outside it. That gives three things at once: a runtime can place one tree
on boot and capture one tree on teardown; a developer can point at one directory
to run the engine on a laptop; and there is exactly one answer to "where does
this end up?" instead of a path per subsystem.

Resolution follows codex's ``find_codex_home`` (codex-rs/utils/home-dir):

* ``SEPTEMBER_HOME`` set and a directory  -> use it, resolved absolute
* ``SEPTEMBER_HOME`` set and missing      -> FATAL, naming the path
* ``SEPTEMBER_HOME`` set and not a dir    -> FATAL, naming the path
* ``SEPTEMBER_HOME`` unset                -> ``~/.september``, created

The asymmetry is deliberate. An unset root is a fresh install and should just
work. A root that was set and does not resolve is a bad mount or a typo, and
continuing would write a user's memory somewhere nobody will look for it.
"""

from __future__ import annotations

import os
from pathlib import Path

SEPTEMBER_HOME_ENV = "SEPTEMBER_HOME"
DEFAULT_HOME_DIRNAME = ".september"

LAYOUT: dict[str, str] = {
    "config": "config",
    "credentials": "credentials",
    "skills": "skills",
    "plugins": "plugins",
    "workspaces": "workspaces",
    "sidechains": "sidechains",
    "results": "results",
    "cache": "cache",
    "tmp": "tmp",
    "log": "log",
}

DB_FILENAME = "brain.sqlite"

HOME_MODE = 0o700


def resolve_september_home(raw: str | None = None) -> Path:
    """Resolve the engine root, creating it only when it was not specified.

    Args:
        raw: An explicit root. Falls back to ``SEPTEMBER_HOME`` when None.

    Raises:
        FileNotFoundError: the root was specified but does not exist.
        NotADirectoryError: the root was specified but is not a directory.
    """
    if raw is None:
        raw = os.environ.get(SEPTEMBER_HOME_ENV)

    if raw is not None and raw.strip():
        path = Path(raw.strip()).expanduser()
        if not path.exists():
            raise FileNotFoundError(
                f"{SEPTEMBER_HOME_ENV} points to {str(path)!r}, but that path does not exist. "
                f"Create it, fix the value, or unset {SEPTEMBER_HOME_ENV} to use "
                f"~/{DEFAULT_HOME_DIRNAME}."
            )
        if not path.is_dir():
            raise NotADirectoryError(
                f"{SEPTEMBER_HOME_ENV} points to {str(path)!r}, but that path is not a directory."
            )
        return path.resolve()

    default = Path.home() / DEFAULT_HOME_DIRNAME
    default.mkdir(parents=True, exist_ok=True, mode=HOME_MODE)
    return default.resolve()


def september_paths(home: str | Path) -> dict[str, Path]:
    """Map every layout key to its absolute path under *home*.

    Includes ``db``, which is a file rather than a directory and is therefore
    never created by :func:`ensure_layout`.
    """
    root = Path(home).expanduser().resolve()
    paths = {key: root / rel for key, rel in LAYOUT.items()}
    paths["root"] = root
    paths["db"] = root / DB_FILENAME
    return paths


def ensure_layout(home: str | Path) -> Path:
    """Create every directory in the layout. Idempotent. Returns the root.

    Called once at boot so the captured tree has a stable shape whether or not
    a given subsystem has written anything yet — a runtime that snapshots this
    root should not see a different layout per engine.

    This is a convenience, not a precondition: every writer still creates its
    own parent directory, matching how codex and Claude Code create theirs at
    the point of use. Nothing breaks if this was never called.
    """
    root = Path(home).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=HOME_MODE)
    for rel in LAYOUT.values():
        (root / rel).mkdir(parents=True, exist_ok=True, mode=HOME_MODE)
    return root
