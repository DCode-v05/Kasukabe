"""Evaluation spec section 6: the report has to carry the new economy.

Section 6 lists the sections `run_report.py` gains, each reading the trace
only. Four were missing, and each hides a whole part of the spec:

  Prices  - E1 to E3 have nowhere to be read
  Stakes  - S1 to S4: tax and upkeep charged, energy bought, dormancies
  Wages   - S6 and S7: promised, paid, the gap, complaints
  Business - R1 to R7 beside the scripted control

A measure with no surface is a measure nobody reads, which is the same
defect as a mechanism with no call site: it exists, it is correct, and it
changes nothing. The town now has posted prices, wages, energy purchases
and a control bakery, and a reader of the report could not tell.

Every figure names the table it came from, per section 6's last line.
"""

from __future__ import annotations

from world.run_report import prices_section, stakes_section, wages_section


def test_prices_reports_the_first_post_and_the_spread() -> None:
    """E1 is the tick of the first posted price; E2 is whether the spread
    across shops falls, which needs the per-shop asks printed.

    The rows below are `economy.price.posted`'s real payload keys. They
    used to be `business_id` and `price_sim` - names the event has never
    carried - so this test and `prices_section` agreed with each other and
    both disagreed with the world, and run 25's price table printed
    `| 42 | None | bread | None |` over eleven real posts.

    A test that invents its own input can encode the same wrong assumption
    the code does. `world/tools/tests/test_the_price_table_reads_a_real_
    price.py` is the companion to this one and takes its payload from a
    real `post_price` call for exactly that reason.
    """
    rows = [
        {"tick": 41, "poster_id": "n07", "holder_id": "b_bakery",
         "holder_kind": "business", "item": "bread", "ask_sim": 30, "bid_sim": None},
        {"tick": 44, "poster_id": "n13", "holder_id": "b_grocery",
         "holder_kind": "business", "item": "bread", "ask_sim": 24, "bid_sim": 18},
    ]
    text = "\n".join(prices_section(rows))
    assert "41" in text, "the tick of the first posted price is E1 itself"
    assert "bread" in text and "30" in text and "24" in text
    assert "b_bakery" in text or "Bakery" in text
    # E2 needs both sides when a shop quotes both: a spread is the finding.
    assert "18" in text


def test_prices_says_plainly_when_nobody_posted_one() -> None:
    """Zero posted prices is a finding, not an empty table - it is what
    every run before this one produced."""
    text = "\n".join(prices_section([]))
    assert "no" in text.lower() or "nobody" in text.lower()


def test_stakes_reports_what_was_charged_and_what_was_bought() -> None:
    """S1 is gated and halts the run if a day boundary passes uncharged,
    so the report must show it was charged, per household."""
    text = "\n".join(stakes_section(
        upkeep=[{"day": 1, "agent_id": "n01", "amount": 14}],
        head_tax=[{"day": 1, "agent_id": "n01", "amount": 6}],
        energy=[{"tick": 50, "agent_id": "n01", "sim_amount": 20000, "usd_cents": 200}],
        dormancies=[],
    ))
    assert "upkeep" in text.lower() and "14" in text
    assert "head tax" in text.lower() and "6" in text
    assert "20000" in text or "20,000" in text
    assert "$2.00" in text or "200" in text


def test_stakes_names_a_run_that_never_reached_a_day_boundary() -> None:
    """Evidence rule 10: "A figure from a run that never reached a night
    is a morning figure. Label it so." Nothing charged is not the same as
    nothing owed."""
    text = "\n".join(stakes_section(upkeep=[], head_tax=[], energy=[], dormancies=[]))
    assert "day boundary" in text.lower() or "morning" in text.lower()


def test_dormancies_are_reported_with_their_cause() -> None:
    text = "\n".join(stakes_section(
        upkeep=[], head_tax=[], energy=[],
        dormancies=[{"agent_id": "n03", "tick": 60, "cause": "wallet"}],
    ))
    assert "n03" in text and "wallet" in text


def test_wages_reports_promised_paid_and_the_gap() -> None:
    """S6 is gated on direction: promises kept should be a majority, and
    the gap is the number that says whether they were."""
    text = "\n".join(wages_section(
        promised=[{"employer": "n07", "employee": "n03", "amount": 40, "tick": 42}],
        paid=[{"employer": "n07", "employee": "n03", "amount": 40, "tick": 55}],
    ))
    assert "40" in text
    assert "1 of 1" in text or "100%" in text


def test_an_unpaid_promise_is_the_headline_not_a_footnote() -> None:
    text = "\n".join(wages_section(
        promised=[{"employer": "n07", "employee": "n03", "amount": 40, "tick": 42}],
        paid=[],
    ))
    assert "0 of 1" in text or "unpaid" in text.lower()


def test_every_section_names_where_its_figures_came_from() -> None:
    """Section 6's last line: "Every figure names the table and query it
    came from." A number with no provenance cannot be checked, and this
    project has published two that turned out to be wrong."""
    for lines in (
        prices_section([]),
        stakes_section(upkeep=[], head_tax=[], energy=[], dormancies=[]),
        wages_section(promised=[], paid=[]),
    ):
        text = "\n".join(lines).lower()
        assert "transactions" in text or "events" in text or "trace" in text, (
            f"a section with no stated source: {text[:120]}"
        )


def test_the_sections_are_wired_into_the_report_itself() -> None:
    """Three formatters nobody calls would be the exact failure this
    project keeps finding, and an especially poor one to commit inside the
    reporting code."""
    import inspect

    from world import run_report

    body = inspect.getsource(run_report.build)
    for name in ("prices_section", "stakes_section", "wages_section"):
        assert name in body, f"{name} is written and never called"
