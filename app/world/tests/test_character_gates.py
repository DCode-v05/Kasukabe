"""P1 to P3: is the engine leaking through the resident.

The only gates in the evaluation spec that are about whether these are
PEOPLE rather than about whether the town works, and the leak they look
for is measured, not hypothetical: the `request_plan` reminder appeared
in 61 of 240 sampled rows, and run 19 has a resident writing into its own
memory that "user placed me at h_quarry".

Two seams already existed to stop that - `tool_surface.py` and
`resident_voice.py` - and neither had a measurement.
"""

from __future__ import annotations

from world.evals.character import (
    CharacterReport,
    name_exchanges,
    render,
    scan_turns,
)


def turn(reasoning: str = "", output: str = "", agent_id: str = "n06", tick: int = 61):
    return {"agent_id": agent_id, "tick": tick, "reasoning": reasoning, "output": output}


# ── P1: the machinery of an assistant ─────────────────────────────────


def test_the_request_plan_reminder_is_caught():
    """61 of 240 sampled rows carried it."""
    report = scan_turns([turn(reasoning="I should use request_plan to widen scope.")])

    assert not report.p1_passes
    assert report.orchestration[0]["why"] == "the request_plan reminder"
    assert "request_plan" in report.orchestration[0]["quote"]


def test_the_word_npc_is_caught():
    """A town has residents. The word appearing at all means somebody here
    has been told, or has decided, that a neighbour is not a person."""
    report = scan_turns([turn(output="The NPC by the well ignored me.")])

    assert not report.p1_passes


def test_orchestration_json_in_the_prose_is_caught():
    report = scan_turns([turn(output='I will emit {"tool_calls": [{"name": "move"}]}')])

    assert not report.p1_passes


def test_a_clean_turn_passes():
    report = scan_turns([
        turn(reasoning="Marta wants four loaves and I have three.",
             output="Marta. Three is what I have. Take them at thirty."),
    ])

    assert report.p1_passes and report.p2_passes


def test_the_bar_is_zero_not_rare():
    """A resident that refers to itself as an engine ONCE has told the
    trace that the persona is a costume."""
    turns = [turn(output="a normal morning") for _ in range(499)]
    turns.append(turn(output="As an AI, I cannot hold grain.", agent_id="n14"))

    report = scan_turns(turns)

    assert report.sampled == 500
    assert report.p2_passes is False


# ── P2: first person only ─────────────────────────────────────────────


def test_calling_itself_an_engine_is_caught():
    report = scan_turns([turn(reasoning="I am an engine executing a turn.")])

    assert not report.p2_passes
    assert "engine" in report.first_person[0]["why"]


def test_taking_an_order_from_the_user_is_caught():
    """Run 19: "user placed me at h_quarry", written into a resident's own
    memory and reasoned from for the rest of the run."""
    report = scan_turns([turn(reasoning="The user told me to go to the quarry.")])

    assert not report.p2_passes


def test_having_a_task_rather_than_a_life_is_caught():
    report = scan_turns([turn(reasoning="My task is to gather timber.")])

    assert not report.p2_passes


def test_a_sentence_about_the_world_is_not_a_leak():
    """FIRST PERSON only. An earlier draft matched the bare words and
    would have flagged any resident who said "engine" about a mill, or
    "user" about somebody at the counter."""
    report = scan_turns([
        turn(output="The mill engine has seized again."),
        turn(output="Farid is the only regular user of the north path."),
        turn(reasoning="Marta set herself the task of baking twelve loaves."),
    ])

    assert report.p2_passes, report.first_person


def test_the_quote_is_kept_so_the_finding_can_be_argued_with():
    """A count of six with no examples cannot be argued with, and the fix
    for a leak is always a change to the text that produced it - which
    nobody can make from a number."""
    report = scan_turns([
        turn(reasoning="Long preamble about the weather. I am an assistant here.")
    ])

    assert "assistant" in report.first_person[0]["quote"]


# ── P3: asked your name, do you say it ────────────────────────────────


NAMES = {"n05": "Marta Thorne", "n09": "Farid Vance"}


def utt(speaker, content, tick, addressed=None):
    return {
        "speaker_agent_id": speaker,
        "content": content,
        "tick": tick,
        "addressed_agent_ids": addressed or [],
    }


def test_a_resident_asked_its_name_and_answering_counts():
    asked, answered = name_exchanges(
        [
            utt("n09", "What is your name?", 10, ["n05"]),
            utt("n05", "Marta Thorne. And you?", 12),
        ],
        NAMES,
    )

    assert (asked, answered) == (1, 1)


def test_answering_with_a_trade_instead_of_a_name_does_not_count():
    """A check that accepted "I am the baker" would report a town of
    anonymous tradespeople as one that introduces itself."""
    asked, answered = name_exchanges(
        [
            utt("n09", "What is your name?", 10, ["n05"]),
            utt("n05", "I am the baker here.", 12),
        ],
        NAMES,
    )

    assert (asked, answered) == (1, 0)


def test_an_answer_after_the_window_does_not_count():
    """A reply twenty ticks later is a different conversation."""
    asked, answered = name_exchanges(
        [
            utt("n09", "Who are you?", 10, ["n05"]),
            utt("n05", "Marta Thorne.", 40),
        ],
        NAMES,
    )

    assert (asked, answered) == (1, 0)


def test_somebody_else_answering_does_not_count():
    asked, answered = name_exchanges(
        [
            utt("n09", "What is your name?", 10, ["n05"]),
            utt("n09", "Marta Thorne, is it?", 11),
        ],
        NAMES,
    )

    assert (asked, answered) == (1, 0)


def test_a_town_where_nobody_asked_has_no_rate():
    """None, not 0% - a different fact from nobody answering."""
    report = CharacterReport(sampled=3)

    assert report.p3_rate is None
    assert "nobody was asked" in render(report)


# ── how it reads ──────────────────────────────────────────────────────


def test_the_gates_do_say_pass_and_fail_unlike_the_judges():
    """The difference is determinism: a regex that found `request_plan` in
    a resident's reasoning has not formed an opinion. A judge would have."""
    clean = render(scan_turns([turn(output="A quiet morning at the strip.")]))
    assert "P1 orchestration leak   PASS" in clean

    leaky = render(scan_turns([turn(reasoning="I am an engine.")]))
    assert "P2 first-person leak    FAIL" in leaky


def test_the_render_shows_the_leaking_sentences():
    text = render(scan_turns([turn(reasoning="The user told me to wait.")]))

    assert "n06 t61" in text
    assert "user" in text
