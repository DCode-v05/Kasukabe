"""LLM-powered "while you were away" recap.

Parallels ``src/services/awaySummary.ts`` from the reference codebase but
adapted to the engine's tool-native model.  The reference version summarizes
a chat transcript; the engine has no transcript — it has structured session
stats.  So the recap prompt feeds the LLM:

    - (optional) the PRE-AWAY user message, so the LLM can anchor the recap
      in what the user was trying to do BEFORE leaving.  Omit this field
      when you only have the reconnect-triggering message — that's current
      intent, not pre-away context, and feeding it inverts the recap.
    - the structured SessionStats (tool calls, errors, engine active
      seconds, idle duration) that happened while the user was away.

The LLM turns that into 1-3 sentences starting with the task context
(not implementation minutiae) and the concrete next step.

Fallback: if the model call fails for any reason the caller should render
``ReconnectSummary.format_recap()`` instead — that's the deterministic
zero-cost path and always works.

Usage::

    from src.herald import create_llm
    from src.herald.catalog import ModelTier, resolve_model_for_tier
    from src.services.away_summary import generate_away_summary

    llm = create_llm(settings, model_id=resolve_model_for_tier(ModelTier.LIGHT, settings).id)
    recap = await generate_away_summary(llm, summary, last_user_message="...")
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.utils.activity_manager import ReconnectSummary

if TYPE_CHECKING:
    from src.herald import LLM

logger = logging.getLogger(__name__)

# Hard ceiling on recap length.  The reference impl asks for 1-3 sentences;
# we set a generous token budget so the model has breathing room without
# drifting into paragraphs.
MAX_RECAP_TOKENS = 180

# Soft timeout: a recap is cosmetic — if the LLM is slow, fall back.
DEFAULT_TIMEOUT_S = 10.0


def build_prompt(summary: ReconnectSummary, last_user_message: str | None) -> str:
    stats = summary.stats
    bullets = [
        f"- previous state: {summary.previous_state.value}",
        f"- idle seconds: {summary.idle_seconds:.0f}",
        f"- tool calls while away: {stats.tool_calls}",
        f"- errors while away: {stats.errors}",
        f"- engine compute seconds: {stats.engine_active_seconds:.1f}",
        f"- user active seconds: {stats.user_active_seconds:.1f}",
    ]
    if last_user_message:
        bullets.append(f"- last user message: {last_user_message[:300]}")

    bullet_block = "\n".join(bullets)
    return (
        "The user stepped away from an autonomous intelligence engine and is "
        "coming back. Below are structured facts about what happened while "
        "they were away.\n\n"
        f"{bullet_block}\n\n"
        "Write exactly 1-3 short sentences. Start by stating the high-level "
        "task or context (not implementation minutiae). Next: the concrete "
        "next step the user should look at. Skip status reports and event "
        "dumps. Do not pad with filler."
    )


async def generate_away_summary(
    llm: "LLM",
    summary: ReconnectSummary,
    *,
    last_user_message: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str | None:
    """Generate a 1-3 sentence recap via the LLM.

    Returns ``None`` on empty input, timeout, or any provider error —
    callers must treat this as best-effort and fall back to
    ``ReconnectSummary.format_recap()`` if ``None`` comes back.
    """
    from src.herald.types import Message

    try:
        prompt = build_prompt(summary, last_user_message)
        message = Message(role="user", content=prompt)

        # generate_with_tools enforces MAX_RECAP_TOKENS server-side — the
        # recap is 1–3 sentences, a runaway model output would waste money.
        # tools=[] so no tool loop; we just want bounded text.
        response = await asyncio.wait_for(
            llm.generate_with_tools(
                messages=[message],
                tools=[],
                max_output_tokens=MAX_RECAP_TOKENS,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.info("away_summary: LLM timed out after %.1fs", timeout_s)
        return None
    except Exception:
        logger.debug("away_summary: LLM call failed", exc_info=True)
        return None

    text = (response.text or "").strip() if response is not None else ""
    return text or None
