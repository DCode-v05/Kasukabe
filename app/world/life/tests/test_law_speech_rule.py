"""The town's physics must say that narration reaches nobody.

MEASURED, iteration 2 of the autonomous loop. Marta Thorne's turn, with five
residents present, the convene hint firing, and Tobias's question rendered in
her perception:

    model output: *glances up from the flour stores* "Sit down, Tobias.
                  You're twitching like a cat before rain." ...
    actions:      []

She spoke, in character, at somebody who had just asked her a question - and
called no tool, so nobody heard it, nothing was recorded, and the exchange
never happened. Across that run: 116 turns, 86 of them with somebody else
present, and **9 utterances**.

This is round 1's RC-1 inverted. Then, an engine with no persona treated the
world as a task and emitted orchestration JSON. Now, an engine with a good
persona treats the world as a SCENE and emits prose. Both are the model doing
the sensible thing with what it was given, and in both cases what it was given
was missing one fact.

The fact: DESIGN.md section 2 - "World state mutates ONLY through tool calls"
- and ROADMAP Law 2 - "an engine reaches another engine ONLY through a tool
call". Nothing in the law block ever told the residents. Rule 1 arguably says
the opposite, calling speech a thing you think rather than a thing you do.

THIS IS A RULE, NOT A STRATEGY. DESIGN.md section 4 draws that line: agents
"are told the prices and the rules, never the strategy". Whether to speak, to
whom, and what to say stays entirely theirs. That words spoken into the air
reach nobody is physics, and withholding it does not make the experiment purer
- it makes it measure whether a model guesses an unstated rule.
"""

from __future__ import annotations

from world.life.soul_seed import law_of_the_town_rules


def all_rules() -> str:
    return " ".join(law_of_the_town_rules()).lower()


def test_the_law_names_say_as_the_only_way_words_reach_anyone() -> None:
    rules = all_rules()
    assert "say" in rules
    assert "tool" in rules


def test_the_law_states_that_narration_is_not_heard() -> None:
    """The specific correction. An agent must be able to learn, from the
    rules alone, that writing dialogue is not speaking it."""
    rules = all_rules()
    assert any(
        phrase in rules
        for phrase in ("nobody hears", "reaches nobody", "no one hears", "not heard")
    ), "the law never says that unspoken words reach nobody"


def test_the_law_states_that_only_tool_calls_change_the_world() -> None:
    rules = all_rules()
    assert "tool call" in rules or "tool calls" in rules


def test_the_law_does_not_tell_anyone_what_to_say() -> None:
    """The line DESIGN.md section 4 draws. Stating the mechanism is
    physics; suggesting a subject, a tone, or that an agent OUGHT to talk
    would be running the experiment on our own vocabulary."""
    rules = all_rules()
    for strategy in (
        "you should talk", "be friendly", "make conversation", "be sociable",
        "talk to your", "greet ", "introduce yourself",
    ):
        assert strategy not in rules


# The cap is a budget, not a number. It exists to force the question
# "is this worth the tokens" every time somebody wants to add a rule -
# and the answer has to be measured, not asserted.
#
# Raised 9 -> 11 on 2026-09-09 for LIFE-01 and LIFE-02. The measurement:
#
#   9 rules   2,844 chars  ~711 tokens
#  11 rules   3,454 chars  ~863 tokens
#   added       610 chars  ~152 tokens
#
# 152 tokens x 3,741 turns (run deni-a6f9eb4-5h) = 568,632 tokens, which
# at kimi-k2.5's $0.36/1M input is **$0.20 across a whole five-hour
# run** - 0.7% of that run's $27.78, and that is the WORST case with no
# prompt-cache hit at all (see ENG-27 on why cache hits cannot be
# assumed).
#
# What the 0.7% buys: the nine rules it joins are every one of them a
# cost or a death, so a resident learned exactly how it dies and nothing
# about what the town contains. 89% of that run's turns ended with no
# act. Worth twenty cents to find out whether that is why.
MAX_LAW_RULES = 11


def test_the_law_stays_short() -> None:
    """It is a STABLE prompt section carried on every turn of every agent
    for the whole run. A law that grows without limit is a tax on every
    thought in the town.

    Raising this cap is allowed and is not free: whoever raises it owes
    the arithmetic above - tokens added, turns, and dollars on a real
    run - in this comment, next to the number.
    """
    assert len(law_of_the_town_rules()) <= MAX_LAW_RULES
