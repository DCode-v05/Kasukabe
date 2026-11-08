"""The other half of NAME-01: what the world hands BACK carries the name.

`test_residents_are_named_not_numbered.py` holds the inbound half - a
resident may write "Marta Thorne" where a person parameter wants an id.
That fix shipped after run 28b and left the town speaking two languages
with nothing joining them:

  * the perception said `Marta Thorne (n07)` (`retrieval_depth._named`,
    "both halves, always");
  * the once-written roster said `Marta Thorne (baker)` - a name and a
    trade, no number in it anywhere;
  * every tool result and every refusal said `n07`.

So a token appeared in a resident's context that nothing else it had ever
read attached to a person. Run 28b, Silas Vance at the mill, reading
`look_around` -> `people_here: ["n02"]`:

    "Right. The system is telling me I'm repeating myself... I'm at the
     mill. Someone is here - n02, who..."
    world_say(listener_agent_id="n02", content="You there. I have planks...")

Ainsley Holloway. Same town since genesis, addressed as "You there".
And the commonest failed call of the run, eighteen times:

    buy(seller_id="n07", item="bread") -> "n07 needs 1 bread, has 0"

read by a buyer who had asked for Marta and could not tell the refusal was
about her.

These tests hold the join in the three places the world speaks: a result, a
refusal, and the round trip - because a name the world writes and then
refuses when it is written back would be worse than the number was.
"""

from __future__ import annotations

import pytest

from world.tools import names
from world.tools.errors import ToolError
from world.tools.models import Business
from world.tools.tests.conftest import spawn_agent

HERE = "Kasukabe Farmland"
SHOP = "b_bakery"


async def _neighbours(world) -> None:
    for agent_id in ("n02", "n07", "n13"):
        await spawn_agent(world, agent_id, HERE)
    world.agent_names.update({
        "n02": "Ainsley Holloway", "n07": "Marta Thorne", "n13": "Junia Vance",
    })


@pytest.mark.asyncio
async def test_look_around_names_the_people_it_lists(world, registry):
    """Run 28b's mill, with the fix in.

    `people_here` is the single most-read list in the town and it was a
    list of bare numbers. The assertion is on the NAME, not on the shape:
    a result that carried the id in some new field and still led with
    `n02` would pass a schema check and leave Silas writing "You there".
    """
    await _neighbours(world)
    outcome = await registry.execute(world, "n07", "look_around", {})

    spoken = names.name_the_numbers(world, outcome.result)

    assert "Ainsley Holloway (n02)" in spoken["people_here"], spoken["people_here"]
    assert "Junia Vance (n13)" in spoken["people_here"], spoken["people_here"]


@pytest.mark.asyncio
async def test_the_raw_result_still_holds_the_id(world, registry):
    """The names go on at the EDGE, not in the handler.

    The same result dict is what a program binds a step to
    (`world/tools/program.py`), what the trace stores and what the game
    draws. All three want `n02`. A handler that returned the decorated
    string would have broken `@here.people_here` fed to `say` - a program
    passing "Ainsley Holloway (n02)" into a parameter that then had to
    un-decorate it, which is the same defect wearing the other coat.
    """
    await _neighbours(world)
    outcome = await registry.execute(world, "n07", "look_around", {})

    assert "n02" in outcome.result["people_here"]
    assert not any("(" in person for person in outcome.result["people_here"])


@pytest.mark.asyncio
async def test_a_refusal_names_the_person_it_is_about(world, registry):
    """"n07 needs 1 bread, has 0" - eighteen times in run 28b.

    The shape exactly: a price posted, the goods gone, and a buyer told the
    truth about somebody it cannot recognise.
    """
    await _neighbours(world)
    world.inventory.credit("n13", "bread", 1)
    await registry.execute(world, "n13", "post_price", {"item": "bread", "ask_sim": 15})
    world.inventory.debit("n13", "bread", 1)

    with pytest.raises(ToolError) as refused:
        await registry.execute(
            world, "n02", "buy", {"seller_id": "n13", "item": "bread", "quantity": 1}
        )

    spoken = names.name_mentions(world, str(refused.value))
    assert "Junia Vance (n13)" in spoken, spoken
    assert "n13 " not in spoken.replace("(n13)", ""), (
        f"a bare number survived in a refusal a resident has to act on: {spoken}"
    )


@pytest.mark.asyncio
async def test_what_the_world_said_can_be_said_back_to_it(world, registry):
    """The round trip, and the reason both halves are one module.

    A resident that reads "Ainsley Holloway (n02)" in a result will write
    exactly that into the next call - it is what it was shown. Refusing it
    would be the world contradicting its own vocabulary, and would show up
    in a report as a resident that "used the wrong id" while doing the one
    thing its context taught it.
    """
    await _neighbours(world)
    before = world.ledger.sim_balance("n02")

    await registry.execute(
        world, "n07", "transfer",
        {"to_agent_id": "Ainsley Holloway (n02)", "amount_sim": 150},
    )

    assert world.ledger.sim_balance("n02") == before + 150


@pytest.mark.asyncio
async def test_a_bare_id_still_works(world, registry):
    """Nothing above may cost the town the vocabulary it already had.

    Every scenario, every eval and four runs of trace all address people by
    id. The fix is additive or it is a migration.
    """
    await _neighbours(world)
    before = world.ledger.sim_balance("n02")

    await registry.execute(world, "n07", "transfer", {"to_agent_id": "n02", "amount_sim": 40})

    assert world.ledger.sim_balance("n02") == before + 40


@pytest.mark.asyncio
async def test_the_four_person_params_that_did_not_end_in_agent_id(world, registry):
    """`_is_person_field` matched a suffix, and four fields do not have it.

    `licensee_id`, `student_id`, `employee_id`, `to_owner_id`. Found by
    reading every params model in `world/tools/primitives/`. A resident
    asking to teach "Tobias" was understood by `teach_skill`
    (`student_agent_id`) and refused by `teach_tool` (`student_id`) - the
    same word, the same act, two answers, and no way to tell which tool
    was the fussy one.

    Held as the resolver's own question rather than through six tools,
    because the tools each have their own gates and this is about the
    field name alone.
    """
    await _neighbours(world)
    fields = {
        "licensee_id", "student_id", "employee_id", "to_owner_id",
        "listener_agent_id", "seller_id",
    }

    for field in sorted(fields):
        resolved = names.resolve_person_references(
            world, "n07", "a_tool", fields, {field: "Junia Vance"}
        )
        assert resolved[field] == "n13", (
            f"{field} takes a person and would not take a person's name; a "
            "resident that wrote 'Junia Vance' here spent a turn on a refusal"
        )


@pytest.mark.asyncio
async def test_a_business_id_is_left_alone(world, registry):
    """Only PEOPLE are renamed. A shop already answers to its own name
    (`test_a_business_answers_to_its_own_names.py`), and rewriting
    `business_id` here would give the town two spellings for a shelf."""
    await _neighbours(world)
    world.businesses[SHOP] = Business(
        business_id=SHOP, name="Thorne's Bakery", business_type="bakery",
        owner_agent_id="n07", location_id=HERE,
    )

    spoken = names.name_the_numbers(world, {"business_id": SHOP, "owner_agent_id": "n07"})

    assert spoken["business_id"] == SHOP
    assert spoken["owner_agent_id"] == "Marta Thorne (n07)"


@pytest.mark.asyncio
async def test_an_id_inside_a_longer_token_is_not_a_mention(world, registry):
    """`home:h_n07` is a house, not Marta.

    The walker matches whole tokens only. Without the lookarounds a
    resident's own home id, a business id and a recipe name would all
    sprout somebody's name in the middle of them, and a `move` to what it
    read back would go nowhere.
    """
    await _neighbours(world)

    spoken = names.name_the_numbers(world, {
        "home": "home:h_n07", "recipe": "cook:n07_special", "who": "n07",
    })

    assert spoken["home"] == "home:h_n07"
    assert spoken["recipe"] == "cook:n07_special"
    assert spoken["who"] == "Marta Thorne (n07)"


@pytest.mark.asyncio
async def test_a_name_is_not_written_twice(world, registry):
    """Several refusals build their own "Name (id)" label already.

    `names._label` has done it since the inbound half shipped, and the
    walker runs over the finished sentence. Nesting them would produce
    "Marta Thorne (Marta Thorne (n07))", which reads as two people.
    """
    await _neighbours(world)

    spoken = names.name_mentions(world, "'Marta' could be Marta Thorne (n07).")

    assert spoken == "'Marta' could be Marta Thorne (n07)."


@pytest.mark.asyncio
async def test_a_resident_who_is_gone_is_not_put_back_in_the_room(world, registry):
    """The name map outlives the resident; the town does not.

    `world.agent_names` keeps an entry after a death so a report can still
    say who someone was. Decorating from it alone would answer a resident
    with the name of somebody who is not there to be addressed.
    """
    await _neighbours(world)
    del world.agents["n13"]

    spoken = names.name_the_numbers(world, {"who": "n13", "and": "n07"})

    assert spoken["who"] == "n13"
    assert spoken["and"] == "Marta Thorne (n07)"
