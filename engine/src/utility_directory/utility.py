"""Concurrent agent/skill/tool provisioning for threads.

Wraps ud_full.py for full definitions and tool_runtime.py for platform tool
hydration. Threads invoke utility.py directly — no coordinator roundtrip.

Caching: full definitions cached per name. Platform tools hydrated
at boot and cached. Cache invalidated via TTL (15 min).
"""

from __future__ import annotations

import logging
from typing import Any

from src.infra.memoize import TTLCache
from src.utility_directory.tool_runtime import ToolRegistry

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 900


class Utility:
    """Concurrent provisioning of agents, skills, and platform tools.

    Thread-safe — multiple threads can request definitions simultaneously.
    Each thread gets its own copies; the cache is shared read-only state.
    """

    def __init__(
        self,
        ud_full: Any | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.ud = ud_full
        self.platform_tools = tool_registry or ToolRegistry()
        self.cache = TTLCache(default_ttl=CACHE_TTL_SECONDS)

    def get_cached(self, key: str) -> Any | None:
        return self.cache.get(key)

    def set_cached(self, key: str, data: Any) -> None:
        self.cache.set(key, data)

    def invalidate(self, prefix: str = "") -> None:
        self.cache.invalidate(prefix)


    async def get_agent(self, name: str, version: str) -> dict[str, Any] | None:
        """Get full agent definition with user overlays. Cached per name + version."""
        cache_key = f"agent:{name}:{version}"
        cached = self.get_cached(cache_key)
        if cached is not None:
            return cached

        if self.ud is None:
            return None

        result = await self.ud.get_agent(name, version)
        if result is not None:
            self.set_cached(cache_key, result)
        return result


    async def get_skill(self, name: str, version: str) -> dict[str, Any] | None:
        """Get full skill definition with user overlays. Cached per name + version."""
        cache_key = f"skill:{name}:{version}"
        cached = self.get_cached(cache_key)
        if cached is not None:
            return cached

        if self.ud is None:
            return None

        result = await self.ud.get_skill(name, version)
        if result is not None:
            self.set_cached(cache_key, result)
        return result

    def skill_execute(self, skill_data: dict[str, Any]) -> dict[str, Any]:
        """Single entry point for all skill execution.

        Takes a skill definition (from catalog match), returns a dict with:
          - "prompt": str or None — system prompt text (from prompt.md + gotchas.md + description)
          - "script_result": str or None — result of running the skill's script
          - "references": list[str] — reference file names available on demand

        Handles all skill types: prompt-only, script-only, both, neither.
        Scripts run in-process (trusted — user brings all skills).
        """
        from pathlib import Path
        import types

        result: dict[str, Any] = {"prompt": None, "script_result": None, "references": []}
        skill_dir_str = skill_data.get("_skill_dir", "")

        if not skill_dir_str:
            desc = skill_data.get("description", "")
            if desc:
                result["prompt"] = desc
            return result

        skill_dir = Path(skill_dir_str)

        parts: list[str] = []
        parts.append(f"### {skill_data.get('name', 'unknown')}")
        parts.append(skill_data.get("description", ""))

        gotchas_file = skill_dir / "gotchas.md"
        if gotchas_file.exists():
            parts.append(f"Known issues:\n{gotchas_file.read_text(encoding='utf-8')[:1000]}")

        prompt_file = skill_dir / "prompt.md"
        if prompt_file.exists():
            parts.append(f"Approach:\n{prompt_file.read_text(encoding='utf-8')[:2000]}")

        if parts:
            result["prompt"] = "\n".join(parts)

        refs_dir = skill_dir / "references"
        if refs_dir.exists() and refs_dir.is_dir():
            result["references"] = [f.name for f in refs_dir.iterdir() if f.is_file()]

        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists() and scripts_dir.is_dir():
            for script_file in sorted(scripts_dir.iterdir()):
                if not script_file.is_file() or not script_file.suffix == ".py":
                    continue
                try:
                    source = script_file.read_text(encoding="utf-8")
                    module = types.ModuleType(f"skill_{skill_data.get('name', 'unknown')}")
                    exec(source, module.__dict__)
                    run_fn = getattr(module, "run", None)
                    if run_fn and callable(run_fn):
                        result["_run_fn"] = run_fn
                        result["_script_loaded"] = True
                except Exception as e:
                    logger.warning(
                        "Skill '%s' script %s failed to load: %s",
                        skill_data.get("name", "?"),
                        script_file.name,
                        e,
                    )

        return result


    async def get_tool(self, name: str, version: str) -> dict[str, Any] | None:
        """Get full tool definition. Cached per name + version."""
        cache_key = f"tool:{name}:{version}"
        cached = self.get_cached(cache_key)
        if cached is not None:
            return cached

        if self.ud is None:
            return None

        result = await self.ud.get_tool(name, version)
        if result is not None:
            self.set_cached(cache_key, result)
        return result


    def cache_stats(self) -> dict[str, Any]:
        return {
            "total_entries": len(self.cache),
            "active_entries": sum(1 for e in self.cache.entries.values() if not e.expired),
            "platform_tools": self.platform_tools.tool_count
            if hasattr(self.platform_tools, "tool_count")
            else len(self.platform_tools.tools),
        }
