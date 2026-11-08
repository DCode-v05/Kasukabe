"""A licence fee is money, not a number in a record.

`GOV-23`. `charter/government.py::issue_license` records `fee_paid_sim`
on the `License` and never debits anybody, and the finding says plainly:
"so a future tool must remember to". `issue_builder_license_handler` is
that future tool, and it did not remember.

Worse than unpaid: self-assessed. `IssueBuilderLicenseParams` carried a
caller-supplied `fee_paid_sim` defaulting to ZERO, so a resident issued
themselves a licence, declared it had cost nothing, and the record
agreed. The charter's `builder_license_fee_sim` - the price the town
actually set, amendable by the government that set it - was read by
nobody on this path.

The parameter is gone. The handler reads the charter and moves the money.

Ordering: the debit happens BEFORE the licence is issued, so an
applicant who cannot afford it is refused with nothing granted. That is
`registry.py`'s own "fallible externals before owned state" discipline;
the reverse order hands out a licence and then discovers the money was
not there.

The debit lives in the handler rather than in `issue_license` because
`charter/` deliberately holds no ledger reference - its module docstring
names that import boundary, and reaching across it would be the cycle
the boundary exists to prevent.
"""
from __future__ import annotations

import pytest

from world.ledger.core import TREASURY_ACCOUNT_ID
from world.ledger.models import TransactionReason

from .test_construction import spawn_agent  # noqa: F401


async def _seat(world, agent_id: str = "minister"):
    """A sitting government, since issuing a licence is a GOVERNMENT act."""
    from world.charter.models import GovernmentState

    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id=agent_id, minister_agent_ids=[])
    )


@pytest.mark.asyncio
async def test_the_fee_actually_moves(world, registry) -> None:
    """The finding. Before this the money stayed exactly where it was."""
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX

    await spawn_agent(world, "minister", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "carpenter", LOCATION_GOVERNMENT_COMPLEX)
    await _seat(world)

    charter = await world.charter.store.get_charter()
    fee = charter.builder_license_fee_sim
    assert fee > 0, "this charter charges nothing, so the test proves nothing"

    world.ledger.get_account("carpenter").sim_units = fee + 100
    before = world.ledger.get_account("carpenter").sim_units
    treasury_before = world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units

    await registry.execute(
        world, "minister", "issue_builder_license",
        {"holder_agent_id": "carpenter"},
    )

    assert world.ledger.get_account("carpenter").sim_units == before - fee, (
        "GOV-23: the licence was issued and the holder paid nothing"
    )
    assert world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units == (
        treasury_before + fee
    )


@pytest.mark.asyncio
async def test_the_price_is_the_charters_not_the_applicants(world, registry) -> None:
    """The parameter is gone. A caller that still passes one is telling
    the tool a price, and the tool must not take instruction on what it
    costs."""
    from world.tools.primitives.institutions import IssueBuilderLicenseParams

    assert "fee_paid_sim" not in IssueBuilderLicenseParams.model_fields, (
        "GOV-23: the licensee still declares its own fee, and it defaults "
        "to zero"
    )


@pytest.mark.asyncio
async def test_the_record_says_what_was_paid(world, registry) -> None:
    """`fee_paid_sim` on the `License` was the only trace of the fee and
    it was a claim. It should now be the amount that moved."""
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX

    await spawn_agent(world, "minister", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "carpenter", LOCATION_GOVERNMENT_COMPLEX)
    await _seat(world)
    charter = await world.charter.store.get_charter()
    world.ledger.get_account("carpenter").sim_units = 10_000

    await registry.execute(
        world, "minister", "issue_builder_license",
        {"holder_agent_id": "carpenter"},
    )

    licences = await world.charter.store.list_licenses()
    issued = [lic for lic in licences if lic.holder_agent_id == "carpenter"]
    assert issued, "no licence was recorded"
    assert issued[-1].fee_paid_sim == charter.builder_license_fee_sim


@pytest.mark.asyncio
async def test_an_applicant_who_cannot_pay_gets_no_licence(world, registry) -> None:
    """The ordering. Debiting after issuing would grant the licence and
    then fail, leaving a free licence on the record."""
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
    from world.ledger.errors import InsufficientFundsError

    await spawn_agent(world, "minister", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "pauper", LOCATION_GOVERNMENT_COMPLEX)
    await _seat(world)
    world.ledger.get_account("pauper").sim_units = 1

    with pytest.raises(InsufficientFundsError):
        await registry.execute(
            world, "minister", "issue_builder_license",
            {"holder_agent_id": "pauper"},
        )

    licences = await world.charter.store.list_licenses()
    assert not [lic for lic in licences if lic.holder_agent_id == "pauper"], (
        "GOV-23: the applicant could not pay and holds a licence anyway"
    )


@pytest.mark.asyncio
async def test_the_fee_is_booked_as_a_fee(world, registry) -> None:
    """ECON-03's vocabulary. A licence fee filed as TRANSFER is a licence
    fee the report cannot separate from a gift."""
    from world.charter.models import LOCATION_GOVERNMENT_COMPLEX

    await spawn_agent(world, "minister", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "carpenter", LOCATION_GOVERNMENT_COMPLEX)
    await _seat(world)
    world.ledger.get_account("carpenter").sim_units = 10_000

    await registry.execute(
        world, "minister", "issue_builder_license",
        {"holder_agent_id": "carpenter"},
    )

    fees = [
        t for t in world.ledger.transactions
        if t.reason is TransactionReason.FEE and t.memo == "builder_license"
    ]
    assert fees, (
        "GOV-23/ECON-03: the licence fee is not recorded as a fee, so the "
        "treasury view cannot tell what the town charged for permission"
    )
