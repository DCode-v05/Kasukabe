"""The land-expansion pipeline has an entry point from inside the town.

`GROW-12`. `petition` is GOVERNMENT-namespace and requires a sitting
seat (`civic.py`, plus `file_petition`'s own `is_sitting` check). Seats
start vacant by design - the first PM is whoever wins the first election.
But no run this project has made has ever crossed a first election, so
every seat-gated verb has refused every resident in every run, and
`petition` is the ONLY in-world route into the land-expansion pipeline.
"The town physically grows" (DESIGN section 1) has therefore never once
been reachable from inside the town.

The review offers two fixes: seed a caretaker government, or make
`petition` PUBLIC with government endorsement as a second step. The
second contradicts DESIGN section 1 - "government petitions the
President" - so this takes the first.

IT IS OFF BY DEFAULT, and that is the point. Appointing a PM nobody
elected is a research decision: DESIGN section 10 measures regime
transitions and an authoritarianism index off terms in office. So a
genesis file has to name `caretaker_pm_agent_id` before anything changes,
the resulting state is marked `caretaker=True`, and `terms_served` stays
empty - an appointment is not a term, and nothing that counts political
history should mistake it for one.

Two gates had to move, not one. Changing only the `ToolSpec` would have
left `file_petition` raising `NotSittingGovernment` one line later - the
same shape as the fold that resolved at the tool boundary and never
reached the storage key.
"""
from __future__ import annotations

import pytest

from world.charter.engine import create_genesis_charter_engine
from world.charter.models import GovernmentState


# ── the default is unchanged ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seats_are_still_vacant_by_default() -> None:
    """Every run so far was made this way. A default that seeded a
    caretaker would silently change what the next run measures."""
    engine = await create_genesis_charter_engine(genesis_tick=0)
    state = await engine.store.get_government_state()

    assert state.pm_agent_id is None
    assert state.minister_agent_ids == []
    assert state.caretaker is False


@pytest.mark.asyncio
async def test_a_named_caretaker_sits() -> None:
    engine = await create_genesis_charter_engine(
        genesis_tick=0, caretaker_pm_agent_id="a0007"
    )
    state = await engine.store.get_government_state()

    assert state.pm_agent_id == "a0007"
    assert state.is_sitting("a0007") is True, (
        "GROW-12: the caretaker does not read as sitting, so every "
        "seat-gated verb still refuses and the pipeline is still shut"
    )


# ── an appointment is not a term ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_caretaker_has_served_no_terms() -> None:
    """DESIGN section 10 counts terms in office for the regime-transition
    and authoritarianism measures. Counting an appointment as a term would
    put a fabricated political history into the research record."""
    engine = await create_genesis_charter_engine(
        genesis_tick=0, caretaker_pm_agent_id="a0007"
    )
    state = await engine.store.get_government_state()

    assert state.terms_served == {}, (
        "GROW-12: the caretaker was credited with a term nobody voted for"
    )


@pytest.mark.asyncio
async def test_the_state_says_it_is_a_caretaker() -> None:
    """Without the flag, a seeded PM and an elected one are the same row,
    and no later reader can tell the town's politics from its setup."""
    engine = await create_genesis_charter_engine(
        genesis_tick=0, caretaker_pm_agent_id="a0007"
    )
    state = await engine.store.get_government_state()

    assert state.caretaker is True
    assert state.is_caretaker_seat("a0007") is True
    assert state.is_caretaker_seat("a0008") is False


def test_an_elected_government_is_not_a_caretaker() -> None:
    """The flag must not leak onto a real government. `caretaker`
    defaults False, so an election that writes a fresh state clears it."""
    elected = GovernmentState(
        pm_agent_id="a0007", minister_agent_ids=["a1"], terms_served={"a0007": 1}
    )
    assert elected.caretaker is False
    assert elected.is_caretaker_seat("a0007") is False


# ── both gates, not one ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_caretaker_can_actually_file_a_petition() -> None:
    """The end-to-end claim, and the reason the ToolSpec alone was not
    enough: `file_petition` checks `is_sitting` itself."""
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX

    engine = await create_genesis_charter_engine(
        genesis_tick=0, caretaker_pm_agent_id="a0007"
    )
    petition = await engine.land_petitions.file_petition(
        filed_by_agent_id="a0007",
        title="North strip",
        description="The town needs farmland.",
        tile_count_requested=40,
        location=LOCATION_GOVERNMENT_COMPLEX,
        now_tick=10,
    )
    assert petition.petition_id


@pytest.mark.asyncio
async def test_a_resident_with_no_seat_still_cannot_petition() -> None:
    """The control. If the caretaker change had opened filing to
    everyone, DESIGN section 1's "government petitions the President"
    would be gone and this would pass silently."""
    from world.charter.errors import NotSittingGovernment
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX

    engine = await create_genesis_charter_engine(
        genesis_tick=0, caretaker_pm_agent_id="a0007"
    )
    with pytest.raises(NotSittingGovernment):
        await engine.land_petitions.file_petition(
            filed_by_agent_id="a0099",
            title="My own strip",
            description="I would like some land.",
            tile_count_requested=40,
            location=LOCATION_GOVERNMENT_COMPLEX,
            now_tick=10,
        )


@pytest.mark.asyncio
async def test_with_no_caretaker_nobody_can_petition_at_all() -> None:
    """The finding itself, as a test. This is the state every run to date
    has been in."""
    from world.charter.errors import NotSittingGovernment
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX

    engine = await create_genesis_charter_engine(genesis_tick=0)
    with pytest.raises(NotSittingGovernment):
        await engine.land_petitions.file_petition(
            filed_by_agent_id="a0007",
            title="North strip",
            description="The town needs farmland.",
            tile_count_requested=40,
            location=LOCATION_GOVERNMENT_COMPLEX,
            now_tick=10,
        )


# ── the parameter reaches the store ──────────────────────────────────────

def test_the_caretaker_is_threaded_all_the_way_from_genesis() -> None:
    """A parameter that stops at the first boundary is a fold that never
    reaches the storage key. Genesis declares it, the runner reads it,
    build_world takes it, the charter factory takes it, seed_genesis
    writes it."""
    import inspect
    from pathlib import Path

    from world.charter import engine as charter_engine
    from world.charter import genesis as charter_genesis
    from world.life.models import Genesis
    from world.tools import bootstrap

    assert "caretaker_pm_agent_id" in Genesis.model_fields, (
        "GROW-12: a genesis file cannot name a caretaker"
    )
    for module, where in (
        (charter_genesis.seed_genesis, "seed_genesis"),
        (charter_engine.create_genesis_charter_engine, "create_genesis_charter_engine"),
        (bootstrap.build_world, "build_world"),
    ):
        assert "caretaker_pm_agent_id" in inspect.signature(module).parameters, (
            f"GROW-12: {where} drops the caretaker, so a genesis that names "
            "one still boots with vacant seats"
        )

    runner = (Path(__file__).resolve().parents[1] / "run_genesis.py").read_text(
        encoding="utf-8"
    )
    assert "genesis.caretaker_pm_agent_id" in runner, (
        "GROW-12: the runner never reads it, so the field is inert on "
        "every real run - which is the defect this review finds most often"
    )


@pytest.mark.asyncio
async def test_a_booted_runner_really_seats_the_caretaker(tmp_path) -> None:
    """End to end, through a real boot.

    The first version of this read `self.genesis.caretaker_pm_agent_id` at
    the `build_world()` call - which happens about a hundred lines before
    `self.genesis` is loaded, so every boot raised AttributeError. The
    unit tests above all passed, because none of them boots a runner.
    A source-level check would have passed too.
    """
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()

    state = await runner.world_state.charter.store.get_government_state()
    named = runner.genesis.caretaker_pm_agent_id

    if named:
        assert state.pm_agent_id == named
        assert state.caretaker is True
        assert state.terms_served == {}
    else:
        # The shipped genesis names nobody, which is the documented
        # default. The assertion that matters is that boot did not raise
        # and the seats are as they have always been.
        assert state.pm_agent_id is None
        assert state.caretaker is False
