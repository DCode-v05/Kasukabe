"""A small cast describes only the people who are in its world.

Run 26 ran `genesis_6.json`, cut from the full town. Every one of its six
personas kept ties to people who were not cut with them - sixteen opinions,
every one about somebody absent. Lior Jarrah's prompt said "People in
Kasukabe you already know, before today: - Saoirse Nakamura (agent id ...)",
and Lior spent the entire run asking for her. Every answer was "haven't seen
her", because she does not exist in a six-person town. Farid Ravensworth
sent a question about debts to Gideon Novak, who does not exist either. A
`debt` opinion also seeds a real loan (TOOL-18), so a resident could owe sim
to nobody.
"""

from __future__ import annotations

from pathlib import Path

from world.life.genesis import RichGenesis
from world.life.soul_seed import build_persona_prompt
from world.run_genesis import prune_ties_to_people_not_in_the_cast

WORLD = Path(__file__).resolve().parents[1]


def _cast(name: str) -> RichGenesis:
    return RichGenesis.model_validate_json((WORLD / name).read_text(encoding="utf-8"))


def test_run_26s_cast_had_the_defect_before_pruning():
    """Proves the fixture still carries the problem, so the tests below
    pass because of the pruning and not because the data changed."""
    cast = _cast("genesis_6.json")
    present = {a.agent_id for a in cast.agents}
    absent = [
        o for a in cast.agents if a.persona for o in a.persona.opinions
        if o.target_agent_id not in present
    ]
    assert len(absent) >= 16


def test_after_pruning_every_tie_is_to_somebody_present():
    cast = _cast("genesis_6.json")
    counts = prune_ties_to_people_not_in_the_cast(cast)

    present = {a.agent_id for a in cast.agents}
    for agent in cast.agents:
        for opinion in agent.persona.opinions:
            assert opinion.target_agent_id in present, (agent.name, opinion.target_name)
        assert agent.persona.secret_target_agent_id in (None, *present)
    assert counts["opinions"] >= 16


def test_lior_is_no_longer_told_saoirse_lives_here():
    cast = _cast("genesis_6.json")
    prune_ties_to_people_not_in_the_cast(cast)
    lior = next(a for a in cast.agents if a.name == "Lior Jarrah")

    assert "Saoirse Nakamura" not in build_persona_prompt(lior)


def test_no_debt_to_an_absent_lender_survives_to_be_seeded():
    cast = _cast("genesis_6.json")
    prune_ties_to_people_not_in_the_cast(cast)
    present = {a.agent_id for a in cast.agents}

    debts = [
        o for a in cast.agents for o in a.persona.opinions if o.kind == "debt"
    ]
    assert all(o.target_agent_id in present for o in debts)


def test_pruning_twice_changes_nothing_the_second_time():
    cast = _cast("genesis_6.json")
    prune_ties_to_people_not_in_the_cast(cast)

    assert prune_ties_to_people_not_in_the_cast(cast) == {
        "opinions": 0, "debts": 0, "secret_targets": 0,
    }
