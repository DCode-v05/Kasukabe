"""The L1 harness shows a model the prompt a resident is actually given.

`EVAL-06`. A harness that sends a prompt no resident is ever given
measures nothing about the town, and this one differed from the real
thing in three ways:

  * the system prompt was two parts - persona, then the soul seed - and
    `run_genesis` writes THREE: persona, the town roster, then the soul
    seed. That third part was moved into the seed by `CORE-16`, to stop
    the standing brief re-sending it every tick, and this harness was not
    updated with it. So from that change onward the harness measured a
    resident who did not know anybody else in the town existed - and the
    roster exists precisely because a resident without it reaches for
    names off the map's buildings (Tobias hailing "Harvey" and
    "Isabella", Bram walking to "sh_silva", none of them in the run).
  * `self_brief["name"]` was the scenario's `resident` field - a unit id
    like `n09` - while the run says "You are Corwin Thorne, the town's
    forester".

The ordering half of this was already understood and is already pinned by
`test_the_system_prompt_is_byte_identical_to_persona_dot_txt`: composed
the other way round, the harness scored eight fewer scenarios and `trade`
fell from 80% reach to 40%, because a prompt ending on "Internally, you
are generous but proud" leaves a model composing a character and one
ending on the law of the town leaves it acting. That test pins the FIRST
and LAST part. Nothing pinned the middle, which is how a whole section
went missing without a single test noticing.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from world.evals.verb_reach import soul_seed_for
from world.run_genesis import GenesisRunner

APP = Path(__file__).resolve().parents[2]
RESIDENT = "n09"


@pytest.fixture(scope="module")
def seed() -> str:
    return soul_seed_for(RESIDENT)


def test_the_harness_has_a_persona_at_all(seed: str) -> None:
    """The first draft sent no system prompt, and five scenarios scored 0%
    by calling nothing - which is what a tool-using model does with a bare
    situation and no sense of who it is."""
    assert len(seed) > 500, seed[:200]
    assert "You are" in seed or "you are" in seed


def test_it_carries_the_town_roster(seed: str) -> None:
    """The part CORE-16 moved into the seed and this harness lost."""
    assert "THE PEOPLE OF THIS TOWN" in seed, (
        "EVAL-06: the harness prompt has no roster, so every scenario is "
        "scored against a resident who does not know anybody else exists - "
        "and `trade`, `sell` and `teach_tool` are all about somebody else"
    )
    assert "The rest of the town:" in seed


def test_the_roster_names_real_residents(seed: str) -> None:
    """A roster of ids would be the `n09` bug in a second place.

    This used to refuse an id ANYWHERE in the roster, which was the right
    guard for the failure it was written against - `roster_entry` returning
    `n06` where the run says "Wren Thorne". It is the wrong guard for the
    roster the town now writes, `Wren Thorne (n06), farmer`, and the
    difference is the whole of NAME-01: an id INSTEAD of a name is the bug,
    an id BESIDE one is the fix. Run 28b had the strict version of this
    everywhere and residents still could not address each other, because
    the perception said `Marta Thorne (n07)`, the roster said `Marta Thorne
    (baker)` and every tool result said `n07`, and nothing joined them.

    So the guard is narrowed rather than dropped: a bare id is still
    refused, and every id must be bracketed behind a name.
    """
    roster = seed[seed.index("THE PEOPLE OF THIS TOWN"):]
    roster = roster[: roster.index("\n\n")] if "\n\n" in roster else roster

    bare = re.findall(r"(?<!\()\bn\d{2}\b(?!\))", roster)
    assert not bare, (
        f"EVAL-06: the roster lists unit ids rather than names: {bare} in "
        f"{roster[:200]}"
    )

    labelled = re.findall(r"([A-Z][A-Za-z'-]+)\s+\((n\d{2})\)", roster)
    assert len(labelled) == len(re.findall(r"\bn\d{2}\b", roster)), (
        f"EVAL-06: an id in the roster has no name in front of it: {roster[:200]}"
    )


def test_the_roster_says_nothing_about_where_anyone_is(seed: str) -> None:
    """Names and trades. A roster carrying positions hands every resident
    a live map of the town for free, which is what perception is for."""
    roster = seed[seed.index("THE PEOPLE OF THIS TOWN"):]
    roster = roster[: roster.index("\n\n")] if "\n\n" in roster else roster
    for word in ("tile", "standing at", "location_id"):
        assert word not in roster.lower()


def test_the_three_parts_are_in_the_runners_order(seed: str) -> None:
    """Persona, roster, law - the order `run_genesis` writes persona.txt
    in, and the order the ordering finding says is worth eight scenarios."""
    roster_at = seed.index("THE PEOPLE OF THIS TOWN")
    assert roster_at > 200, "the roster comes first, before any persona"
    assert roster_at < len(seed) - 400, "the roster is last, after the law"


def test_it_has_the_same_number_of_parts_as_the_real_seed(seed: str) -> None:
    """Bound to the runner's own composition rather than to a literal
    three, so a fourth part added there fails here instead of silently
    going unmeasured - which is exactly what happened with the roster."""
    source = (APP / "world" / "run_genesis.py").read_text(encoding="utf-8")
    block = re.search(r'persona_text = "[^"]*"\.join\(\[(.*?)\]\)', source, re.S)
    assert block, "run_genesis no longer composes persona.txt as a list"
    parts = [
        line.strip().rstrip(",")
        for line in block.group(1).strip().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(parts) == 3, (
        f"EVAL-06: the resident's seed now has {len(parts)} parts and this "
        f"test knows about 3 - check the harness composes all of them: {parts}"
    )


def test_the_brief_uses_a_name_not_a_unit_id() -> None:
    from world.evals.verb_reach import _self_brief_for

    class _Scenario:
        resident = RESIDENT
        location = "Kasukabe Market Square"
        sim = 1800
        inventory: dict = {}

    brief = _self_brief_for(_Scenario())
    assert brief["name"] != RESIDENT, (
        "EVAL-06: the brief introduces the resident as a unit id, while "
        "the run says \"You are Corwin Thorne, the town's forester\""
    )
    assert brief.get("occupation"), "the brief carries no trade"


def test_an_unknown_resident_still_gets_a_brief() -> None:
    """A scenario naming somebody outside the genesis file must degrade to
    the id rather than raise - a harness that crashes on an unrecognised
    scenario is worse than one that names it plainly."""
    from world.evals.verb_reach import _self_brief_for

    class _Scenario:
        resident = "nobody-by-that-name"
        location = "x"
        sim = 0
        inventory: dict = {}

    assert _self_brief_for(_Scenario())["name"] == "nobody-by-that-name"


def test_the_harness_still_says_what_it_cannot_do() -> None:
    """The remaining half of EVAL-06 - `heard_events=[]`, with speech
    appended as text instead of going through the real hearing path - is
    NOT fixed here. It must stay written down rather than quietly
    forgotten now that the other parts are closed."""
    source = inspect.getsource(
        __import__("world.evals.verb_reach", fromlist=["render_situation"]).render_situation
    )
    assert "Said here just now" in source, (
        "the text-appended speech path changed; if it now goes through "
        "perception, say so and delete this test"
    )
