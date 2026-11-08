"""hook_settings — Load and register hooks from settings JSON files.

Hooks can be configured in four persistent settings files (in priority order,
highest first) and two ephemeral in-memory sources:

1. User settings    — ``~/.config/intelligence-engine/settings.json``
2. Project settings — ``.intelligence-engine/settings.json``  (cwd-relative)
3. Local settings   — ``.intelligence-engine/settings.local.json``
4. Plugin hooks     — ``~/.config/intelligence-engine/plugins/*/hooks.json``
5. Session hooks    — in-memory, added via ``HookSettingsManager.register_session_hook``
6. Built-in hooks   — registered at engine startup for internal behaviours

Priority rule: **user > project > local > plugin > session > built-in**.
When multiple hooks target the same event/matcher combination, all of them
run (hooks are additive).  The priority determines which *source* is
consulted first when loading — it does NOT deduplicate; every entry in
every source is registered.

Settings file format (``hooks`` key inside the JSON):

```json
{
  "hooks": [
    {
      "hooks": [
        { "type": "command", "command": "path/to/script.sh" }
      ],
      "matcher": "Bash",
      "event": "PreToolUse"
    }
  ]
}
```

Each entry in the top-level ``hooks`` array is a *HookMatcher* block:
- ``event``   — HookEvent value string (e.g. ``"PreToolUse"``)
- ``matcher`` — tool name pattern (default ``"*"``)
- ``hooks``   — list of hook command objects

Hook command objects have a ``type`` field:
- ``command`` — ``{ "type": "command", "command": "...", "shell": "bash", "timeout": 600 }``
- ``http``    — ``{ "type": "http", "url": "https://...", "timeout": 30 }``

Session hooks can also be ``function`` type (Python callables) registered
directly via ``register_function_hook``.

Reference: ``src/utils/hooks/hooksSettings.ts`` (priority ordering and file
discovery), ``src/schemas/hooks.ts`` (settings-file schema).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.engine_core.hooks import HookEvent, matches_tool
from src.engine_core.pre_tool_hooks import (
    CallablePreToolUseHook,
    CommandPreToolUseHook,
    PreToolUseHookInput,
    PreToolUseHookResult,
    PreToolUseHookRegistry,
)
from src.engine_core.post_tool_hooks import (
    CallablePostToolUseHook,
    CommandPostToolUseHook,
    PostToolHookContext,
    PostToolUseHookResult,
    PostToolUseHookRegistry,
)
from src.engine_core.stop_hooks import (
    CallableStopHook,
    CommandStopHook,
    StopHookRegistry,
)
from src.engine_core.generic_hooks import (
    CallableGenericHook,
    CommandGenericHook,
    HttpGenericHook,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.engine_core.lifecycle_hooks import LifecycleHookBundle

logger = logging.getLogger(__name__)


CONFIG_DIR_ENV = "INTELLIGENCE_ENGINE_CONFIG_DIR"
CONFIG_SUBDIR = ".intelligence-engine"
SETTINGS_FILENAME = "settings.json"
LOCAL_SETTINGS_FILENAME = "settings.local.json"
PLUGINS_SUBDIR = "plugins"


def user_settings_path() -> Path:
    """Return path to user-level settings (highest priority)."""
    config_dir = os.environ.get(CONFIG_DIR_ENV)
    if config_dir:
        return Path(config_dir) / SETTINGS_FILENAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "intelligence-engine" / SETTINGS_FILENAME
    return Path.home() / ".config" / "intelligence-engine" / SETTINGS_FILENAME


def project_settings_path(cwd: str | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    return base / CONFIG_SUBDIR / SETTINGS_FILENAME


def local_settings_path(cwd: str | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    return base / CONFIG_SUBDIR / LOCAL_SETTINGS_FILENAME


def plugin_hook_paths() -> list[Path]:
    """Return all plugin hooks.json paths in the user plugins directory."""
    config_dir = os.environ.get(CONFIG_DIR_ENV)
    if config_dir:
        plugins_root = Path(config_dir) / PLUGINS_SUBDIR
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            plugins_root = Path(xdg) / "intelligence-engine" / PLUGINS_SUBDIR
        else:
            plugins_root = Path.home() / ".config" / "intelligence-engine" / PLUGINS_SUBDIR
    if not plugins_root.is_dir():
        return []
    return sorted(plugins_root.glob("*/hooks.json"))


def plugin_env_overrides(plugin_hooks_json: Path) -> dict[str, str]:
    """Return PLUGIN_ROOT, PLUGIN_DATA, PLUGIN_OPTION_* env vars for a plugin.

    Sidecar convention: ``<plugin_root>/plugin.json`` (optional) may carry
    an ``options`` object; each key/value pair becomes ``PLUGIN_OPTION_<KEY>``
    (value coerced to string). ``<plugin_root>/data/`` if present becomes
    ``PLUGIN_DATA``. ``PLUGIN_ROOT`` is always the plugin directory. All
    three are handed to the subprocess via CommandGenericHook's
    ``env_overrides`` so hook scripts can locate plugin assets.
    """
    plugin_root = plugin_hooks_json.parent
    env: dict[str, str] = {"PLUGIN_ROOT": str(plugin_root)}
    data_dir = plugin_root / "data"
    if data_dir.is_dir():
        env["PLUGIN_DATA"] = str(data_dir)
    plugin_meta = plugin_root / "plugin.json"
    if plugin_meta.is_file():
        try:
            with open(plugin_meta, "r", encoding="utf-8") as f:
                meta = json.load(f)
            options = meta.get("options") if isinstance(meta, dict) else None
            if isinstance(options, dict):
                for key, value in options.items():
                    if not isinstance(key, str):
                        continue
                    env[f"PLUGIN_OPTION_{key.upper()}"] = str(value)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("plugin.json options load failed for %s: %s", plugin_root, exc)
    return env


def load_settings_file(path: Path) -> dict[str, Any]:
    """Read a settings JSON file. Returns empty dict on any error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Could not load settings from %s: %s", path, exc)
        return {}


def extract_hook_matchers(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the ``hooks`` array from a parsed settings dict."""
    raw = settings.get("hooks")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def build_shell_command(command_str: str, shell: str) -> list[str]:
    """Return an exec-style command list for a shell command string."""
    shell = shell or "bash"
    return [shell, "-c", command_str]


def command_hook_for_event(
    event: HookEvent,
    cmd_list: list[str],
    timeout_s: float,
    env_overrides: dict[str, str] | None = None,
) -> Any:
    """Instantiate the right CommandXxxHook for the given event type.

    Returns a typed Command hook for the core tool-lifecycle / Stop events
    and a generic ``CommandGenericHook`` for every other HookEvent.
    ``env_overrides`` (PLUGIN_ROOT, PLUGIN_DATA, PLUGIN_OPTION_*) are
    layered on top of ``os.environ`` before subprocess spawn.
    """
    if event == HookEvent.PRE_TOOL_USE:
        return CommandPreToolUseHook(
            cmd_list,
            timeout_s=timeout_s,
            env_overrides=env_overrides,
        )
    if event == HookEvent.POST_TOOL_USE:
        return CommandPostToolUseHook(
            cmd_list,
            timeout_s=timeout_s,
            env_overrides=env_overrides,
        )
    if event == HookEvent.STOP:
        return CommandStopHook(
            cmd_list,
            timeout_s=timeout_s,
            env_overrides=env_overrides,
        )
    return CommandGenericHook(
        cmd_list,
        timeout_s=timeout_s,
        event_name=event.value,
        env_overrides=env_overrides,
    )


@dataclass
class SessionHookEntry:
    """An in-memory session hook registered via ``register_function_hook``."""

    event: HookEvent
    matcher: str
    name: str
    hook: Any


class HookSettingsManager:
    """Loads hooks from all sources and registers them into the three
    core registries (pre-tool, post-tool, stop).

    Usage::

        manager = HookSettingsManager(
            pre_tool_registry=thread.pre_tool_hooks,
            post_tool_registry=thread.post_tool_hooks,
            stop_registry=thread.stop_hooks,
            cwd="/project",
        )
        manager.load()  # loads from settings files + session hooks

    After ``load()``, all enabled hooks sourced from JSON settings files
    and in-memory session hooks are registered in the appropriate registries.

    Session hooks (registered programmatically) are loaded last so they
    can supplement or override settings-file hooks if needed.  Because
    registry names include a source prefix (``"user:"``, ``"session:"``
    etc.), hooks from different sources coexist without clobbering each other.
    """

    def __init__(
        self,
        pre_tool_registry: PreToolUseHookRegistry,
        post_tool_registry: PostToolUseHookRegistry,
        stop_registry: StopHookRegistry,
        cwd: str | None = None,
        enabled: bool = True,
        lifecycle_bundle: "LifecycleHookBundle | None" = None,
    ) -> None:
        self.pre = pre_tool_registry
        self.post = post_tool_registry
        self.stop = stop_registry
        self.cwd = cwd
        self.enabled = enabled
        self.lifecycle_bundle = lifecycle_bundle
        self.session_hooks: list[SessionHookEntry] = []


    def load(self) -> None:
        """Discover and register all hooks from settings files + session."""
        if not self.enabled:
            return
        sources: list[tuple[str, dict[str, Any], dict[str, str]]] = [
            ("user", load_settings_file(user_settings_path()), {}),
            ("project", load_settings_file(project_settings_path(self.cwd)), {}),
            ("local", load_settings_file(local_settings_path(self.cwd)), {}),
        ]
        for plugin_path in plugin_hook_paths():
            plugin_name = plugin_path.parent.name
            plugin_env = plugin_env_overrides(plugin_path)
            sources.append((f"plugin:{plugin_name}", load_settings_file(plugin_path), plugin_env))

        for source_name, settings, plugin_env in sources:
            for matcher_block in extract_hook_matchers(settings):
                self.register_from_block(source_name, matcher_block, plugin_env)

        for entry in self.session_hooks:
            self.register_session_entry(entry)

    def register_session_hook(
        self,
        event: HookEvent,
        matcher: str,
        name: str,
        command: list[str],
        timeout_s: float = 60.0,
    ) -> None:
        """Register a command-based hook for the current session only.

        ``name`` must be unique within the event + matcher combination.
        Re-registering the same name replaces the prior hook.
        """
        hook = command_hook_for_event(event, command, timeout_s)
        if hook is None:
            return
        self.session_hooks.append(
            SessionHookEntry(
                event=event,
                matcher=matcher,
                name=name,
                hook=hook,
            )
        )

    def register_function_hook(
        self,
        event: HookEvent,
        matcher: str,
        name: str,
        fn: Callable[..., Awaitable[Any]],
        timeout_s: float = 60.0,
    ) -> None:
        """Register an async Python callable as a hook for this session.

        The callable's signature must match the event type:
        - PreToolUse:  ``async (PreToolUseHookInput) -> PreToolUseHookResult``
        - PostToolUse: ``async (PostToolHookContext) -> PostToolUseHookResult``
        - Stop:        ``async (StopHookInput) -> StopHookResult``

        All other events (SubagentStart/SubagentStop, PreCompact/PostCompact,
        UserPromptSubmit, TaskCreated/TaskCompleted, and the rest) are
        registered into the lifecycle bundle's per-event registry when
        ``load()`` runs; the matcher is informational for them since they
        carry no tool_name.  If no lifecycle bundle was supplied, they are
        dropped with a debug log.
        """
        hook: Any
        if event == HookEvent.PRE_TOOL_USE:
            hook = make_filtered_pre_hook(fn, matcher, timeout_s)
        elif event == HookEvent.POST_TOOL_USE:
            hook = make_filtered_post_hook(fn, matcher, timeout_s)
        elif event == HookEvent.STOP:
            hook = CallableStopHook(fn, timeout_s=timeout_s)
        else:
            hook = CallableGenericHook(fn, timeout_s=timeout_s)
        self.session_hooks.append(
            SessionHookEntry(
                event=event,
                matcher=matcher,
                name=name,
                hook=hook,
            )
        )


    def register_from_block(
        self,
        source_name: str,
        block: dict[str, Any],
        plugin_env: dict[str, str] | None = None,
    ) -> None:
        """Parse a hook-matcher block from a settings file and register hooks.

        ``plugin_env`` is the resolved PLUGIN_ROOT/PLUGIN_DATA/PLUGIN_OPTION_*
        environment to inject into command-hook subprocesses. Empty for
        non-plugin sources.
        """
        event_str = block.get("event")
        if not event_str:
            return
        try:
            event = HookEvent(event_str)
        except ValueError:
            logger.debug(
                "hook_settings: unknown event '%s' in source '%s' — skipped",
                event_str,
                source_name,
            )
            return

        matcher_str = str(block.get("matcher", "*") or "*")
        hook_defs = block.get("hooks", [])
        if not isinstance(hook_defs, list):
            return

        for idx, hook_def in enumerate(hook_defs):
            if not isinstance(hook_def, dict):
                continue
            hook_type = str(hook_def.get("type", ""))
            name = f"{source_name}:{event.value}:{matcher_str}:{idx}"

            if hook_type == "command":
                cmd_str = str(hook_def.get("command", "") or "")
                if not cmd_str:
                    continue
                shell = str(hook_def.get("shell", "bash") or "bash")
                timeout_s = float(hook_def.get("timeout", 60) or 60)
                cmd_list = build_shell_command(cmd_str, shell)
                hook_obj = command_hook_for_event(
                    event,
                    cmd_list,
                    timeout_s,
                    env_overrides=dict(plugin_env) if plugin_env else None,
                )
                self.register_hook_obj(event, matcher_str, name, hook_obj)

            elif hook_type == "http":
                from src.engine_core.hook_executors import (
                    HttpPreToolUseHook,
                    HttpPostToolUseHook,
                    HttpStopHook,
                )

                url = str(hook_def.get("url", "") or "")
                if not url:
                    continue
                timeout_s = float(hook_def.get("timeout", 30) or 30)
                if event == HookEvent.PRE_TOOL_USE:
                    http_hook: Any = HttpPreToolUseHook(url, timeout_s=timeout_s)
                elif event == HookEvent.POST_TOOL_USE:
                    http_hook = HttpPostToolUseHook(url, timeout_s=timeout_s)
                elif event == HookEvent.STOP:
                    http_hook = HttpStopHook(url, timeout_s=timeout_s)
                else:
                    http_hook = HttpGenericHook(
                        url,
                        timeout_s=timeout_s,
                        event_name=event.value,
                    )
                self.register_hook_obj(event, matcher_str, name, http_hook)

    def register_session_entry(self, entry: SessionHookEntry) -> None:
        reg_name = f"session:{entry.name}"
        self.register_hook_obj(entry.event, entry.matcher, reg_name, entry.hook)

    def register_hook_obj(
        self,
        event: HookEvent,
        matcher: str,
        name: str,
        hook_obj: Any,
    ) -> None:
        """Register a hook object into the correct registry.

        For tool-lifecycle events the hook is wrapped in a matcher filter
        so it only fires for matching tool names.  For Stop and the other
        generic lifecycle events the matcher is informational only — they
        never carry a tool_name.
        """
        if event == HookEvent.PRE_TOOL_USE:
            pre_wrapped = MatchedPreToolHook(hook_obj, matcher)
            self.pre.register(name, pre_wrapped)
        elif event == HookEvent.POST_TOOL_USE:
            post_wrapped = MatchedPostToolHook(hook_obj, matcher)
            self.post.register(name, post_wrapped)
        elif event == HookEvent.STOP:
            self.stop.register(name, hook_obj)
        elif self.lifecycle_bundle is not None:
            self.lifecycle_bundle.registry(event).register(name, hook_obj)
        else:
            logger.debug(
                "hook_settings: event %s skipped — no lifecycle_bundle",
                event.value,
            )


class MatchedPreToolHook:
    """Wraps a PreToolUseHook and evaluates the matcher pattern.

    If the tool name does not match, returns an ``inherit`` no-op result.
    This lets a single registry hold hooks with different matchers without
    requiring per-hook registries.
    """

    def __init__(self, inner: Any, matcher: str) -> None:
        self.inner = inner
        self.matcher = matcher

    async def execute(self, inp: PreToolUseHookInput) -> PreToolUseHookResult:
        if not matches_tool(self.matcher, inp.tool_name, inp.tool_input):
            return PreToolUseHookResult()
        return await self.inner.execute(inp)


class MatchedPostToolHook:
    """Wraps a PostToolUseHook and evaluates the matcher pattern."""

    def __init__(self, inner: Any, matcher: str) -> None:
        self.inner = inner
        self.matcher = matcher

    async def execute(self, ctx: PostToolHookContext) -> PostToolUseHookResult:
        if not matches_tool(self.matcher, ctx.tool_name, ctx.tool_input):
            return PostToolUseHookResult()
        return await self.inner.execute(ctx)


def make_filtered_pre_hook(
    fn: Callable[[PreToolUseHookInput], Awaitable[PreToolUseHookResult]],
    matcher: str,
    timeout_s: float,
) -> MatchedPreToolHook:
    return MatchedPreToolHook(
        CallablePreToolUseHook(fn, timeout_s=timeout_s),
        matcher,
    )


def make_filtered_post_hook(
    fn: Callable[[PostToolHookContext], Awaitable[PostToolUseHookResult]],
    matcher: str,
    timeout_s: float,
) -> MatchedPostToolHook:
    return MatchedPostToolHook(
        CallablePostToolUseHook(fn, timeout_s=timeout_s),
        matcher,
    )
