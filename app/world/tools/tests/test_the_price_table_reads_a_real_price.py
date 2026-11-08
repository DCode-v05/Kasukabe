"""EVAL-01 one layer in: the report read the right EVENT and the wrong FIELDS.

`test_report_reads_events_the_world_emits.py` checks that every event type
`run_report` reads is a type the world emits. It has passed since it was
written, and it passed all the way through run 25 - whose price table still
printed

    | 42 | None | bread | None |

on eleven real posts, because `prices_section` read `business_id` and
`price_sim` from a payload whose keys are `holder_id`, `holder_kind`,
`poster_id`, `item`, `ask_sim` and `bid_sim`. A type-level guard cannot see
a field-level mismatch, and both failures look the same from outside: a
confident table full of nothing, over a town that was trading.

So this test does not match strings against source. It calls the real
`post_price`, takes the real `economy.price.posted` payload the world
emitted, and hands it to the real section function - the whole path the
report takes, in miniature.
"""

from __future__ import annotations

import json

import pytest

from world.run_report import prices_section

from .conftest import spawn_agent


def _payloads(outcome, event_type: str, tick: int) -> list[dict]:
    """Every payload of one type this call emitted, shaped as the report
    reads them: `_events()` merges the row's `tick` column into the
    payload dict, so the section function sees one flat mapping."""
    out = []
    for event in outcome.events:
        if event.type != event_type:
            continue
        payload = event.payload
        row = json.loads(payload) if isinstance(payload, str) else dict(payload)
        row.setdefault("tick", tick)
        out.append(row)
    return out


@pytest.mark.asyncio
async def test_the_price_table_renders_what_post_price_actually_emits(
    world, registry
):
    await spawn_agent(world, "n01", "street", age_years=40.0)
    world.inventory.credit("n01", "planks", 3)

    outcome = await registry.execute(
        world, "n01", "post_price", {"item": "planks", "ask_sim": 300}
    )

    posted = _payloads(outcome, "economy.price.posted", world.tick())
    assert posted, "post_price emitted no economy.price.posted"

    rendered = "\n".join(prices_section(posted))

    # The failure this exists for: a column read from a key that is not
    # there renders as the string "None", once per missing field per row.
    assert "None" not in rendered, (
        "the price table read a field the payload does not carry.\n"
        f"payload keys: {sorted(posted[0])}\n\n{rendered}"
    )
    assert "planks" in rendered
    assert "300" in rendered
    assert "n01" in rendered


@pytest.mark.asyncio
async def test_a_price_posted_by_a_person_is_not_called_a_shop(world, registry):
    """`holder_kind` is load-bearing, not decoration.

    Run 25's residents posted as PEOPLE as often as through a business -
    Silas Vance hawking planks in the street at 300, Rosalind Vance
    selling her own wheat at 10. Folding both into a "Shop" column loses
    the difference between a shop's price list and a resident trading on
    their own account, which is most of what the early economy was.
    """
    await spawn_agent(world, "n02", "street", age_years=40.0)
    world.inventory.credit("n02", "wheat", 5)

    outcome = await registry.execute(
        world, "n02", "post_price", {"item": "wheat", "ask_sim": 10}
    )

    posted = _payloads(outcome, "economy.price.posted", world.tick())

    rendered = "\n".join(prices_section(posted))
    assert "person" in rendered, rendered


@pytest.mark.asyncio
async def test_a_two_sided_quote_shows_both_sides(world, registry):
    """A resident who will buy at 60 and sell at 75 has posted a SPREAD.

    Run 25's grocer did exactly that on bread and the table had no column
    to say so, so the one genuinely interesting price in the run - a
    retail margin over the bakery's 50 - was invisible.
    """
    await spawn_agent(world, "n03", "street", age_years=40.0)
    world.inventory.credit("n03", "bread", 4)

    outcome = await registry.execute(
        world, "n03", "post_price", {"item": "bread", "ask_sim": 75, "bid_sim": 60}
    )

    posted = _payloads(outcome, "economy.price.posted", world.tick())

    rendered = "\n".join(prices_section(posted))
    assert "75" in rendered and "60" in rendered, rendered
