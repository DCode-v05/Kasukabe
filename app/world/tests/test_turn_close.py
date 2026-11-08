"""The last thing an agent reads is that nothing has happened yet.

MEASURED, iteration 4 of the autonomous loop. Corwin Thorne, asked a direct
question by somebody standing with him:

    output : "I need to answer him. Not with more explaining. With something
              simple..."
    actions: []

He decided to reply and the turn ended. Across that run, **79 of 232 cognition
turns (34%) produced prose and called no tool** - and every one of those is a
paid turn in which the town did not move.

Law rule 6 already states that narrated words reach nobody, and it worked: it
took `say` from the fourth-most-used tool to the first and lifted turns-with-
effect from 40% to 68%. But it lives in the STABLE, cached prompt section -
first thing in, furthest from the decision. This is the same fact placed last,
where a model is deciding whether it is finished.

IT IS STILL A RULE, NOT A STRATEGY. It says nothing about what to do, whom to
speak to, or whether to act at all - only that the turn ends when they stop and
that unspoken intent is not an event. An agent that reads it and chooses to do
nothing has made a real choice, which is exactly what the experiment is for.
"""

from __future__ import annotations

from world.core.perception import AmbientInfo, Perception
from world.retrieval_depth import RetrievalDepthConfig, render_perception_text


def perception(tick: int = 10) -> Perception:
    return Perception(
        agent_id="n03",
        tick=tick,
        location_id="home:h_field",
        ambient=AmbientInfo(game_day=0, hour=6, minute=20, is_daytime=True),
        visible_agents=["n06"],
        heard_events=[],
    )


def rendered(**kw) -> str:
    return render_perception_text(perception(), depth=RetrievalDepthConfig(fraction=0.0), **kw)


def test_the_perception_closes_by_saying_nothing_has_happened_yet() -> None:
    text = rendered()
    tail = text.strip().splitlines()[-1].lower()
    assert "tool" in tail


def test_the_closing_line_is_last() -> None:
    """Recency is the whole reason this exists separately from law rule 6,
    which says the same thing in the cached section and is read first."""
    text = rendered().strip()
    assert text.splitlines()[-1].strip() != ""
    assert "has happened yet" in text.lower()


def test_the_closing_line_does_not_tell_anyone_what_to_do() -> None:
    tail = rendered().lower()
    for strategy in (
        "you should", "you must reply", "answer them", "be sure to talk",
        "remember to say", "make sure you speak",
    ):
        assert strategy not in tail


def test_the_closing_line_is_one_line() -> None:
    """Carried on every turn of every agent for the whole run. Two
    sentences is a tax; a paragraph is a defect."""
    text = rendered().strip()
    closing = text.splitlines()[-1]
    assert len(closing) < 200
