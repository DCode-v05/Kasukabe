"""Money constants and pure conversion helpers.

Every quantity here is an integer: USD in cents, sim-currency in whole
units. app/CONVENTIONS.md is explicit that floating-point currency is a
bug, not a style choice, because it breaks the ledger invariant by
construction, so nothing in this module ever produces a float.
"""

from __future__ import annotations

CENTS_PER_USD = 100

# DESIGN.md section 3: the whole simulation's real-money footprint,
# fixed for the life of the run. $500 genesis (250 agents x $2) + $500
# government treasury = $1000.00 = 100_000 cents. This is the number
# app/CONVENTIONS.md's ledger invariant checks against after every
# USD-moving transaction.
TOTAL_USD_CENTS = 1000 * CENTS_PER_USD

GENESIS_AGENT_COUNT = 250
GENESIS_AGENT_USD_CENTS = 2 * CENTS_PER_USD  # $2.00 per agent wallet
GENESIS_TREASURY_USD_CENTS = 500 * CENTS_PER_USD  # $500.00 government treasury

# Self-checking constant: if a future edit changes one of the three
# numbers above without keeping the sum at $1000, genesis() would halt
# the simulation the moment it ran. Failing at import time instead is
# cheaper to debug than failing at boot.
assert (
    GENESIS_AGENT_COUNT * GENESIS_AGENT_USD_CENTS + GENESIS_TREASURY_USD_CENTS
    == TOTAL_USD_CENTS
), "genesis constants no longer sum to the $1000 invariant, fix money.py"

# DESIGN.md section 3 (re-rated 2026-08-01): "10,000 sim-currency -> $1
# API credit". SIM_PER_USD is the single constant this whole exchange
# rate derives from; every other module in app/ that needs the rate
# (world/life/constants.py's REVIVAL_USD_CREDIT_CENTS included) imports
# a value computed from this one number instead of re-declaring the
# rate, so it is structurally impossible for the two to drift apart.
# The original rate was 100_000:1; DESIGN.md section 3 explains the
# operator's reasoning for re-rating it to 10_000:1 (thinking was too
# expensive relative to earning at the old rate for honest work to fund
# cognition).
#
# Re-rated again to 5,000:1 on the operator's decision of 2026-09-04, for
# the same reason one more time. ECON-05 measured it: at 10,000:1 and
# roughly $2 of thinking per agent-day, a resident had to earn 20,000 sim
# a day to pay for its own cognition, against day wages an order of
# magnitude below that. Energy was not expensive, it was UNBUYABLE from
# honest work - so the only survivable strategies were ones the town was
# not built to reward, and every run ended with residents going dormant
# rather than trading.
#
# Halving the rate halves what a day of thinking costs in sim. It does
# not touch the $1,000 invariant above: that is denominated in cents and
# is asserted independently, which is exactly why this constant can be
# changed at all.
SIM_PER_USD = 5_000

# The finest exact-integer granularity the rate supports: any sim
# amount that is a multiple of this many units converts to a whole
# number of cents with no remainder. Derived, not hand-picked, and
# asserted rather than silently floor-divided, so a future edit to
# SIM_PER_USD that is not a clean multiple of CENTS_PER_USD fails at
# import time instead of quietly rounding cents away, which this
# package's integer-only money rule forbids. The $1-sized "block"
# DESIGN.md illustrates is the common case, not a hard minimum enforced
# here; a tool-registry layer that wants to restrict exchange calls to
# whole-dollar increments can do so at the tool-gating level without
# changing this rate.
assert SIM_PER_USD % CENTS_PER_USD == 0, (
    f"SIM_PER_USD={SIM_PER_USD} is not an exact multiple of "
    f"CENTS_PER_USD={CENTS_PER_USD}; sim-to-cents conversion would round"
)
SIM_PER_USD_CENT = SIM_PER_USD // CENTS_PER_USD

# DESIGN.md section 4: pensions pay a fraction of the retiree's last
# salary, from treasury, starting at this age. Rate depends on whether
# the last job was private-sector or government.
PENSION_MINIMUM_AGE = 70
PENSION_PRIVATE_PCT = 25
PENSION_GOVERNMENT_PCT = 50

# Well-known account ids. Treasury and burned are real, balance-tracked
# singleton accounts (Ledger.__init__ creates them). Mint is virtual: it
# never appears in Ledger.accounts and never carries a balance, because
# sim minted for blessings is created from nothing by design (DESIGN.md
# section 3, "Baby blessing: 5,000 sim to god"). It exists only as a
# Transaction.from_account label so the money's origin stays auditable
# in the transaction log even though nothing is debited to produce it.
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


# ── energy: what a dollar is, to a resident ─────────────────────────────
#
# Dollars are not money in Kasukabe. Inside the ledger they stay integer
# USD cents, because the broker bills real inference in them and the
# $1,000 invariant is kept in them. But nothing in the town prices, pays,
# trades or owes in dollars - every one of those is sim - so a resident is
# shown its thinking balance as ENERGY, never as "$" in a "wallet".
#
# Measured before this was written: runs 25 and 26 together had 0 of 505
# utterances naming a dollar amount, and every resident-to-resident
# transaction was sim. The only dollar movements were thinking burned, sim
# exchanged for thinking, a revival and an estate. The words residents
# were given said otherwise - "Thinking costs real money ... billed against
# your own wallet in real US dollars", "Energy: $5.34 in your wallet" - and
# a unit described as money is a unit that invites being used as money.
#
# Ten units to the cent, so every figure a resident meets is a whole
# number: a turn uses about 5, dormancy is at 20, a revival brings 1,000,
# and 5 sim buys 1. Presentation only - no balance is stored in energy.
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

