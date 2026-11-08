"""The baseline the residents are measured against actually runs.

`EVAL-15`. DESIGN section 10 puts ten rule-based businesses in the town
as a control - "no LLM" - so an agent-run business has something to be
compared with. `control_bakery.py` implements one completely: six rules,
a refusal taxonomy for R7, its own tests. Its `tick()` docstring says it
"is called once per world tick by the runner, in the same place
`dispatch_due_agents` would have spent a model call".

Nothing called it. R1-R7 have never been computed on any run, so every
claim about what an agent-run business achieves is a claim with no
baseline underneath it.

This is the ninth complete-and-unwired mechanism in this review, and by
some distance the most consequential for the research: the others lost
records, this one loses the comparison the records were for.

The finding's other half - `test_it_always_answers_a_directed_question`
failing on the review's checkout, suspected to be the
`listener`-vs-addressee confusion of EVAL-11 - passes here now.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.control_bakery import ControlBakery
from world.life.models import ControlBusiness
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest_asyncio.fixture
async def runner(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    return runner


def _declare(runner, *businesses: ControlBusiness) -> None:
    runner.genesis.control_businesses = list(businesses)
    runner._controls = None


def _bakery(business_id: str = "cb-bakery") -> ControlBusiness:
    return ControlBusiness(
        business_id=business_id, name="Control Bakery",
        business_type="bakery", pricing_strategy="cost_plus",
        starting_sim_balance=2000,
    )


@pytest.mark.asyncio
async def test_a_declared_bakery_becomes_a_running_control(runner) -> None:
    """The finding: the class existed and nothing built one."""
    _declare(runner, _bakery())
    controls = runner.build_controls()
    assert len(controls) == 1
    assert isinstance(controls[0], ControlBakery)


@pytest.mark.asyncio
async def test_the_tick_gives_it_its_turn(runner) -> None:
    """`ControlBakery.act` is the thing with no caller. This is the caller."""
    _declare(runner, _bakery())
    await runner.act_the_controls(tick=1)      # must not raise

    control = runner._controls[0]
    assert control.actions or control.refusals, (
        "EVAL-15: the control took its turn and neither acted nor refused, "
        "so R7's failure taxonomy has nothing in it either way"
    )


@pytest.mark.asyncio
async def test_it_is_built_once_and_keeps_its_memory(runner) -> None:
    """The rules turn on what it posted today and who it has paid through.
    Rebuilding per tick would reset exactly that."""
    _declare(runner, _bakery())
    await runner.act_the_controls(tick=1)
    first = runner._controls[0]
    await runner.act_the_controls(tick=2)
    assert runner._controls[0] is first


@pytest.mark.asyncio
async def test_a_control_that_fails_does_not_end_the_tick(runner, monkeypatch) -> None:
    """A control that can end the run is a control that decides what the
    TREATMENT's data looks like - the same rule `act()` follows inside
    itself, for the same reason."""
    _declare(runner, _bakery())
    runner._controls = None
    runner.build_controls()

    class _Exploding:
        business_id = "cb-boom"

        async def act(self, *_a, **_kw):
            raise RuntimeError("the oven exploded")

    runner._controls = [_Exploding()]
    assert await runner.act_the_controls(tick=1) == []      # must not raise


@pytest.mark.asyncio
async def test_a_genesis_with_no_controls_is_not_an_error(runner) -> None:
    """`genesis_20.json` declares none, and every run so far used it."""
    _declare(runner)
    assert runner.build_controls() == []
    assert await runner.act_the_controls(tick=1) == []


@pytest.mark.asyncio
async def test_an_unscripted_control_is_reported_not_skipped(runner, caplog) -> None:
    """Only the bakery has a script. A genesis naming ten controls yields
    one runner and nine inert declarations - nine baselines nothing is
    compared against, which is this same finding one layer down."""
    import logging

    _declare(runner, _bakery(), ControlBusiness(
        business_id="cb-smithy", name="Control Smithy",
        business_type="smithy", pricing_strategy="cost_plus",
        starting_sim_balance=2000,
    ))
    with caplog.at_level(logging.INFO):
        controls = runner.build_controls()

    assert len(controls) == 1
    assert "cb-smithy" in caplog.text, (
        "EVAL-15: a declared control with no script is silently dropped, so "
        "a genesis naming ten reads as ten baselines and provides one"
    )


@pytest.mark.asyncio
async def test_the_tick_loop_really_calls_it(runner) -> None:
    """The mechanism existing is not the mechanism running - nine times
    over in this review, and this is the one that costs the comparison.

    This drives `dispatch_due_agents` for real rather than grepping
    run_genesis.py for the call. The grep is what this test used to do,
    and it is why the first version of this wiring shipped BROKEN: the
    call was there, spelled `act_the_controls(timestamp.tick_index)`, and
    `timestamp` is a local of the nested `one()` - so every tick of every
    run raised NameError before any control acted. The source said the
    mechanism was wired. Running it said otherwise.
    """
    from world.core.clock import timestamp_for
    from world.core.engine import TickResult

    _declare(runner, _bakery())

    class Outcome:
        succeeded = True
        error = None
        cost_cents = 0

        def __getattr__(self, name):
            return None

    class SpyGateway:
        def __getattr__(self, name):
            async def _any(*a, **k):
                return Outcome()
            return _any

    runner.gateway = SpyGateway()

    # No due residents at all: a control is the baseline the residents are
    # measured against, so it takes its turn whether or not anyone was due.
    await runner.dispatch_due_agents(TickResult(tick=timestamp_for(60), due_agents=[]))

    assert runner._controls, (
        "EVAL-15: a full tick went by and no control was even built, so "
        "R1-R7 stay uncomputed"
    )
    control = runner._controls[0]
    assert control.actions or control.refusals, (
        "EVAL-15: the tick loop reached the control and it neither acted nor "
        "refused"
    )
