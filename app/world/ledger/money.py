"""Money constants and pure conversion helpers.

Every quantity here is an integer: USD in cents, sim-currency in whole
units. app/CONVENTIONS.md is explicit that floating-point currency is a
bug, not a style choice, because it breaks the ledger invariant by
construction, so nothing in this module ever produces a float.
"""

from __future__ import annotations

CENTS_PER_USD = 100

TOTAL_USD_CENTS = 1000 * CENTS_PER_USD

GENESIS_AGENT_COUNT = 250
GENESIS_AGENT_USD_CENTS = 2 * CENTS_PER_USD
GENESIS_TREASURY_USD_CENTS = 500 * CENTS_PER_USD

assert (
    GENESIS_AGENT_COUNT * GENESIS_AGENT_USD_CENTS + GENESIS_TREASURY_USD_CENTS
    == TOTAL_USD_CENTS
), "genesis constants no longer sum to the $1000 invariant, fix money.py"

SIM_PER_USD = 5_000

assert SIM_PER_USD % CENTS_PER_USD == 0, (
    f"SIM_PER_USD={SIM_PER_USD} is not an exact multiple of "
    f"CENTS_PER_USD={CENTS_PER_USD}; sim-to-cents conversion would round"
)
SIM_PER_USD_CENT = SIM_PER_USD // CENTS_PER_USD

PENSION_MINIMUM_AGE = 70
PENSION_PRIVATE_PCT = 25
PENSION_GOVERNMENT_PCT = 50

TREASURY_ACCOUNT_ID = "government:treasury"
BURNED_ACCOUNT_ID = "system:burned"
MINT_ACCOUNT_ID = "system:mint"


def sim_to_usd_cents(sim_amount: int) -> int:
    """Convert a sim-currency amount to USD cents at the fixed exchange rate.

    Raises ValueError if sim_amount is not a positive multiple of
    SIM_PER_USD_CENT. A partial unit would otherwise silently round away
    real value, which this package's integer-only money rule forbids.
    """
    if sim_amount <= 0:
        raise ValueError(f"sim_amount must be positive, got {sim_amount}")
    if sim_amount % SIM_PER_USD_CENT != 0:
        raise ValueError(
            f"sim_amount={sim_amount} is not a multiple of {SIM_PER_USD_CENT} "
            f"(the exchange rate is {SIM_PER_USD} sim per $1.00; every exchange "
            f"must convert to a whole number of cents)"
        )
    return sim_amount // SIM_PER_USD_CENT


ENERGY_UNITS_PER_USD_CENT = 10
SIM_PER_ENERGY_UNIT = SIM_PER_USD_CENT // ENERGY_UNITS_PER_USD_CENT
if SIM_PER_ENERGY_UNIT * ENERGY_UNITS_PER_USD_CENT != SIM_PER_USD_CENT:
    raise ValueError(
        f"SIM_PER_USD_CENT={SIM_PER_USD_CENT} does not divide into "
        f"{ENERGY_UNITS_PER_USD_CENT} energy units, so the price of one unit "
        "would not be a whole number of sim. Re-rate one or the other."
    )


def energy_units(usd_cents: float) -> int:
    """USD cents as whole energy units. Never negative."""
    return max(0, round(float(usd_cents) * ENERGY_UNITS_PER_USD_CENT))


def format_energy(usd_cents: float) -> str:
    """534 -> "5,340 energy"."""
    return f"{energy_units(usd_cents):,} energy"

