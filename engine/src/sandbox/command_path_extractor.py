"""Extract target paths from command argv lists.

Per-command argument parsers that understand each command's flag layout
and return the list of positional path arguments. Handles POSIX ``--``
end-of-options correctly to defeat attack payloads like::

    rm -- -/../.claude/settings.local.json

Initial scope is the removal commands (``rm``, ``rmdir``, ``unlink``).
Adding a new command is additive: implement an extractor, register it in
``PATH_EXTRACTORS`` and ``REMOVAL_ALIASES`` (if it's a removal alias).
Future follow-ups will extend the dispatcher to ``mv``, ``cp``, ``dd``,
``find -delete`` and friends without touching existing callers.
"""

from __future__ import annotations

from typing import Callable


def filter_positional(args: list[str]) -> list[str]:
    """Extract positional args, respecting POSIX ``--`` end-of-options.

    Most POSIX utilities stop parsing options at ``--`` and treat every
    subsequent argument as positional, even if it starts with ``-``. A
    naive ``not arg.startswith("-")`` filter drops those, which causes
    path validation to be silently skipped for attack payloads like::

        rm -- -/../.claude/settings.local.json

    Here ``-/../.claude/settings.local.json`` starts with ``-`` so the
    naive filter drops it, validation sees zero paths, returns passthrough
    and the file is deleted without any prompt. This implementation keeps
    an explicit ``after_double_dash`` flag and preserves everything after
    the first ``--`` regardless of leading dash.
    """
    result: list[str] = []
    after_double_dash = False
    for arg in args:
        if after_double_dash:
            result.append(arg)
            continue
        if arg == "--":
            after_double_dash = True
            continue
        if not arg:
            continue
        if arg.startswith("-"):
            continue
        result.append(arg)
    return result


# All three removal commands (``rm``, ``rmdir``, ``unlink``) take positional
# path arguments after flag filtering, so one extractor handles all of them.
# Split per-command functions would reintroduce boilerplate without
# behavioural divergence. If a future removal command diverges (e.g. needs
# destination-vs-source extraction like ``mv``), add a dedicated function
# then — extensibility stays a one-line edit.
extract_removal_paths = filter_positional


# Canonical command name → extractor function.
#
# Callers look up the canonical name via ``resolve_removal_command`` first
# and then index this dict. Keeping the two tables separate lets alias
# resolution stay a pure string transform that a future AST analyser can
# reason about without caring about the extractor signatures.
PATH_EXTRACTORS: dict[str, Callable[[list[str]], list[str]]] = {
    "rm": extract_removal_paths,
    "rmdir": extract_removal_paths,
    "unlink": extract_removal_paths,
}


# Removal-command alias table. ``rm``/``rmdir``/``unlink`` are their own
# canonicals today; the table exists so future Windows support can collapse
# ``del``/``ri``/``rd``/``erase`` into ``remove-item`` with a one-line edit
# and no branching elsewhere.
REMOVAL_ALIASES: dict[str, str] = {
    "rm": "rm",
    "rmdir": "rmdir",
    "unlink": "unlink",
    # Future: "del": "remove-item", "ri": "remove-item", "rd": "remove-item",
    # "erase": "remove-item".
}


def resolve_removal_command(cmd_name: str) -> str | None:
    """Return the canonical removal command name, or ``None`` if not one.

    Case-insensitive — ``RM`` and ``rm`` resolve to the same canonical.
    Caller is responsible for ``os.path.basename`` stripping on absolute
    paths like ``/usr/bin/rm`` before calling.
    """
    if not cmd_name:
        return None
    return REMOVAL_ALIASES.get(cmd_name.lower())
