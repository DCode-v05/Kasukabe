"""The compaction summariser must speak about residents, not a user.

Every turn a resident takes is followed by a second, billed call that
compacts the exchange. Its prompt lives in
`engine/src/engine_core/thread.py:4241`, which is frozen under ROADMAP
Law 1 and correct for the product that engine was written for:

    "Summarize this exchange in under 100 tokens total. Format:
     [user] what they said/asked (50 tok max)
     [engine] what you did/responded (50 tok max) ..."

In a town there is no user and no engine, there are two residents. Run 19
shows what the model then wrote into each resident's own memory
(docs/runs/run-19-report.md):

    L296  "[user] At 06:00, user placed me at h_quarry with farmland work
           pending, no food."                                  - Tobias
    L280  "[engine] Assessed pantry stocks (3 grain, 2 meat, berries for
           ~2 days)."                                          - Ainsley

The first has a resident believing a "user" placed it in its own home.
The second is a pantry that does not exist in world state - the whole
town held 12 units of food - laundered into memory as fact.

165 of run 19's 810 calls were this summariser: 20% of the calls, $0.43,
1.1 million input tokens. It is not a side channel; it is a fifth of what
the town paid for.

thread.py may not be edited, so the prompt is rewritten in this package -
the same seam `tool_surface.py` uses to take the engine's seven
assistant-shaped tools off a resident's tool list.
"""

from __future__ import annotations

import asyncio

from src.sim.resident_voice import FROZEN_SUMMARY_PROMPT, resident_summary_prompt, speak_as_resident


class _Message:
    def __init__(self, role: str, content: str) -> None:
        self.role, self.content = role, content


class _Adapter:
    """Records what it was asked to generate, and answers nothing useful."""

    def __init__(self) -> None:
        self.seen: list[list[_Message]] = []
        self.model_id = "test-model"

    async def generate(self, messages, **kwargs):
        self.seen.append(list(messages))
        return "ok"


def test_the_frozen_prompt_is_replaced_before_it_reaches_the_model() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Tobias Holloway")

    asyncio.run(wrapped.generate([_Message("user", FROZEN_SUMMARY_PROMPT)]))

    sent = adapter.seen[0][0].content
    assert "[user]" not in sent and "[engine]" not in sent, (
        "the resident is still being told to summarise itself as an engine "
        "answering a user"
    )
    assert "Tobias Holloway" in sent, "the summary is not written in anyone's voice"


def test_the_frozen_prompt_is_replaced_in_the_engines_own_message_type() -> None:
    """`_Message` above is mutable; the engine's `herald.types.Message` is a
    frozen dataclass. Assigning to its `content` raised, the `except` logged
    and sent the frozen prompt anyway - and all 87 of run 28b's post-turn
    summaries went out as "[user] what they said / [engine] what you did",
    0 as the resident's. Every test here passed the whole time."""
    from src.herald.types import Message, ToolResultBlock

    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Tobias Holloway")
    history = [
        Message("user", "You are at the quarry."),
        Message("user", [ToolResultBlock(tool_use_id="t1", content="3 copper")]),
        Message("user", FROZEN_SUMMARY_PROMPT),
    ]

    asyncio.run(wrapped.generate(history))

    sent = adapter.seen[0][-1].content
    assert "[user]" not in sent and "Tobias Holloway" in sent
    assert wrapped.rewrites == 1
    assert history[-1].content == FROZEN_SUMMARY_PROMPT, "the engine's own message was edited"


def test_the_replacement_still_asks_for_a_short_factual_summary() -> None:
    """The frozen prompt's JOB is right - compaction has to stay cheap and
    factual. Only its vocabulary is wrong, so the replacement keeps the
    budget and the focus."""
    prompt = resident_summary_prompt("Ainsley Holloway")
    low = prompt.lower()
    assert "100 tokens" in low
    assert "facts" in low or "what was said" in low
    assert "invent" in low or "did not happen" in low, (
        "nothing warns against writing down things that did not happen, which "
        "is how a pantry of 3 grain and 2 meat entered Ainsley's memory"
    )


def test_every_other_prompt_passes_through_untouched() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Nell Holloway")

    asyncio.run(wrapped.generate([_Message("user", "It is game-day 0, 06:20. You are Nell.")]))
    assert adapter.seen[0][0].content == "It is game-day 0, 06:20. You are Nell."


def test_the_wrapper_forwards_everything_else_about_the_adapter() -> None:
    """A proxy that hides the adapter's own attributes breaks the engine in
    ways no test here would see."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Nell Holloway")
    assert wrapped.model_id == "test-model"


def test_an_unnamed_resident_still_gets_a_prompt_without_a_user_in_it() -> None:
    prompt = resident_summary_prompt(None)
    assert "[user]" not in prompt and "[engine]" not in prompt


def test_the_wrapper_is_actually_installed_on_the_boot_path() -> None:
    """A rewriter nothing calls is the failure this project keeps finding:
    code that exists, is tested, and is never reached. `pinned_boot` is
    where the adapter every turn goes through is built."""
    from pathlib import Path

    boot = Path(__file__).resolve().parent.parent / "src" / "sim" / "pinned_boot.py"
    text = boot.read_text(encoding="utf-8")
    assert "speak_as_resident" in text, "the rewriter is never wired to the live adapter"
    assert "primary_adapter = speak_as_resident(" in text, (
        "the rewriter is imported but the adapter the thread uses is not wrapped"
    )


def test_a_turn_that_called_no_tool_does_not_pay_for_a_summary() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Tobias Holloway")

    summary = asyncio.run(wrapped.generate([
        _Message("user", "You are at the quarry. Nobody else is here."),
        _Message("assistant", "I should think about what to do with the copper."),
        _Message("user", FROZEN_SUMMARY_PROMPT),
    ]))

    assert adapter.seen == [], "an idle turn still paid for a summariser call"
    assert "Tobias Holloway took no action this turn." in summary
    assert "copper" in summary
    assert wrapped.skipped == 1


def test_a_turn_that_called_a_tool_still_gets_a_real_summary() -> None:
    """Errs toward paying. A turn wrongly called idle loses a real
    summary; a turn wrongly called busy costs one call."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Tobias Holloway")

    asyncio.run(wrapped.generate([
        _Message("user", "You are at the quarry."),
        _Message("assistant", "Mining."),
        _Message("user", [{"type": "tool_result", "content": "3 copper"}]),
        _Message("user", FROZEN_SUMMARY_PROMPT),
    ]))

    assert len(adapter.seen) == 1
    assert wrapped.skipped == 0


def test_only_this_turns_tool_calls_count() -> None:
    """The conversation history carries earlier turns' tool results. A
    detector that looked at the whole list would answer "this turn acted"
    forever after a resident's first successful act."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Tobias Holloway")

    asyncio.run(wrapped.generate([
        _Message("user", "You are at the quarry."),
        _Message("user", [{"type": "tool_result", "content": "3 copper"}]),
        _Message("user", "You are at the quarry. Nobody else is here."),
        _Message("assistant", "Still thinking."),
        _Message("user", FROZEN_SUMMARY_PROMPT),
    ]))

    assert adapter.seen == [], "history from an earlier turn was read as this turn"


def test_a_call_that_is_not_the_summariser_is_never_skipped() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Tobias Holloway")

    asyncio.run(wrapped.generate([
        _Message("user", "What is the price of bread?"),
    ]))

    assert len(adapter.seen) == 1
    assert wrapped.skipped == 0


def test_the_skip_can_be_turned_off(monkeypatch) -> None:
    """One knob, so a run can restore a model call on every turn and
    compare what the signals are worth."""
    import src.sim.resident_voice as rv

    monkeypatch.setattr(rv, "SKIP_IDLE_SUMMARIES", False)
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Tobias Holloway")

    asyncio.run(wrapped.generate([
        _Message("user", "You are at the quarry."),
        _Message("assistant", "Thinking."),
        _Message("user", FROZEN_SUMMARY_PROMPT),
    ]))

    assert len(adapter.seen) == 1
