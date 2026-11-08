"""Tunable numbers for the life system, in one place so they are easy to find.

Two kinds of constant live here, and every one is labelled which kind it
is:

- DESIGN-PINNED: DESIGN.md section 5 gives this number exactly (the four
  ailment incidence rates, the terminal death window, the 24-hour
  dormancy timeout, the minority cutoff) or section 3 gives it exactly
  (the 5k sim revival fee, the 5k sim blessing fee, the 5k-sim-to-$1
  exchange rate, the $2 genesis wallet - re-rated 2026-08-01 from the
  original 100k/50k/100k figures, see money.py). Changing these means
  the design changed, not that game balance needed a tweak.
- ASSUMPTION: DESIGN.md describes the mechanic but not the number (e.g.
  "sickness cuts work capacity" with no multiplier, "untreated chronic"
  with no daily hazard rate). These are placeholders picked to be
  internally consistent (short < medium < long severity, and comfortably
  clear of DESIGN-pinned probabilities) pending real game-balance data.
  Whoever tunes population dynamics should treat these as the first
  place to look, not the code that reads them.
"""

from __future__ import annotations

from world.ledger.money import SIM_PER_USD, SIM_PER_USD_CENT
from world.life.models import AilmentKind

AILMENT_DAILY_INCIDENCE: dict[AilmentKind, float] = {
    AilmentKind.SHORT: 0.04,
    AilmentKind.MEDIUM: 0.01,
    AilmentKind.LONG: 0.0025,
    AilmentKind.TERMINAL: 0.0008,
}

AILMENT_DURATION_DAYS_SHORT = 1
AILMENT_DURATION_DAYS_LONG = 20
AILMENT_DURATION_MEDIUM_MIN_DAYS = 3
AILMENT_DURATION_MEDIUM_MAX_DAYS = 7
TERMINAL_DEATH_WINDOW_DAYS: tuple[int, int] = (3, 10)

WORK_CAPACITY_BY_AILMENT: dict[AilmentKind, float] = {
    AilmentKind.SHORT: 0.5,
    AilmentKind.MEDIUM: 0.3,
    AilmentKind.LONG: 0.15,
    AilmentKind.TERMINAL: 0.0,
}
WORK_CAPACITY_HEALTHY = 1.0

CHRONIC_UNTREATED_DAILY_HAZARD: dict[AilmentKind, float] = {
    AilmentKind.MEDIUM: 0.005,
    AilmentKind.LONG: 0.01,
}

ELDERLY_HAZARD_AGE_YEARS = 85
ELDERLY_DAILY_HAZARD = 0.004

PHARMACY_TREATABLE_AILMENTS = frozenset({AilmentKind.SHORT})
PHARMACY_SHORT_TREATMENT_COST_SIM = 500

DORMANCY_DEATH_HOURS = 24.0
DORMANCY_THRESHOLD_USD_CENTS = 2
REVIVAL_COST_SIM = SIM_PER_USD
EXCHANGE_SIM_PER_USD_CENT = SIM_PER_USD_CENT
REVIVAL_USD_CREDIT_CENTS = REVIVAL_COST_SIM // EXCHANGE_SIM_PER_USD_CENT

MINOR_AGE_LIMIT_YEARS = 18.0
YEARS_AGED_PER_GAME_HOUR = 1.0
GAME_HOURS_PER_DAY = 24.0

BABY_BLESSING_COST_SIM = 5_000

TEMPLE_WEDDING_FEE_SIM = 1_000
MARRIAGE_MIN_AGE_YEARS = 18.0

GENESIS_AGENT_WALLET_USD_CENTS = 200
GENESIS_TREASURY_USD_CENTS = 50_000
GENESIS_POPULATION = 250

GENESIS_AGENT_STARTING_SIM_BALANCE = 5_000
GENESIS_BUSINESS_STARTING_SIM_BALANCE = 20_000

DEFAULT_TARGET_BAND = (250, 300)
