"""The scripted control bakery can act, and its owner is not a resident.

EVAL-15. `build_controls` passed `owner_id=business.business_id`, so the
control bakery acted as `cb01` - which is not an agent - and every
`registry.execute` it made was refused at the actor gate. Run 26 logged 151
refused `post_price` calls: the baseline the whole R1-R7 comparison is
measured against never did anything. `test_control_bakery.py` passed the
whole time because it spawned a real owner agent, which production never
created.

The owner lives in `world_state.control_actors`, not `agents`. DESIGN
section 10 makes control businesses "owner-less by design", and everything
that counts, bills, houses, elects or wakes residents iterates `agents` - so
a scripted owner in there would be a citizen who votes, owes upkeep and goes
dormant with no engine behind it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.life.models import ControlBusiness
from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


@pytest_asyncio.fixture
async def runner(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    runner.genesis.control_businesses = [ControlBusiness(
        business_id="cb01", name="Ration Bakery", business_type="bakery",
        pricing_strategy="cost_plus_10pct", starting_sim_balance=20000,
    )]
    runner._controls = None
    return runner


@pytest.mark.asyncio
async def test_the_control_bakery_actually_posts_its_price(runner):
    bakery = runner.build_controls()[0]

    fired = await bakery.act(runner.world_state, runner.registry, tick=36)

    assert "post_price" in fired, bakery.refusals
    assert runner.world_state.posted("cb01", "bread") is not None


@pytest.mark.asyncio
async def test_its_owner_is_not_the_business_id_and_not_a_resident(runner):
    residents_before = set(runner.world_state.agents)

    bakery = runner.build_controls()[0]

    assert bakery.owner_id != bakery.business_id
    assert bakery.owner_id in runner.world_state.control_actors
    assert bakery.owner_id not in runner.world_state.agents
    assert set(runner.world_state.agents) == residents_before, (
        "standing up a control added somebody to the population"
    )


@pytest.mark.asyncio
async def test_the_business_it_runs_is_owned_by_that_owner(runner):
    bakery = runner.build_controls()[0]

    business = runner.world_state.businesses["cb01"]
    assert business.owner_agent_id == bakery.owner_id
    assert runner.world_state.location_of(bakery.owner_id) == business.location_id


@pytest.mark.asyncio
async def test_building_the_controls_twice_is_harmless(runner):
    """A resumed world already has the account and the business row; only
    the in-memory actor has to be put back."""
    first = runner.build_controls()[0]
    runner._controls = None

    second = runner.build_controls()[0]

    assert second.owner_id == first.owner_id
    assert list(runner.world_state.control_actors) == [first.owner_id]
