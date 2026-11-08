"""NAME-01, at the edge: every surface a resident reads says the same thing.

`world/tools/tests/test_the_world_answers_in_names.py` holds the renderer.
This holds that the renderer is CALLED - the failure this project keeps
making. `docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md`: *"a fix that reaches
nobody is indistinguishable from no fix"*, and ENG-16/WIRE-02 are both
instruments that computed the right answer and were wired to nothing.

There are exactly three surfaces the world writes a person onto, and
before this they disagreed:

  * the perception - `Marta Thorne (n07)` since `retrieval_depth._named`;
  * the roster in the persona seed - `Marta Thorne (baker)`, no id at all;
  * every tool result and refusal - `n07`, no name at all.

A resident holding all three had no way to learn they were one person. Run
28b, Silas Vance, verbatim: *"Someone is here - n02"*, then `say(content=
"You there.")` to a neighbour he had lived beside since genesis. Marta was
asked for bread eighteen times and answered *"n07 needs 1 bread, has 0"*.

So: one shape, `Name (id)`, on all three. These tests read what the town
actually hands over - a route response, a brief, a roster line - rather
than asserting that a function exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)


def _runner(tmp_path: Path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))


def _a_named_pair(runner) -> tuple[str, str]:
    """Two residents who share a location, so one can see the other."""
    named = [a for a in runner.world_state.agents if runner.agent_names.get(a)]
    for who in named:
        here = runner.world_state.location_of(who)
        for other in named:
            if other != who and runner.world_state.location_of(other) == here:
                return who, other
    raise AssertionError("no two named residents share a location in this genesis")


@pytest.mark.asyncio
async def test_a_tool_result_leaves_the_world_with_the_names_on_it(tmp_path):
    """Run 28b's mill, through the door the engine actually uses.

    `/agents/{id}/tool_calls` is the only path a tool result takes to a
    resident (`registry.execute` has one other caller in this file and it
    is the resume path). Asserting on the response body rather than on
    `outcome.result` is the whole point: the renderer sits at the edge,
    and a renderer nobody calls is the defect it was written to fix.
    """
    runner = _runner(tmp_path)
    await runner.boot(resume=False)
    client = TestClient(build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics
    ))
    who, other = _a_named_pair(runner)
    name = runner.agent_names[other]

    response = client.post(f"/agents/{who}/tool_calls",
                           json={"tool": "look_around", "input": {}})

    assert response.status_code == 200, response.text
    served = response.json()["result"]
    assert f"{name} ({other})" in served, (
        f"{who} looked around and was handed {other!r} with no name on it. "
        f"That is run 28b's \"Someone is here - n02\": {served}"
    )
    await runner.shutdown()


@pytest.mark.asyncio
async def test_a_refusal_leaves_the_world_with_the_names_on_it(tmp_path):
    """The other half of the door, and the half run 28b lost turns to.

    A refusal is prose, so it goes through `name_mentions` rather than the
    structure walker - two entry points, and a test that only covered the
    result would have left every "n07 needs 1 bread, has 0" exactly as it
    was.
    """
    runner = _runner(tmp_path)
    await runner.boot(resume=False)
    client = TestClient(build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics
    ))
    who, other = _a_named_pair(runner)
    name = runner.agent_names[other]

    # Run 28b's shape exactly, reproduced: a price posted and the goods
    # gone. The refusal `InsufficientInventoryError` writes is a true
    # sentence about a person, built from the id alone - `"n07 needs 1
    # bread, has 0"` - and it is the one this run lost eighteen calls to.
    # A refusal that already names somebody (`NotFoundError` builds its own
    # label) would pass this test whether the fix were here or not.
    runner.world_state.inventory.credit(other, "bread", 1)
    await runner.registry.execute(runner.world_state, other, "post_price",
                                  {"item": "bread", "ask_sim": 15})
    runner.world_state.inventory.debit(other, "bread", 1)

    response = client.post(
        f"/agents/{who}/tool_calls",
        json={"tool": "buy", "input": {"seller_id": other, "item": "bread", "quantity": 1}},
    )

    assert response.status_code == 400, response.text
    said = str(response.json()["detail"])
    assert f"{name} ({other})" in said, (
        f"the refusal is about {name} and numbers them: {said}"
    )
    await runner.shutdown()


@pytest.mark.asyncio
async def test_the_brief_names_the_person_a_debt_is_owed_to(tmp_path):
    """A resident was told every morning that it owed 400 sim to a number.

    `WorldState.debts_of` writes the lender's id, correctly - the ledger,
    the evals and the trace all read those rows. The brief is where it
    becomes something a person reads, and Bram Holloway spent run 28b
    reconstructing the join by hand in his own memory: *"I owe 400 sim to
    n07 (Marta)"*.
    """
    runner = _runner(tmp_path)
    await runner.boot(resume=False)
    borrower, lender = _a_named_pair(runner)
    # The ledger fact a `transfer` tagged as a loan leaves behind - the same
    # row `check_debts` and the brief both read (`WorldState.record_loan`).
    runner.world_state.record_loan(lender, borrower, 400, runner.world_state.tick())

    brief = runner.standing_brief(borrower)

    owed = brief.get("owe") or []
    assert owed, "the borrower's brief says nothing about the loan it just took"
    assert any(runner.agent_names[lender] in str(row.get("to")) for row in owed), (
        f"the brief names the creditor by number only: {owed}"
    )
    await runner.shutdown()


@pytest.mark.asyncio
async def test_the_roster_carries_the_number_beside_the_name(tmp_path):
    """The roster is where the join belongs, and it was the one place
    without it.

    This list is the only thing that tells a resident the whole cast
    exists. It is read before anybody is in the room, which is exactly
    when a resident needs to address somebody who is not - a buyer for
    wheat, a lender, the baker. `Marta Thorne (baker)` could not be turned
    into a call by any tool in the town.
    """
    runner = _runner(tmp_path)
    await runner.boot(resume=False)
    who, other = _a_named_pair(runner)

    entry = runner.roster_entry(other)

    assert entry is not None
    assert f"{runner.agent_names[other]} ({other})" in entry, entry
    assert f"({other})" in runner.town_roster_text(who), (
        "the once-written roster names everybody and numbers nobody, so a "
        "resident cannot address anyone it cannot currently see"
    )
    await runner.shutdown()


@pytest.mark.asyncio
async def test_the_name_the_world_wrote_is_accepted_back(tmp_path):
    """The round trip, end to end, because the two halves are separate code.

    A resident reads `Marta Thorne (n07)` in a result and writes exactly
    that into its next call - it is what it was shown. If the resolver
    refused it, this fix would have replaced one wasted turn with another
    and the run report would blame the resident.
    """
    runner = _runner(tmp_path)
    await runner.boot(resume=False)
    client = TestClient(build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics
    ))
    who, other = _a_named_pair(runner)
    before = runner.ledger.sim_balance(other)

    response = client.post(
        f"/agents/{who}/tool_calls",
        json={"tool": "transfer", "input": {
            "to_agent_id": f"{runner.agent_names[other]} ({other})", "amount_sim": 25,
        }},
    )

    assert response.status_code == 200, response.text
    assert runner.ledger.sim_balance(other) == before + 25
    await runner.shutdown()
