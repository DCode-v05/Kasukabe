"""ENG-02 and ENG-09: what a resident's history is, decided in the seam.

Two findings, one class, because they are two halves of the same sentence:
this is where the seam decides what a turn is handed back about itself.
ENG-02 is how MUCH history a turn sees; ENG-09 is what each remembered
turn IS. See the second half of this docstring for ENG-09.

# ENG-02: bound the resident's history in the seam, not by compaction

The operator's decision of 2026-09-04: history is bounded in the SEAM.

What was happening. `SIM_CONTEXT_WINDOW` was set to 12,500 deliberately, to
make the engine compact early and keep prompts small. But the engine's
preflight is

    post_tokens + sys_tokens + tools_tokens + RESERVE_FLOOR >= context_window

with `RESERVE_FLOOR = 25_000` - an absolute constant. 25,000 alone exceeds
12,500, so the test was true with **zero messages**, on every call, for
every resident, in every run. Every single model call took the EMERGENCY
path, which:

  * guts all tool results, so a resident never sees what its own actions
    returned earlier in the same tick;
  * keeps `groups[0]` - the resident's first stored message, which is the
    day-0 06:20 perception - forever, so every prompt for twenty-three runs
    carried a stale morning;
  * appends "[Context restored after compaction]" and a "[Post-compact tool
    reminder]" of assistant-genre text to a villager's prompt;
  * invalidates the consciousness manager, so the prompt cache was cold on
    every call - we paid full price for a prefix that never changed.

A resident diagnosed it out loud in run 23: *"the compaction is occurring
BETWEEN my thought and the action."*

What this does instead. The seam decides what history a turn sees, before
the engine ever looks at it:

  * `DISABLE_AUTO_COMPACT=1` so the engine stops rewriting the prefix;
  * the window is raised to the model's real size, so `cap_turn_tool_results`
    (25% of window) stops truncating world results to ~3K tokens;
  * `BoundedConversationStore` returns the last N exchanges instead of the
    whole thread, so history is bounded by a rule we control and can state,
    rather than by an emergency.

Nothing in `src/engine_core/` is modified. This subclasses its store and is
swapped in at boot, which is the same seam technique `world_corrections`
and `resident_voice` already use.

# ENG-09: a turn is remembered as what it did

152 of run 23's 349 sessions were one paragraph of prose and no action at
all. The review's first named cause is not the prompt, it is the history:
frozen `thread.py:3098-3108` stores `json.dumps(full_text)` as the
assistant row of a turn - the narration, and only the narration. So every
earlier self a resident meets in its own context is a monologue with no
action anywhere in it, and the genre a model watches itself write is the
genre it goes on to write. Narrowing which of those paragraphs were loaded,
which is all the first half of this module did, changed how many essays a
resident read and not that they were essays.

So the assistant row is now an ACTION RECORD. `save_message` asks the
session log what the turn in flight actually did and stores that instead:
the calls, their arguments, what came back, and - for a turn that called
nothing - exactly that fact, in as many words. The narration is not lost;
it is still returned to the world as the turn's reply, still written to
working memory by the engine, and still kept whole in the session log,
which is where ENG-09 says to keep it for research.

What this does NOT reach, and cannot from here: `thread.py:3082` also
writes `full_text` to working memory as a `MESSAGE_ASSISTANT` entry, and
`thread.py:2995-2996` rebuilds the turn's history from those WM entries
when the SQLite read returned nothing. On that fallback path the resident
reads its narration back exactly as before. Both lines are in
`src/engine_core/`, which ROADMAP Law 1 freezes. The fallback is reached
only when the conversation read failed or the thread is genuinely empty,
so it is a degraded path rather than the normal one - but it is a real
hole and it is named here rather than left to be discovered in a run.
"""

from __future__ import annotations

import json
import os

from src.engine_core.conversation_store import ConversationStore
from src.sim.session_log import active_session_log

# How many stored messages a turn may see. Not "how many fit" - how many are
# WORTH seeing. A resident acts on what is in front of it now: the last
# handful of exchanges plus the perception it was just handed. Everything
# older is the memory system's job, and the memory system is a real one
# (memory_stream, memory_query, the learning centre) rather than a prefix
# nobody chose.
#
# Kept as an env knob because the right number is an empirical question and
# this is the first run that can ask it honestly.
DEFAULT_HISTORY_MESSAGES = int(os.environ.get("KASUKABE_HISTORY_MESSAGES", "24"))


class BoundedConversationStore(ConversationStore):
    """A ConversationStore that decides what a turn is handed about itself.

    `load_messages` narrows to the recent tail (ENG-02): loading is where
    the size of a prompt is decided. `save_message` rewrites the assistant
    row as an action record (ENG-09): saving is where the CONTENT of a
    remembered turn is decided, and the review found this class had left
    that entirely to the parent while saying so in its own docstring.

    Pinning, the thread index and everything else remain the parent's.
    """

    def __init__(self, db, *, keep: int | None = None, actions=None) -> None:
        super().__init__(db)
        self.keep = DEFAULT_HISTORY_MESSAGES if keep is None else keep
        # Counted so a run can say how much it dropped rather than leaving
        # it to be inferred from a bill.
        self.messages_dropped = 0
        # Where "what did this turn do" is asked. Any object with an
        # `action_record()` - in a real run, the process's SessionLogWriter;
        # in a test, one built for the test. `None` means look it up at save
        # time instead of holding a reference, so this class does not care
        # whether boot built the session log before or after it built this.
        self._actions = actions
        # Counted for the same reason `messages_dropped` is: a run that
        # rewrote nothing looks identical to a run without this code, and
        # the difference should be a number rather than an inference.
        self.assistant_turns_recorded_as_actions = 0

    @property
    def records_actions(self) -> bool:
        """Can this store answer "what did the turn just do"?

        False means every assistant row it saves will be the narration
        again, which is exactly ENG-09 - so it is worth being able to
        assert on rather than discover in a run.
        """
        return (self._actions or active_session_log()) is not None

    def _action_record(self) -> str | None:
        source = self._actions if self._actions is not None else active_session_log()
        if source is None:
            return None
        return source.action_record()

    async def save_message(
        self, thread_id: str, seq: int, role: str, content_json: str
    ) -> None:
        """Store the assistant turn as what it did, not as what it said.

        Only the assistant row. The user row is the world talking to the
        resident - the perception - and rewriting that would be the seam
        editing the town's own words.

        A `None` record means no turn is in flight and nothing here knows
        what happened, so the engine's own content stands. Failing open
        matters: this is on the path of every turn, and a history written
        by a guess is worse than a history written as prose.
        """
        if role == "assistant":
            record = self._action_record()
            if record is not None:
                content_json = json.dumps(record)
                self.assistant_turns_recorded_as_actions += 1
        await super().save_message(thread_id, seq, role, content_json)

    async def load_messages(self, thread_id: str) -> list[dict]:
        rows = await super().load_messages(thread_id)
        if self.keep <= 0 or len(rows) <= self.keep:
            return rows

        kept = rows[-self.keep :]
        self.messages_dropped += len(rows) - len(kept)

        # Do not start the window on an orphaned tool result: a `tool` role
        # whose matching call was dropped is a message the model cannot
        # interpret, and some providers reject it outright. Walk forward to
        # the first message that can legally begin a conversation.
        while kept and kept[0].get("role") not in ("user", "assistant"):
            kept = kept[1:]
            self.messages_dropped += 1
        return kept


def bound_history_in_the_seam(
    coordinator, *, keep: int | None = None, actions=None
) -> int:
    """Swap the coordinator's store for a bounded one. Returns `keep`.

    Must run BEFORE `get_or_create_thread`, because that call is what
    propagates the store onto the thread. Returns the bound so a caller can
    log the decision rather than assume it.

    `actions` is where the ENG-09 action record comes from. It is optional
    and left unset by the one live caller, so the store finds the process's
    session log for itself: this function's whole argument list at that call
    site is `(coordinator)`, and a fix that only works when a caller opts
    into it is a fix that is not running.
    """
    if coordinator.conversation_store is None:
        return 0
    bounded = BoundedConversationStore(
        coordinator.conversation_store.db, keep=keep, actions=actions
    )
    coordinator.conversation_store = bounded
    return bounded.keep
