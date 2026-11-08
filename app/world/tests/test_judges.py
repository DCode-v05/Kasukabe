"""L3 judges 3.2 and 3.3, tested without spending a cent.

`answered_or_deflected` is judge 3.1 and was the only one. The evaluation
spec's §8 requires 3.1 to 3.3 before the first five-day run, so these two
are what that run was waiting on.

Nothing here reaches the broker. What is under test is the part that can
be wrong without anybody noticing: which categories exist, that errors
are counted rather than dropped, that the sample is stratified, and above
all that a judge NEVER returns a verdict - "a judge distribution is not a
gate. It is printed beside the mechanical gate it informs."
"""

from __future__ import annotations

from world.evals.judges import (
    ENERGY_RUBRIC,
    ENERGY_VERDICTS,
    JUDGE_AGENT_ID,
    PERSONA_RUBRIC,
    PERSONA_VERDICTS,
    distribution,
    render,
    stratified,
)


def test_persona_has_the_specs_three_categories():
    """§3.2, verbatim: "one of `in character`, `generic`, `out of
    character`"."""
    assert PERSONA_VERDICTS == ("in character", "generic", "out of character")


def test_generic_is_named_as_its_own_finding_not_a_middle_score():
    """The spec calls it "the important category: text that breaks nothing
    and could be anyone". A rubric that presented it as a middling score
    would collect the turns nobody felt strongly about instead."""
    assert "not a middle score" in PERSONA_RUBRIC
    assert "could be any resident" in PERSONA_RUBRIC


def test_energy_has_the_specs_three_categories():
    assert ENERGY_VERDICTS == ("reasoned about energy", "mentioned", "ignored")


def test_the_energy_rubric_asks_about_the_decision_not_its_quality():
    """S5 asks whether residents REASON about their own energy, not
    whether they reason well. A rubric that rewarded good decisions would
    measure the model's judgement and report it as awareness."""
    assert "not whether the decision was wise" in ENERGY_RUBRIC


def test_neither_rubric_asks_for_a_score_or_a_verdict():
    """Evidence rule 11. A judge that returned pass/fail would quietly
    become the gate and nobody would notice it had."""
    for rubric in (PERSONA_RUBRIC, ENERGY_RUBRIC):
        low = rubric.lower()
        for word in ("pass", "fail", "score out of", "rate ", "1-5", "/10"):
            assert word not in low, f"{word!r} in a judge rubric"


def test_the_rubrics_tell_the_judge_not_to_reward_length_or_manners():
    """3.1 learned this: a judge that rewards politeness scores a town of
    polite deflections as a town that answers."""
    assert "Do not reward" in PERSONA_RUBRIC
    assert "reward" in PERSONA_RUBRIC


def test_the_judge_bills_the_treasury_and_never_a_resident():
    """An eval billed as cognition is a resident paying to be measured,
    and it lands in the cognition lane. The same rule 3.1 follows."""
    assert JUDGE_AGENT_ID == "government:treasury"


def test_the_call_is_tagged_as_an_eval():
    import inspect

    from world.evals import judges

    source = inspect.getsource(judges._ask)
    assert '"X-Kasukabe-Purpose": "eval.judge"' in source


def test_one_item_per_call():
    """A judge asked to score fifty turns in one prompt is a judge whose
    later answers are conditioned on its earlier ones."""
    import inspect

    from world.evals import judges

    source = inspect.getsource(judges._ask)
    assert '"messages": [{"role": "user"' in source


def test_errors_are_counted_not_dropped():
    """Dropping them turns "the broker was down for forty of these" into a
    confident distribution over the ten that worked."""
    counts = distribution(["in character"] * 3 + ["error"] * 7)

    assert counts["error"] == 7
    assert sum(counts.values()) == 10


def test_the_render_names_the_errors_rather_than_hiding_them():
    text = render("persona", distribution(["generic"] * 2 + ["error"] * 8), [])

    assert "could not be judged" in text
    assert "8" in text


def test_the_render_never_says_pass_or_fail():
    text = render(
        "persona",
        distribution(["out of character"] * 50),
        ["n06 tick 61: called Marta 'the user'"],
    ).lower()

    assert "pass" not in text and "fail" not in text
    assert "out of character" in text


def test_the_borderline_cases_are_shown():
    """3.1's rule: "a printed distribution and the borderline cases". A
    distribution with no examples cannot be argued with."""
    text = render(
        "persona", distribution(["generic"]), ["n06 tick 61: could be anyone"]
    )

    assert "borderline: n06 tick 61" in text


def test_an_empty_run_does_not_divide_by_zero():
    """A judging run that found nothing to judge is a normal state - a
    town where nobody spoke - not a crash."""
    assert "0 judged" in render("persona", distribution([]), [])


def test_the_sample_is_stratified_so_one_resident_cannot_dominate():
    """One resident took 158 turns in a run and another took 19. An
    unstratified sample is mostly a judgement about the talkative one."""
    rows = [{"agent_id": "n06", "turn": i} for i in range(100)]
    rows += [{"agent_id": "n14", "turn": i} for i in range(3)]

    sample = stratified(rows, "agent_id", per_group=5)

    assert sum(1 for r in sample if r["agent_id"] == "n06") == 5
    assert sum(1 for r in sample if r["agent_id"] == "n14") == 3


def test_stratifying_keeps_the_order_it_was_given():
    """So a caller that sorted by tick gets a sample spread across the
    run rather than one clustered at whichever end it read first."""
    rows = [{"agent_id": "n06", "tick": t} for t in (10, 20, 30, 40)]

    sample = stratified(rows, "agent_id", per_group=2)

    assert [r["tick"] for r in sample] == [10, 20]
