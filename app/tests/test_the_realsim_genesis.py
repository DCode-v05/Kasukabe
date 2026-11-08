"""The town the Real-sim button generates.

UI-14. These three tests live in `app/tests/` rather than
`game/tests/` because they exercise BOTH the game's real-sim controller
(`game.realsim.validate`'s purse rules) and `world.make_genesis_subset`,
and app/CONVENTIONS.md's test conventions put cross-module tests here:
"things no single package owns ... live in the top-level app/tests/". In
`game/tests/` they were a package-local suite importing across the
boundary its own package is forbidden to cross, and nothing said so - the
repo's only boundary test checked boto3.

What they pin is unchanged: a purse override must reach every agent, must
keep the $1000 ledger invariant, must be refused when the town cannot
fund it, and must not break the schema the runner parses.
"""
from __future__ import annotations

import json

import pytest

from world.ledger.core import TOTAL_USD_CENTS

def test_the_purse_reaches_every_agent_and_the_ledger_still_balances() -> None:
    from world.make_genesis_subset import build_subset

    out = build_subset(20, 400)
    assert {a["usd_wallet_cents"] for a in out["agents"]} == {400}
    led = out["usd_ledger"]
    assert led["agent_wallets_cents"] == 20 * 400
    assert led["total_cents"] == TOTAL_USD_CENTS
    assert led["treasury_cents"] == TOTAL_USD_CENTS - 20 * 400


def test_the_generator_refuses_a_purse_the_town_cannot_fund() -> None:
    from world.make_genesis_subset import build_subset

    with pytest.raises(ValueError, match="Maximum per agent"):
        build_subset(50, 5000)


def test_the_subset_still_loads_as_the_runner_will_load_it() -> None:
    """A purse override must not break the schema the runner parses."""
    from world.life.genesis import RichGenesis
    from world.make_genesis_subset import build_subset

    parsed = RichGenesis.model_validate_json(json.dumps(build_subset(20, 400)))
    assert parsed.population == 20


