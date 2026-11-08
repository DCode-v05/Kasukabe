"""The town's rulebook: `village_rules.json` plus the loader that checks it."""

from world.rules.loader import RULES_PATH, RuleError, Rules, load_rules

__all__ = ["RULES_PATH", "RuleError", "Rules", "load_rules"]
