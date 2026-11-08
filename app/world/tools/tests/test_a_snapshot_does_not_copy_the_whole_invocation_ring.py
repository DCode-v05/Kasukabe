"""The snapshot every tool call pays for, and the one field worth not copying.

`ToolRegistry.execute()` snapshots tools-owned state before dispatching, under
a single global `asyncio.Lock`, on every call every resident makes. Forty of
the forty-one owned fields are sized by the TOWN - agents, households,
businesses - and copying them measures at about 0.1ms together.
`tool_invocations` is sized by the number of ACTS instead: a ring of up to
`MAX_TOOL_INVOCATIONS_KEPT` (5,000) records. Deep-copying a full ring measured
at ~27ms, so past the first five thousand calls every call in the town paid
~27ms of copying that nothing ever read, serialised behind that one lock.

That queue is what the election30 diagnosis found at the far end: with thirty
engines posting at once, calls waited past the 10s read timeout in
`engine/src/sim/world_tools.py` and about one action in seven - roughly 1,800
of 12,600 - came back as `ReadTimeout`. A resident cannot tell a lost call
from a refusal, so it spent the rest of the turn reasoning from an act it had
never performed. It also made the world get slower the longer it ran, which is
why day 1 read as though production had stopped.

So `snapshot()` copies that one list shallowly. This file pins both halves of
why that is safe: the copy is genuinely shallow (or the cost is back), and
rollback still undoes membership, which is the only thing the field was in
`OWNED_STATE_FIELDS` for.
"""

import pytest

from world.tools.models import ToolInvocationRecord
from world.tools.state import MAX_TOOL_INVOCATIONS_KEPT, OWNED_STATE_FIELDS
from world.tools.tests.conftest import spawn_agent
from world.tools.locations import farmland_location

FIELD = farmland_location("wheat")


def _record(i: int) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        invocation_id=f"invocation-{i:08d}",
        tool_name="harvest",
        namespace="world",
        actor_id="a0042",
        tick=i,
        params={"resource": "wheat", "quantity": 3},
        owner_id=None,
        program_id=None,
        program_step=None,
    )


def _fill(world, n: int) -> None:
    while len(world.tool_invocations) < n:
        world.tool_invocations.append(_record(len(world.tool_invocations)))


@pytest.mark.asyncio
async def test_the_invocation_ring_is_not_deep_copied(world) -> None:
    """Identity, not milliseconds.

    A timing assertion for this would be flaky on a loaded machine and
    would not say what it meant. What "shallow" actually means is that the
    snapshot holds the SAME record objects - so if someone restores the
    deepcopy, every one of these is a different object and this fails
    immediately, on any hardware.
    """
    _fill(world, 50)

    held = world.snapshot()["tool_invocations"]

    assert len(held) == 50
    for kept, live in zip(held, world.tool_invocations):
        assert kept is live, (
            "the invocation ring was copied record by record; at the 5,000 cap "
            "that is ~27ms on every tool call in the town, inside execute()'s "
            "global lock"
        )


@pytest.mark.asyncio
async def test_every_other_owned_field_is_still_copied_deeply(world) -> None:
    """The exemption is one field wide, and stays that way.

    Deep-copying is what makes rollback safe for state that is MUTATED in
    place, which is most of it. This is the guard against the shallow case
    being widened to a field that needs the deep copy.
    """
    await spawn_agent(world, "n01", FIELD)
    snapshot = world.snapshot()

    shallow = [
        name
        for name in OWNED_STATE_FIELDS
        if getattr(world, name) and snapshot[name] is getattr(world, name)
    ]
    assert shallow == [], f"copied by reference, so rollback cannot undo: {shallow}"

    assert snapshot["agents"]["n01"] is not world.agents["n01"]


@pytest.mark.asyncio
async def test_a_restore_still_forgets_records_appended_after_it(world) -> None:
    """Membership is the whole job.

    The field is in OWNED_STATE_FIELDS so that a composite which fails
    partway leaves no phantom record behind (see `ToolInvocationRecord`).
    That is a claim about which records are IN the list, and a shallow copy
    of a list restores exactly that.
    """
    _fill(world, 10)
    snapshot = world.snapshot()

    world.tool_invocations.append(_record(999))
    assert len(world.tool_invocations) == 11

    world.restore(snapshot)

    assert len(world.tool_invocations) == 10
    assert all(r.invocation_id != "invocation-00000999" for r in world.tool_invocations)


@pytest.mark.asyncio
async def test_a_restore_puts_back_records_the_ring_trimmed_away(world) -> None:
    """The other mutation: `dispatch()` trims the ring from the front.

    A shallow copy restores a record the trim deleted just as a deep copy
    did, because the snapshot still holds the reference. Pinned because the
    trim is the one write to this list that REMOVES anything, and it is
    easy to assume a cheap copy cannot undo a deletion.
    """
    _fill(world, 10)
    oldest = world.tool_invocations[0]
    snapshot = world.snapshot()

    del world.tool_invocations[:3]
    assert world.tool_invocations[0] is not oldest

    world.restore(snapshot)

    assert len(world.tool_invocations) == 10
    assert world.tool_invocations[0] is oldest


@pytest.mark.asyncio
async def test_a_full_ring_costs_about_what_an_empty_one_costs(world) -> None:
    """The claim in cost terms, stated as a ratio rather than a deadline.

    Absolute milliseconds vary by machine; the SHAPE is the finding - the
    snapshot must not scale with how long the town has been running. A
    generous 5x bound still fails loudly at the ~300x the deep copy cost,
    without turning into a flaky test on a busy CI box.
    """
    import time

    def cost() -> float:
        runs = []
        for _ in range(5):
            start = time.perf_counter()
            world.snapshot()
            runs.append(time.perf_counter() - start)
        return min(runs)

    await spawn_agent(world, "n01", FIELD)
    empty = cost()
    _fill(world, MAX_TOOL_INVOCATIONS_KEPT)
    full = cost()

    assert full < empty * 5 + 0.002, (
        f"a full invocation ring costs {full * 1000:.1f}ms against "
        f"{empty * 1000:.1f}ms empty - the snapshot is scaling with the "
        "number of acts the town has taken, which is the defect"
    )
