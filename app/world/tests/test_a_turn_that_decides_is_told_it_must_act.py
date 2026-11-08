"""The prompts say an unspent decision expires. Held here so it stays said.

`PROMPT-01`. Run `deni-a6f9eb4-5h`: 3,741 turns, **3,341 of them ending
with `tool_calls_count = 0`**. Not blocked and not refused - the model
reasoned its way to a decision and stopped. 595 of those turns wrote out
the acts they had settled on, in prose, and called none of them; one
narrated a call to `world:describeprimitives`, a tool with no underscore
and no existence, which is how you can tell it was composed rather than
executed.

The town's prompts stated the mechanism ("only a tool call reaches the
world") and never its consequence: that nothing else is going to happen
when you stop, and that nobody is waiting to carry out what you decided.
Codex's base instructions carry exactly that rule for a coding agent -
*"keep going until the query is completely resolved, before ending your
turn and yielding back to the user"* - and the load-bearing half is the
one about there being nobody to hand back to. A resident has no user to
yield to at all, which is why it has to be told.

WHAT IS DELIBERATELY NOT SAID, and the last test holds it: nothing here
tells a resident to act, to trade, to speak, or to do anything in
particular. DESIGN.md section 4's line is "told the prices and the
rules, never the strategy". A resident that reads this and still stops
has made a real choice - which is the thing the experiment exists to
observe, and which telling it to trade would destroy.
"""
from __future__ import annotations

from world.life.soul_seed import law_of_the_town_rules
from world.retrieval_depth import render_perception_text
from world.tests.test_turn_close import rendered


def test_the_law_says_a_decision_is_not_an_act() -> None:
    law = " ".join(law_of_the_town_rules()).lower()
    assert "nobody prompts you again inside a turn" in law
    assert "a decision you did not call simply does not happen" in law


def test_the_law_says_a_narrated_outcome_is_not_an_outcome() -> None:
    """The 595 turns. A resident that writes down an act it did not
    perform reads it back next turn as memory, and acts on it."""
    law = " ".join(law_of_the_town_rules()).lower()
    assert "is something you imagined" in law
    assert "read it next turn as memory" in law


def test_it_is_folded_into_the_speech_rule_not_added_beside_it() -> None:
    """The persistence fact belongs to the rule that already owns "only a
    tool call reaches the world" - same subject, no new tax.

    The COUNT is not asserted here any more; `test_the_law_stays_short`
    owns the budget and the arithmetic for raising it (it went 9 -> 11
    for LIFE-01 and LIFE-02, at a measured $0.20 a run). What this test
    owns is narrower and does not move: this particular fact is inside
    the speech rule rather than beside it, because it is the same
    subject.
    """
    rules = law_of_the_town_rules()
    speech = [r for r in rules if "Only a tool call reaches the world" in r]
    assert len(speech) == 1
    assert "does not happen" in speech[0]


def test_the_closing_line_carries_it_too_and_stays_short() -> None:
    """The law is cached first and read furthest from the moment a model
    decides it has finished; the closing line is the last thing it reads.
    Both, or the rule is stated only where it is least likely to land."""
    closing = rendered().strip().splitlines()[-1]
    assert "does not happen" in closing
    assert len(closing) < 200, "the last line is a tax on every turn of the run"


def test_none_of_it_tells_a_resident_what_to_do() -> None:
    """The line DESIGN.md draws. Mechanism, never strategy."""
    text = (" ".join(law_of_the_town_rules()) + " " + rendered()).lower()
    for strategy in (
        "you should ", "you must act", "make sure you", "remember to ",
        "try to trade", "sell your", "buy from", "it is important that you",
        "be productive", "take action",
    ):
        assert strategy not in text, f"the prompts now coach: {strategy!r}"
