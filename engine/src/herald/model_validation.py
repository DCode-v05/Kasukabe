"""Boot-time model validation against the citadel-synced catalog.

Config model fields are pure overrides over the herald defaults block
(src/herald/defaults.py); neither is validated at Settings.load time because
the catalog only exists after the first citadel sync. This module runs right
after ``catalog_sync.initialize`` in bootstrap and checks every RESOLVED
model id (override-else-default) against the synced catalog:

- sync succeeded  -> an unknown id is FATAL (boot aborts with the report;
  a model typo must never surface as per-request citadel 4xxs)
- sync degraded   -> everything is a warning; an empty catalog cannot
  distinguish "missing" from "unsyncable", and catalog_sync's accelerated
  retry re-runs this check warn-only when the first late sync lands.

Embedding nuance: the EMBEDDING role must resolve to a catalog row with
tier "embedding". If the sync succeeded but citadel published ZERO
embedding-tier rows, the finding downgrades to a warning - a citadel that
only lists chat models must not brick boot.
"""

from __future__ import annotations

import logging
from typing import Any

from src.herald import catalog_sync
from src.herald.defaults import ROLE_SETTINGS_FIELD, ModelRole, resolve_model

# Tiers that are not chat models. Inlined when herald/router.py was removed
# for the Kasukabe build - this was its only surviving consumer.
NON_CHAT_TIERS = frozenset({"embedding", "rerank", "reranker", "moderation"})

logger = logging.getLogger(__name__)

# Chat roles checked unconditionally; FALLBACK only when resolved non-empty.
CHAT_ROLES = (
    ModelRole.PRIMARY,
    ModelRole.PLANNER,
    ModelRole.LIGHT,
    ModelRole.REASONING,
    ModelRole.DISPATCH,
    ModelRole.DISPATCH_ALTERNATIVE,
    ModelRole.LC,
)


def describe_source(role: ModelRole, settings: Any) -> str:
    """Whether the resolved id came from an env override or the default -
    tells the operator which knob to touch."""
    field_name = ROLE_SETTINGS_FIELD.get(role)
    if field_name and settings is not None and getattr(settings, field_name, ""):
        return f"env override {field_name.upper()}"
    return "herald default (src/herald/defaults.py)"


def validate_resolved_models(settings: Any, synced: bool) -> tuple[list[str], list[str]]:
    """Check every resolved model id against the catalog. Never raises.

    Returns ``(fatal, warnings)``. ``fatal`` is non-empty only when
    ``synced`` is True; with a degraded catalog every finding is a warning.
    """
    fatal: list[str] = []
    warnings: list[str] = []
    overlay = catalog_sync.get_catalog_overlay()
    known = sorted(
        m.id for m in overlay.values() if (m.tier or "").lower() not in NON_CHAT_TIERS
    )
    embedding_rows = [m for m in overlay.values() if (m.tier or "").lower() == "embedding"]

    def report(message: str, downgrade: bool = False) -> None:
        if synced and not downgrade:
            fatal.append(message)
        else:
            warnings.append(message)

    if not synced and not overlay:
        # One consolidated warning beats nine identical unverifiable ones.
        warnings.append(
            "catalog empty (citadel sync degraded) - configured model ids are "
            "unverified until the first successful sync; accelerated retry is "
            "active and re-checks then"
        )
        return fatal, warnings

    checked: set[tuple[str, str]] = set()
    for role in CHAT_ROLES:
        model_id = resolve_model(role, settings)
        key = (model_id, "chat")
        if key in checked:
            continue
        checked.add(key)
        entry = catalog_sync.lookup(model_id)
        if entry is None:
            report(
                f"{role.value} model {model_id!r} ({describe_source(role, settings)}) "
                f"is not in the citadel catalog; known chat models: "
                f"{', '.join(known) or '(none)'}"
            )
        elif (entry.tier or "").lower() in NON_CHAT_TIERS:
            report(
                f"{role.value} model {model_id!r} ({describe_source(role, settings)}) "
                f"has non-chat tier {entry.tier!r} and cannot serve chat traffic"
            )

    fallback_id = resolve_model(ModelRole.FALLBACK, settings)
    if fallback_id:
        entry = catalog_sync.lookup(fallback_id)
        if entry is None:
            report(
                f"fallback model {fallback_id!r} "
                f"({describe_source(ModelRole.FALLBACK, settings)}) is not in the "
                f"citadel catalog; known chat models: {', '.join(known) or '(none)'}"
            )

    embedding_id = resolve_model(ModelRole.EMBEDDING, settings)
    entry = catalog_sync.lookup(embedding_id)
    no_embedding_rows = synced and not embedding_rows
    if entry is None:
        report(
            f"embedding model {embedding_id!r} "
            f"({describe_source(ModelRole.EMBEDDING, settings)}) is not in the "
            f"citadel catalog"
            + (
                " (citadel published no embedding-tier rows at all - warning only)"
                if no_embedding_rows
                else ""
            ),
            downgrade=no_embedding_rows,
        )
    elif (entry.tier or "").lower() != "embedding":
        report(
            f"embedding model {embedding_id!r} "
            f"({describe_source(ModelRole.EMBEDDING, settings)}) has tier "
            f"{entry.tier!r}, expected 'embedding'"
        )

    return fatal, warnings


def log_findings(fatal: list[str], warnings: list[str]) -> None:
    for w in warnings:
        logger.warning("model validation: %s", w)
    for e in fatal:
        logger.error("model validation: %s", e)
