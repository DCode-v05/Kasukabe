"""B7: the town, rendered in plain language (The 250 sections 19.7, 22, 23).

Two things under test, and they matter for different reasons:

  1. The **framing** - "Nothing is shown as a bare number without also
     saying how much to trust it." A confidence label is a claim, so the
     rule that produces it is asserted directly.
  2. The **plumbing** - console route -> WorldAdminClient -> world's
     read-only routers -> back, over a real HTTP round trip through the
     mock transport, the same way every other console test works.
"""

from __future__ import annotations


from game.console.framing import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_MODERATE,
    CONFIDENCE_NONE,
    HIGH_CONFIDENCE_DAYS,
    confidence_for,
    frame_at_risk,
    frame_business,
    frame_household,
    frame_skill_spread,
    frame_treasury,
)
from game.console.tests.conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


# -- the confidence rule ---------------------------------------------------

def test_confidence_is_earned_by_evidence_not_by_drama():
    """A dashboard that says "high confidence" on two days of data is worse
    than one that shows a bare number."""
    assert confidence_for(0) == CONFIDENCE_NONE
    assert confidence_for(1) == CONFIDENCE_LOW
    assert confidence_for(3) == CONFIDENCE_MEDIUM
    assert confidence_for(6) == CONFIDENCE_MODERATE
    assert confidence_for(HIGH_CONFIDENCE_DAYS) == CONFIDENCE_HIGH
    assert confidence_for(500) == CONFIDENCE_HIGH


def test_the_high_label_costs_what_the_spec_quotes():
    """Section 19.7's own example rests on "the last 10 days of income data"."""
    assert HIGH_CONFIDENCE_DAYS == 10
    assert confidence_for(9) != CONFIDENCE_HIGH


def test_confidence_never_decreases_with_more_evidence():
    order = [CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_MODERATE, CONFIDENCE_HIGH]
    ranks = [order.index(confidence_for(d)) for d in range(0, 20)]
    assert ranks == sorted(ranks)


# -- the framing -----------------------------------------------------------

def test_a_household_two_days_out_gets_the_scenario_sentence_verbatim():
    """Section 19.7's own worked example."""
    framed = frame_household(
        {"household_id": "Duval household", "days_of_runway": 2, "earners": 1, "dependents": 2},
        days_observed=10,
    )
    assert framed.question == "Will Duval household make it?"
    assert "If no relative or the town's charity path intervenes in the next 2 days" in framed.answer
    assert "enters hunger" in framed.answer
    assert framed.confidence == CONFIDENCE_HIGH
    assert "last 10 days of income data" in framed.basis
    assert framed.severity == "critical"


def test_a_household_with_dependents_and_nobody_earning_is_always_critical():
    framed = frame_household(
        {"household_id": "hh-x", "days_of_runway": 40, "earners": 0, "dependents": 3},
        days_observed=10,
    )
    assert framed.severity == "critical"
    assert "nobody earning" in framed.answer


def test_a_comfortable_household_is_not_dressed_up_as_a_crisis():
    framed = frame_household(
        {"household_id": "hh-ok", "days_of_runway": 50, "earners": 2, "dependents": 0},
        days_observed=10,
    )
    assert framed.severity == "info"
    assert "covering its own bill" in framed.answer


def test_every_framed_result_carries_its_own_numbers_too():
    """Plain language INSTEAD of the breakdown would be worse, not better."""
    framed = frame_household(
        {"household_id": "hh", "days_of_runway": 3, "earners": 1, "dependents": 1,
         "combined_sim": 500, "daily_bill_sim": 150, "shape": "single-earner with dependents"},
        days_observed=4,
    )
    labels = {b["label"] for b in framed.breakdown}
    assert {"earners", "dependents", "combined balance", "daily bill", "days of runway"} <= labels


def test_a_business_that_cannot_pay_wages_says_so_in_words():
    framed = frame_business(
        {"name": "Kasukabe Grocery", "trend": "cannot make payroll", "profit_sim": -500,
         "by_day": [{}, {}]}
    )
    assert framed.severity == "critical"
    assert "employees are about to be unpaid" in framed.answer
    assert framed.confidence == CONFIDENCE_MEDIUM     # two days of books


def test_a_brand_new_business_is_not_given_a_confident_verdict():
    framed = frame_business({"name": "New Shop", "trend": "too early to say", "by_day": []})
    assert framed.confidence == CONFIDENCE_NONE
    assert "not traded long enough" in framed.answer


def test_a_broke_treasury_is_stated_plainly():
    framed = frame_treasury({"balance_sim": 0}, {"tax_rate_bps": 300}, days_observed=10)
    assert framed.severity == "critical"
    assert "The town is broke" in framed.answer


def test_a_solvent_treasury_quotes_the_rate_that_feeds_it():
    framed = frame_treasury(
        {"balance_sim": 9000, "tax_collected_sim": 5000, "wages_paid_sim": 100,
         "pensions_paid_sim": 0},
        {"tax_rate_bps": 250}, days_observed=10,
    )
    assert "2.5%" in framed.answer
    assert framed.severity == "info"


def test_the_skill_view_answers_why_specialization_happened():
    framed = frame_skill_spread([
        {"skill": "baking", "practitioners": 6,
         "best": {"name": "Dara", "agent_id": "d"}, "average_proficiency": 40.0},
        {"skill": "trading", "practitioners": 2, "best": None, "average_proficiency": 10.0},
    ])
    assert framed.question == "What is this town good at?"
    assert "baking" in framed.answer and "Dara" in framed.answer


def test_an_empty_town_does_not_pretend_to_know_anything():
    assert frame_skill_spread([]).confidence == CONFIDENCE_NONE
    assert frame_at_risk([], 0).confidence == CONFIDENCE_NONE


def test_the_watchlist_headline_counts_only_the_genuinely_urgent():
    framed = frame_at_risk(
        [{"household_id": "a", "days_of_runway": 0}, {"household_id": "b", "days_of_runway": 30}],
        days_observed=10,
    )
    assert "1 household is" in framed.answer
    assert framed.severity == "critical"


# -- the plumbing ----------------------------------------------------------

def test_the_overview_route_answers_all_four_questions(client):
    body = client.get("/api/town/overview", headers=AUTH).json()
    assert body["headline"]["question"] == "Is anyone about to go hungry?"
    assert body["treasury"]["question"] == "Can the town pay for itself?"
    assert body["skills"]["question"] == "What is this town good at?"
    assert body["at_risk"] and body["businesses"]


def test_the_overview_derives_confidence_from_the_books_not_a_constant(client):
    """Claiming a confidence without knowing how long the town has run is
    exactly the failure section 19.7 warns against."""
    body = client.get("/api/town/overview", headers=AUTH).json()
    assert body["days_observed"] == 2          # the fake's business has two days of books
    assert body["headline"]["confidence"] == CONFIDENCE_MEDIUM


def test_the_destitute_household_surfaces_as_critical(client):
    body = client.get("/api/town/overview", headers=AUTH).json()
    duval = [h for h in body["at_risk"] if "Duval" in h["question"] or "duval" in h["question"]]
    assert duval and duval[0]["severity"] == "critical"


def test_the_failing_business_surfaces_as_critical(client):
    body = client.get("/api/town/overview", headers=AUTH).json()
    assert body["businesses"][0]["severity"] == "critical"


def test_households_route_frames_every_household(client):
    rows = client.get("/api/town/households", headers=AUTH).json()
    assert len(rows) == 2
    assert all("question" in r and "confidence" in r for r in rows)


def test_terms_and_wealth_are_proxied(client):
    terms = client.get("/api/town/terms", headers=AUTH).json()
    assert terms[0]["pm_name"] == "Dara"
    assert "richest" in client.get("/api/town/wealth", headers=AUTH).json()


def test_every_town_route_needs_the_operator_token(client):
    for path in ("/api/town/overview", "/api/town/households", "/api/town/wealth", "/api/town/terms"):
        assert client.get(path).status_code == 401, path
