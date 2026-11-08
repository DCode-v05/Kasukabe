"""The learning pipeline, told it is encoding a life and not a helpdesk.

THE DEFECT, read out of run 25's brains rather than out of the code.
`learning_centre/pipeline.py` ships four system prompts, and every one of
them describes the memory of **a personal AI assistant**:

    "You are an episode creation engine for a personal AI assistant's
     memory."
    "The episode captures WHAT HAPPENED - a narrative record of the
     USER'S DIGITAL LIFE."
    "extract entries to store ... facts about THE USER, their team,
     tools, environment, preferences ... e.g. 'deploys on Tuesdays',
     'code review needs 2 approvals', 'checks Grafana first when
     debugging production'"

Rosalind Vance is a miller. She has no team, no Grafana and no deploys,
and there is no user. So the pipeline did the only thing those prompts
allow: it cast her as the operator of an assistant and wrote her life up
as a support ticket. Her real, consolidated memory after run 25 reads:

    "The user concurrently handles interconnected responsibilities:
     trade negotiations (Junia/wheat pricing), conflict resolution ..."
    "The user exhibits consistent location-based workflow optimization:
     negotiating at grocery -> confronting on street -> travelling ..."
    "Debt obligations appear across multiple relationships (Silas,
     Ainsley), suggesting the user tracks financial entanglements ..."

Those are not wrong, exactly. They are about the right events. But they
are written ABOUT a user by a tool, in the third person, in the register
of a productivity report - and they are what gets handed back to Rosalind
as her own memory of her own morning. A resident recalling "the user
exhibits location-based workflow optimization" learns nothing it can act
on. Compare what the same batch produced when the signals were concrete
enough to resist the framing:

    "Junia offered to buy wheat at 10 sim per unit, indicating the
     baseline market price she accepts."   <- usable
    "Bram Holloway owes 260 sim (loan)."   <- usable

The prompts are the difference. This module supplies four that describe
the thing this pipeline is actually encoding.

WHY THIS IS NOT A LAW 1 EXCEPTION. `engine/src/learning_centre/` is
frozen and this module does not edit it. The four prompts are module-level
globals read INSIDE the encoding methods at call time
(`pipeline.py:324, 368, 383, 719` - `system=[SystemPromptBlock(text=
EPISODE_ENCODING_SYSTEM)]`), so rebinding the module attribute before the
first batch runs changes what the pipeline sends without changing a line
of it. `install()` is called from `pinned_boot.py`, the same seam that
already registers the memory stream, `search_memory`, and the resident's
persona section.

That is worth the small indirection: an edit inside the frozen tree is a
merge conflict waiting for the next upstream pull, and it makes "is this
the engine's behaviour or ours?" unanswerable - which is the question the
research depends on.

WHAT IS DELIBERATELY UNCHANGED. The JSON contract of every prompt, exactly.
Field names, nesting, the null/empty sentinels, and the confidence bands
are the pipeline's parser and its storage schema; this module rewrites the
INSTRUCTIONS and touches none of the SHAPE. A prompt rewrite that also
moved a key would be a parser change wearing a prompt's clothes, and it
would fail silently into an empty batch.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kasukabe.sim.resident_learning_prompts")

# Every prompt below says "you" to the resident and never "the user",
# because the thing being remembered is the rememberer's own life. The
# original's second-person-about-a-third-party framing is the whole
# defect.

EPISODE_ENCODING_SYSTEM = (
    "You write the memory of one person living in the town of Kasukabe.\n\n"
    "Given signals from a stretch of that person's day - what they did, "
    "what they were told, what the world allowed and refused - write the "
    "episode as they would remember it.\n\n"
    "This is a life, not a task log. What happened, to whom, and what it "
    "cost or gained. Write in the first person, as the person remembering.\n\n"
    "Produce:\n"
    "1. key: One sentence (max 20 words) naming what happened. Start with "
    "the game-day if the signals carry one. Examples:\n"
    '   - "Day 0 | Milled Kem\'s wheat and stocked the flour; he still owes for it"\n'
    '   - "Day 1 | Asked Tova to work the bakery; she did not answer"\n'
    '   - "Walked to the ore outcrop and found Bram already working it"\n\n'
    "2. description: 2-5 sentences covering:\n"
    "   - What you were trying to do, and why\n"
    "   - What you actually did, and where\n"
    "   - What came of it - what you got, what it cost, what refused you\n"
    "   - Who was there, by name, and what passed between you\n"
    "   - What you would do differently\n\n"
    "Prices, quantities, debts and names are the substance. Keep them "
    "exact. Do not summarise a number into an adjective: 'he owes me 260 "
    "sim' is a memory, 'he owes me a lot' is not.\n\n"
    'Return ONLY valid JSON: {"key": "...", "description": "..."}\n'
    'If nothing worth remembering happened: {"key": null, "description": null}'
)

KNOWLEDGE_ENCODING_SYSTEM = (
    "You record what one resident of the town of Kasukabe has learned.\n\n"
    "Given signals from their day, extract what is worth keeping. Two "
    "kinds:\n"
    '- "knowledge": facts about this town - what things cost, who owns '
    "what, who owes whom, where a trade or a resource is, what somebody "
    "said they would do, what the law and the taxes are\n"
    '- "process": how to actually do something here, learned by doing '
    "it\n"
    "  e.g. 'wheat must be harvested at farmland:wheat, then carried to "
    "the mill to craft into flour', 'the grocery pays 10 sim a unit for "
    "wheat', 'stock_shelf only works at a business you own or work for'\n\n"
    "A refusal from the world is a fact about the world and belongs here. "
    "So does a price somebody named out loud, and so does a promise.\n\n"
    "For each entry:\n"
    '{"subject": "short topic", "content": "the information",\n'
    ' "type": "knowledge" or "process", "confidence": 0.4-1.0,\n'
    ' "tags": ["relevant", "tags"]}\n\n'
    "Write content as something the resident could act on tomorrow. Name "
    "people, places, items and amounts exactly as the signals name them - "
    "an agent id, a sim price, a location like business:b_mill. Never "
    "refer to internal record ids.\n\n"
    # 1.0 is not a strength of belief to the pipeline, it is a CATEGORY:
    # `learning_centre/pipeline.py` reads `confidence >= 1.0` as
    # `is_user_stated` and stores the row as a stated fact, exempt from the
    # 0.8 cap. This prompt's first draft gave 1.0 to "something stated
    # plainly to you, or a refusal the world gave you" - which is nearly
    # everything a resident experiences - and run 26 stored every row as
    # `user_stated` at 1.0, including "world:find_people with query
    # 'resident who tax collector official election' was attempted".
    "Confidence rules. 1.0 is rare: it files the entry as a stated fact for "
    "good, so reserve it.\n"
    "- 1.0 only for a figure or rule said to you word for word: a price "
    "someone quoted, a debt someone admitted, the exact reason the world gave "
    "when it refused you\n"
    "- What you saw happen, or worked out from it: 0.4-0.8\n"
    "- Corrections: 0.7-0.8\n"
    "- A pattern seen once: 0.4-0.6\n"
    "- A pattern seen repeatedly: 0.7-0.8\n\n"
    "Do not record that you called a tool, tried one, or searched for "
    "something - 'world:find_people with query X was attempted' is not "
    "knowledge. Record what it taught you, or nothing.\n\n"
    'Return ONLY valid JSON: {"entries": [...]}\n'
    'If nothing: {"entries": []}'
)

SOCIAL_ENCODING_SYSTEM = (
    "You record the people one resident of the town of Kasukabe knows.\n\n"
    "Given signals from their day, extract the people who appeared and "
    "what stands between them - kin, trade, debt, employment, a dispute, "
    "a promise made or broken.\n\n"
    "Use the name the town uses. Where the signals carry an agent id "
    "(n01, n11), keep it in the attributes so the person can be addressed "
    "later.\n\n"
    "For each person:\n"
    '{"name": "full name", "attributes": [{"key": "trade", "value": "miller"}, '
    '{"key": "agent_id", "value": "n11"}, ...]}\n\n'
    "For each relationship:\n"
    '{"from": "person A", "to": "person B", "relationship": "owes 260 sim"}\n\n'
    "A relationship is what actually holds between them, not a category. "
    '"buys wheat from" and "owes 260 sim for milling" are worth more than '
    '"knows".\n\n'
    'Return ONLY valid JSON: {"nodes": [...], "edges": [...]}\n'
    'If nobody appeared: {"nodes": [], "edges": []}'
)

CONSOLIDATION_SYSTEM = (
    "You settle one resident of the town of Kasukabe's memory at the end "
    "of a stretch of their day.\n\n"
    "Given:\n"
    "- What they learned this batch\n"
    "- The episodes written this batch\n"
    "- Who they are becoming (current traits)\n"
    "- Entries that may contradict each other\n\n"
    "Produce:\n"
    "1. cross_patterns: Things true across several of the above that none "
    "of them says alone - a price that moved, a person who keeps not "
    "paying, a route that keeps failing.\n"
    '   Each: {"subject": "...", "content": "...", "confidence": 0.4-0.8, '
    '"tags": ["cross-batch"]}\n'
    "   State the pattern in the town's own terms, with the names and "
    "numbers in it. Never cite a record id, and never describe the "
    "resident's behaviour from outside - 'Silas has not paid for two "
    "loads' is a pattern; 'the user exhibits workflow optimization' is "
    "not, and is not worth a line of memory.\n"
    "   If nothing spans them, return none. An empty list is a good "
    "answer.\n\n"
    "2. soul_updates: Who this person is turning into, from what they "
    "actually did.\n"
    '   Each: {"action": "new_signal" or "reinforce", "trait": "...", '
    '"value": "...", "confidence": 0.1-0.9, "evidence_count": N}\n'
    "   - New signals: low confidence (0.3-0.5), evidence_count=1\n"
    "   - Reinforcements: increment evidence_count, never lower confidence\n\n"
    "3. contradictions: Resolution of conflicting entries.\n"
    '   Each: {"action": "supersede_existing" or "discard_new" or "keep_both",\n'
    '          "existing_entry_id": "...", "new_entry_id": "...", "reason": "..."}\n'
    "   A price that changed is not a contradiction - it is the newer "
    "price superseding the older one.\n\n"
    "Return ONLY valid JSON:\n"
    '{"cross_patterns": [...], "soul_updates": [...], "contradictions": [...]}\n'
    'If nothing: {"cross_patterns": [], "soul_updates": [], "contradictions": []}'
)

# The four names this module rebinds, and nothing else. Named as data so
# `install()` cannot drift from what the tests check, and so a fifth
# prompt appearing upstream is visible as a gap rather than silently
# unhandled.
PROMPT_NAMES = (
    "EPISODE_ENCODING_SYSTEM",
    "KNOWLEDGE_ENCODING_SYSTEM",
    "SOCIAL_ENCODING_SYSTEM",
    "CONSOLIDATION_SYSTEM",
)


def install(pipeline_module: Any | None = None) -> int:
    """Point the frozen pipeline at the resident prompts. Returns the count.

    Idempotent, and safe to call before any batch has run. Called from
    `pinned_boot.py` at engine start, which is before the first
    `SimLearningGate.on_tick`.

    Raises nothing. A resident whose prompts failed to install still
    learns - badly, in the old register, which is exactly the behaviour
    of every run before this one. Learning is bookkeeping about turns
    that already happened; it must never be why an engine fails to boot.
    """
    if pipeline_module is None:
        from src.learning_centre import pipeline as pipeline_module  # noqa: PLC0415

    installed = 0
    for name in PROMPT_NAMES:
        replacement = globals().get(name)
        if not isinstance(replacement, str) or not replacement:
            logger.warning("no resident prompt for %s - leaving the engine's", name)
            continue
        if not hasattr(pipeline_module, name):
            # Upstream renamed or removed it. Say so loudly: silently
            # skipping is how a prompt rewrite becomes a no-op that
            # nobody notices for twenty-three runs.
            logger.error(
                "learning pipeline has no %s - the resident prompt was NOT "
                "installed and this batch will encode in the assistant "
                "register. Upstream may have renamed it.",
                name,
            )
            continue
        setattr(pipeline_module, name, replacement)
        installed += 1

    logger.info("resident learning prompts installed: %d of %d", installed, len(PROMPT_NAMES))
    return installed
