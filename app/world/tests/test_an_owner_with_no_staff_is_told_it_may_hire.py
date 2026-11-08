"""An owner learned wages exist only once somebody already worked for them.

The employer sentence sat inside `if staff:`. So `set_wage` and `pay_wage`
were named to an owner who already had staff, and nothing anywhere named
`hire_employee` - there was no way to acquire the staff that unlocked the
telling.

Four of the six shops in `genesis_50_town.json` open with nobody employed
(the cafe and the pharmacy have one each), so most owners were told neither
verb. ECON-5 has failed
every run for want of it: **0 hires, 0 wage payments**, while a miller told a
neighbour "come to the mill and we can count a specific task against what the
till can pay" and had no verb in front of her to do it with.

GOV-3 is the precedent that naming a verb in a resident's OWN perception moves
behaviour: nine words in a Prime Minister's brief moved `set_tax_rate` after
twenty-three runs of nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from world.retrieval_depth import format_standing_brief

APP = Path(__file__).resolve().parents[2]


def _brief(staff: list[str]) -> dict:
    return {"my_business": [{
        "business_id": "b_mill", "name": "Vance Mill",
        "till_sim": 5000, "stock": {"flour": 24}, "staff": staff,
    }]}


def test_an_owner_with_nobody_employed_is_told_it_may_hire() -> None:
    said = format_standing_brief(_brief(staff=[])) or ""

    assert "hire_employee" in said, "an owner with no staff is never told it may hire"


def test_it_does_not_name_verbs_that_cannot_apply_yet() -> None:
    """`set_wage` and `pay_wage` stay out until there is somebody to pay.

    test_an_owner_is_told_it_has_a_shop_and_may_pay made that call before this
    one existed, and it is right: a verb named where it cannot apply is noise,
    and this brief is read every tick by every owner in the town. The gap was
    never that wages went unmentioned - it was that HIRING did.
    """
    said = format_standing_brief(_brief(staff=[])) or ""

    assert "set_wage" not in said and "pay_wage" not in said


def test_an_owner_who_has_staff_still_gets_the_original_sentence() -> None:
    said = format_standing_brief(_brief(staff=["Milo Quarshie"])) or ""

    assert "Milo Quarshie works for you." in said
    assert "set_wage" in said and "pay_wage" in said


def test_it_says_what_an_employer_MAY_do_never_that_it_should() -> None:
    """The standing rule, and why `vote` has never been worded as an
    instruction either. A brief that tells a resident what to do is a script,
    not a town."""
    said = format_standing_brief(_brief(staff=[])) or ""

    assert "may" in said.lower()
    for imperative in ("you must", "you should", "hire someone", "go and hire"):
        assert imperative not in said.lower(), f"the brief instructs: {imperative!r}"


def test_the_cast_this_matters_for_really_does_open_unstaffed() -> None:
    """If genesis ever seeds staff everywhere, this branch stops mattering and
    the test should say so rather than passing for the wrong reason."""
    g = json.loads((APP / "world" / "genesis_50_town.json").read_text())
    shops = g["small_businesses"]
    unstaffed = [b for b in shops if not (b.get("employee_agent_ids") or [])]

    assert unstaffed, "every business now opens with staff - re-read this test's premise"
    assert len(unstaffed) * 2 >= len(shops), (
        f"only {len(unstaffed)} of {len(shops)} shops open unstaffed - this branch "
        "matters less than the docstring claims"
    )
