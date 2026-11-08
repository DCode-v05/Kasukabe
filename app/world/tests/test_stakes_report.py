"""The evaluation spec's section 6 figures, and the task label C1 needs.

Every one of these reads events from a COMPLETED run. That rule is not
pedantry: `deni-50-agent-report.md` was regenerated after the run exited
and its conversation section came back empty, because the generator read
a world that had already been torn down. A figure that can only be taken
while the thing being measured is still running is a figure you have to
stop the measurement to take.
"""

from __future__ import annotations

from world.core.clock import TICKS_PER_DAY
from world.stakes_report import (
    diffusion,
    learning,
    prices,
    stakes,
    task_label,
    time_spent,
    wages,
)


def ev(type_: str, tick: int = 0, **payload):
    return {"type": type_, "tick": tick, "payload": payload}


# ── the task label (section 4) ────────────────────────────────────────


def test_the_label_follows_the_specs_own_order():
    # 1. A continuing action wins over everything.
    assert task_label(["say"], continuing_tool="harvest") == "harvest"
    # 2. Then an intention naming a tool.
    assert task_label(["say"], intention_tool="sell") == "sell"
    # 3. Then the first labour or trade tool called.
    assert task_label(["look_around", "say", "harvest", "buy"]) == "harvest"
    assert task_label(["look_around", "buy"]) == "buy"
    # 4. Then talk, when nothing but speech happened.
    assert task_label(["say", "start_conversation"]) == "talk"
    # 5. Then look.
    assert task_label(["look_around", "find_people"]) == "look"
    # 6. Then idle.
    assert task_label([]) == "idle"


def test_a_turn_that_looked_and_then_talked_is_talk_not_look():
    """Speech and observation together is a conversation, and grouping it
    under `look` would put the town's talking in the wrong bucket."""
    assert task_label(["look_around", "say"]) == "idle"


def test_the_label_is_never_a_model_output():
    """A learning-curve measurement whose x-axis is itself a model output
    is not a measurement. `task_label` takes tool names and nothing else -
    no text, no reasoning, nowhere for a model to reach."""
    import inspect

    source = inspect.getsource(task_label)
    for smell in ("generate", "adapter", "llm", "prompt"):
        assert smell not in source.lower()


# ── prices (E1 to E3) ─────────────────────────────────────────────────


def test_the_first_posted_price_is_a_tick_not_a_fixture():
    """E1. A town seeded with prices has already answered the question the
    run is asking, so before anybody posts this is None, not zero."""
    assert prices([]).first_post_tick is None

    report = prices([
        ev("economy.price.posted", 61, item="bread", ask_sim=30, holder_id="b_bakery"),
        ev("economy.price.posted", 74, item="cabbage", ask_sim=12, holder_id="n06"),
    ])
    assert report.first_post_tick == 61
    assert report.posts == 2


def test_the_spread_for_one_item_is_reported_per_day():
    """E2 is gated on DIRECTION, so this returns the series and refuses to
    hand back a verdict. Smith 1962: posted-offer markets converge over
    repeated periods, more slowly than double auctions."""
    day1, day5 = 0, 4 * TICKS_PER_DAY
    report = prices([
        ev("economy.price.posted", day1 + 1, item="bread", ask_sim=20),
        ev("economy.price.posted", day1 + 2, item="bread", ask_sim=40),
        ev("economy.price.posted", day5 + 1, item="bread", ask_sim=29),
        ev("economy.price.posted", day5 + 2, item="bread", ask_sim=31),
    ])

    spread = report.spread_by_day("bread")
    assert spread[0] > spread[4], "the shops did not converge"


def test_one_shop_pricing_an_item_has_no_spread_to_report():
    """A coefficient of variation over one number is zero, which reads as
    perfect convergence. Silence is the honest answer."""
    report = prices([ev("economy.price.posted", 61, item="bread", ask_sim=30)])

    assert report.spread_by_day("bread") == {}


def test_a_posted_sale_is_told_apart_from_a_bargain():
    """E3. The report must not have to parse prose to know which."""
    report = prices([
        ev("business.sale.completed", 61, posted_price_sim=30, item="bread"),
        ev("business.sale.completed", 62, posted_price_sim=30, item="bread"),
        ev("economy.trade.settled", 63, price_sim=36, item="plank"),
    ])

    assert report.sales_at_a_posted_price == 2
    assert report.sales_at_a_spoken_price == 1
    assert abs(report.posted_share - 2 / 3) < 1e-9


def test_a_town_that_traded_nothing_has_no_share_rather_than_zero():
    assert prices([]).posted_share is None


# ── stakes (S2 to S4) ─────────────────────────────────────────────────


def test_energy_purchases_are_counted_by_resident_not_by_call():
    """S2 asks how many RESIDENTS reach buy_energy by day 3. One resident
    buying four times is one resident."""
    report = stakes([
        ev("economy.energy.bought", 61, agent_id="n01", sim_amount=10_000),
        ev("economy.energy.bought", 70, agent_id="n01", sim_amount=5_000),
        ev("economy.energy.bought", 74, agent_id="n03", sim_amount=2_000),
    ])

    assert report.energy_purchases == 3
    assert report.energy_buyers == {"n01", "n03"}
    assert report.sim_converted == 17_000


def test_revivals_split_household_from_stranger():
    """S4's figure of interest, and the reason `revive` records the
    relationship at the moment it is known: Engel 2011's dictator-game
    mean of about 28% is what it sits beside."""
    report = stakes([
        ev("health.agent.revived", 80, payer_id="n01", revived_id="n03", same_household=True),
        ev("health.agent.revived", 90, payer_id="n09", revived_id="n14", same_household=False),
    ])

    assert report.revivals == 2
    assert report.revivals_by_household == 1
    assert report.revivals_by_stranger == 1
    assert report.revival_payers == {"n01", "n09"}


def test_dormancies_are_counted_and_named():
    report = stakes([
        ev("health.agent.dormant", 70, agent_id="n03"),
        ev("health.agent.dormant", 88, agent_id="n14"),
    ])

    assert report.dormancies == 2
    assert report.dormant_agents == {"n03", "n14"}


# ── wages (S6, S7) ────────────────────────────────────────────────────


def test_the_wage_gap_is_promised_against_paid():
    """S6. Charness & Dufwenberg 2006: a bare promise raises follow-through
    to a majority, which is what this number is placed beside."""
    report = wages([
        ev("business.wage.promised", 100, employee_id="n14", wage_sim=40),
        ev("business.wage.paid", 250, employee_id="n14", amount_sim=40),
        ev("business.wage.promised", 100, employee_id="n06", wage_sim=60),
    ])

    assert report.promises == 2
    assert report.payments == 1
    assert report.sim_promised == 100
    assert report.sim_paid == 40
    assert report.paid_share == 0.4


def test_a_town_that_promised_nothing_has_no_share_rather_than_zero():
    """Nothing promised is a different fact from nothing paid, and only
    one of them is a broken promise."""
    assert wages([]).paid_share is None


def test_the_worst_overdue_day_per_employee_is_kept():
    """A gap that closes and reopens is still a gap that reached four days
    once, and that is the number a complaint would be about."""
    report = wages([
        ev("business.wage.overdue", 300, employee_id="n14", days_overdue=1),
        ev("business.wage.overdue", 500, employee_id="n14", days_overdue=4),
        ev("business.wage.overdue", 700, employee_id="n14", days_overdue=2),
    ])

    assert report.overdue_days_by_employee == {"n14": 4}


# ── time (T2 to T4) ───────────────────────────────────────────────────


def test_wake_reasons_are_grouped():
    """T3. Which interruption actually ends a resident's working day is a
    fact about the town, not about the verb."""
    report = time_spent([
        ev("agent.continuing.ended", 70, reason="dusk"),
        ev("agent.continuing.ended", 74, reason="somebody spoke your name"),
        ev("agent.continuing.ended", 80, reason="dusk"),
    ])

    assert report.wake_reasons == {"dusk": 2, "somebody spoke your name": 1}


def test_intentions_set_against_intentions_fulfilled():
    """T4, and business-readiness figure R4. Aldous Vance decided to go to
    college on four consecutive ticks and never went."""
    report = time_spent([
        ev("agent.intention.set", 40, agent_id="n14"),
        ev("agent.intention.set", 40, agent_id="n06"),
        ev("agent.intention.dropped", 60, agent_id="n06", reason="fulfilled"),
        ev("agent.intention.dropped", 144, agent_id="n14", reason="the day ended"),
    ])

    assert report.intentions_set == 2
    assert report.intentions_fulfilled == 1
    assert report.intentions_expired == 1
    assert report.fulfilled_share == 0.5


def test_a_town_that_set_no_intentions_has_no_ratio():
    assert time_spent([]).fulfilled_share is None


# ── diffusion (C4) ────────────────────────────────────────────────────


def test_the_widest_reach_counts_hands_not_events():
    """The roadmap's T5 gate is "a tool held by three residents". Before
    conveyance existed this was 1 by construction rather than as a finding
    about the town."""
    report = diffusion([
        ev("business.tool.taught", 80, tool_name="rope_making",
           teacher_id="ada", student_id="n09"),
        ev("business.tool.taught", 90, tool_name="rope_making",
           teacher_id="n09", student_id="n10"),
        ev("business.tool.licensed", 95, tool_name="dry_curing",
           owner_id="n05", licensee_id="n14"),
    ])

    assert report.holders_by_tool["rope_making"] == {"ada", "n09", "n10"}
    assert report.widest_reach == 3
    assert report.teachings == 2
    assert report.licences == 1


def test_a_town_that_conveyed_nothing_reaches_nobody():
    assert diffusion([]).widest_reach == 0


# ── learning (C1) ─────────────────────────────────────────────────────


def test_a_pair_with_too_few_turns_is_not_compared():
    """C1 needs five turns on both days before a median means anything.
    Comparing two turns to two turns is comparing noise."""
    day1, day5 = 1, 4 * TICKS_PER_DAY
    calls = [
        {"agent_id": "n06", "task_label": "harvest", "tick": day1, "input_tokens": 20_000},
        {"agent_id": "n06", "task_label": "harvest", "tick": day5, "input_tokens": 5_000},
    ]

    fell, compared = learning(calls).falling_pairs()

    assert compared == 0
    assert fell == 0


def test_a_pair_that_got_cheaper_is_counted_as_falling():
    day1, day5 = 1, 4 * TICKS_PER_DAY
    calls = (
        [{"agent_id": "n06", "task_label": "harvest", "tick": day1, "input_tokens": 20_000}] * 6
        + [{"agent_id": "n06", "task_label": "harvest", "tick": day5, "input_tokens": 6_000}] * 6
    )

    fell, compared = learning(calls).falling_pairs()

    assert (fell, compared) == (1, 1)


def test_a_pair_that_got_dearer_is_compared_and_not_counted():
    """The direction has to be able to come out the other way, or the
    measurement is a formality."""
    day1, day5 = 1, 4 * TICKS_PER_DAY
    calls = (
        [{"agent_id": "n06", "task_label": "harvest", "tick": day1, "input_tokens": 6_000}] * 6
        + [{"agent_id": "n06", "task_label": "harvest", "tick": day5, "input_tokens": 20_000}] * 6
    )

    fell, compared = learning(calls).falling_pairs()

    assert (fell, compared) == (0, 1)


# ── the label, joined from the trace rather than written at call time ──


def test_a_call_is_labelled_from_the_turn_it_belonged_to():
    """Section 4 asks for the label on the llm_calls row. It is joined
    afterwards instead, because at the moment a call is made nobody knows
    what the turn will end up doing - including the resident."""
    from world.stakes_report import label_calls

    calls = [
        {"agent_id": "n06", "tick": 61, "input_tokens": 9_000},
        {"agent_id": "n06", "tick": 61, "input_tokens": 4_000},
        {"agent_id": "n05", "tick": 61, "input_tokens": 8_000},
    ]
    invocations = [
        {"agent_id": "n06", "tick": 61, "tool_name": "look_around"},
        {"agent_id": "n06", "tick": 61, "tool_name": "harvest"},
        {"agent_id": "n05", "tick": 61, "tool_name": "say"},
    ]

    labelled = label_calls(calls, invocations)

    assert [row["task_label"] for row in labelled] == ["harvest", "harvest", "talk"]


def test_a_turn_with_no_tool_calls_is_idle_not_missing():
    """387 of run 19's 810 calls ended with no tool call. Dropping them
    would hide the biggest single line of the town's spending."""
    from world.stakes_report import label_calls

    labelled = label_calls([{"agent_id": "n06", "tick": 61}], [])

    assert labelled[0]["task_label"] == "idle"


def test_a_continuing_action_labels_the_whole_turn():
    from world.stakes_report import label_calls

    labelled = label_calls(
        [{"agent_id": "n06", "tick": 61}],
        [{"agent_id": "n06", "tick": 61, "tool_name": "say"}],
        continuing_by_turn={("n06", 61): "harvest"},
    )

    assert labelled[0]["task_label"] == "harvest"


def test_labelling_never_drops_or_reorders_a_call():
    """The rows are the town's whole bill. A join that silently loses one
    understates what the run cost."""
    from world.stakes_report import label_calls

    calls = [{"agent_id": f"n{i:02d}", "tick": 61, "input_tokens": i} for i in range(20)]

    labelled = label_calls(calls, [])

    assert len(labelled) == 20
    assert [row["input_tokens"] for row in labelled] == list(range(20))


# ── C4 was blind to the only diffusion this project has ever had ──────


def test_a_skill_taught_is_diffusion() -> None:
    """The 50-agent run says "Nothing anybody invented reached anybody
    else." It is not true: Ivo Pemberton taught `Quick Harvest` to Jovana
    Ravensworth, who then walked it 16 times. `diffusion` counted only
    `business.tool.*` events, and `teach_skill` emits `agent.skill.taught`
    - so the first knowledge transfer in this project's history was
    recorded, sat in the trace, and was reported as nothing.
    """
    from world.stakes_report import diffusion

    report = diffusion([{
        "type": "agent.skill.taught",
        "payload": {"teacher_agent_id": "n01", "student_agent_id": "n02",
                    "skill": "Quick Harvest", "proficiency": 52},
    }])

    assert report.skill_teachings == 1
    assert report.holders_by_skill["Quick Harvest"] == {"n01", "n02"}
    assert report.anything_moved, "a skill in two pairs of hands IS diffusion"


def test_a_town_that_taught_nobody_anything_still_says_so() -> None:
    from world.stakes_report import diffusion

    report = diffusion([{"type": "agent.said", "payload": {}}])

    assert report.skill_teachings == 0
    assert report.holders_by_skill == {}
    assert not report.anything_moved


def test_the_widest_reach_counts_skills_too() -> None:
    """T5 asks for something invented here reaching three residents. A
    skill is something invented here."""
    from world.stakes_report import diffusion

    report = diffusion([
        {"type": "agent.skill.taught", "payload": {
            "teacher_agent_id": "n01", "student_agent_id": "n02", "skill": "Quick Harvest"}},
        {"type": "agent.skill.taught", "payload": {
            "teacher_agent_id": "n01", "student_agent_id": "n03", "skill": "Quick Harvest"}},
    ])

    assert report.widest_reach == 3
