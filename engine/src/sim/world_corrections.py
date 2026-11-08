"""The world as corrector: a refusal becomes a learning signal.

ROADMAP track T4, recorded in the assessment as not started - *"No
`SignalStore` writes from the seam. `tool_refusals` is recorded and goes
nowhere."* That was exactly true, and it is a short journey to fix,
because every refusal the world issues arrives at one place:
`WorldClient.call_tool` turns a 4xx into the string "Error: ..." and hands
it to the agent loop as text. The resident reads it once, in that turn,
and nothing in the system ever reads it again.

What it costs is the whole of the compounding the project is named for.
The evaluation spec's C5 - "correction signals emitted by the world and
consumed by the pipeline" - cannot exceed zero while this is missing, so
a town that refuses the same resident the same way every morning teaches
it nothing between mornings. The learning centre has had a `correction`
signal type waiting for a caller since it was written.

WHAT THIS IS NOT. It is not a second refusal mechanism, and it changes
nothing the resident sees this turn: the refusal text goes back exactly as
before. It is the durable half - the same sentence, written where the
learning pipeline can consolidate it during sleep.

SOURCE IS `user`, AND THIS PARAGRAPH USED TO SAY THE OPPOSITE.

The original reasoning was that a world refusal is the town's physics
declining a move, not a person giving an instruction, so filing it as
`user` would put the two in one bucket. That is a tidy distinction and it
made the entire mechanism inert.

`learning_centre/pipeline.py`'s `step11_skill_evolution` reads:

    SELECT content, thread_id FROM learning_signals
    WHERE type = 'correction' AND source = 'user'

Run 23 wrote 70 corrections. `step11` read none of them. The signals were
stored, counted, and reported, and not one of them ever drove a skill
gotcha or a fork - which is C5 of the evaluation spec and the whole of the
compounding this module was written to enable. `is_trivial_task` filters
the same way, on `MEANINGFUL_USER_TYPES` AND `source == "user"`.

The pipeline is frozen under ROADMAP Law 1 and cannot be changed; this
module is the seam and can. So the question is only which of
`SIGNAL_SOURCES` = {user, engine, wm_flush} is the right label, and the
vocabulary has no third option meaning "the world said so". In it, `user`
does not mean "a human typed this" - there is no human in a Kasukabe run,
and the frozen post-turn hook in `engine_core/thread.py` already stamps
`source="user"` on the event signals it writes about the agent's own
turn. `user` means "arising from the conversation, worth learning from",
and `engine` means "housekeeping". A world refusal is the first of those.

Nothing is lost by the change: `subject` still carries the tool name and
the content still opens with "The world refused", so a reader can always
tell a refusal from anything else. What WAS lost, for every run so far,
was the correction reaching the step that exists to consume it.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from src.learning_centre.signals import LearningSignal

logger = logging.getLogger("kasukabe.sim.world_corrections")

REFUSAL_PREFIX = "Error:"


class SignalWriter(Protocol):
    async def write(self, signal: LearningSignal) -> int: ...


def is_refusal(result: str | None) -> bool:
    """Did the world decline this call?"""
    return bool(result) and str(result).lstrip().startswith(REFUSAL_PREFIX)


def correction_from_refusal(
    *,
    agent_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    result: str,
    thread_id: str,
) -> LearningSignal | None:
    """One durable correction, or None when the call succeeded.

    The content names the verb, what was asked of it, and what the world
    said back, in that order - a correction that does not name the verb it
    corrects is a sentence the pipeline cannot attach to anything, and the
    consolidation step groups by subject.
    """
    if not is_refusal(result):
        return None

    said = str(result).lstrip()[len(REFUSAL_PREFIX):].strip()
    asked = ", ".join(f"{k}={v!r}" for k, v in sorted(tool_input.items())) or "no arguments"
    return LearningSignal(
        type="correction",
        source="user",
        subject=tool_name,
        thread_id=thread_id,
        content=(
            f"The world refused `{tool_name}` ({asked}). It said: {said}"
        ),
    )


async def record_correction(
    store: SignalWriter | None,
    *,
    agent_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    result: str,
    thread_id: str,
) -> bool:
    """Write the correction if there is one. True when one was written.

    Never raises. A learning signal is bookkeeping about a turn that has
    already happened, and a resident whose turn dies because the signal
    store is full has been punished for the store's problem - the opposite
    of the trace-store rule, where losing an observation must halt the run,
    because there the record IS the product.
    """
    if store is None:
        return False
    signal = correction_from_refusal(
        agent_id=agent_id, tool_name=tool_name, tool_input=tool_input,
        result=result, thread_id=thread_id,
    )
    if signal is None:
        return False
    try:
        await store.write(signal)
    except Exception as exc:  # noqa: BLE001 - a refusal must still reach the agent
        logger.warning("could not record a world correction for %s: %s", tool_name, exc)
        return False
    return True
