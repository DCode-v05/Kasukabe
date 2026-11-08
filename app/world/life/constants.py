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

# ── DESIGN-PINNED (DESIGN.md section 5: incidence rates) ────────────
AILMENT_DAILY_INCIDENCE: dict[AilmentKind, float] = {
    AilmentKind.SHORT: 0.04,
    AilmentKind.MEDIUM: 0.01,
    AilmentKind.LONG: 0.0025,
    AilmentKind.TERMINAL: 0.0008,
}

# ── DESIGN-PINNED durations ───────────────────────────────────────────
AILMENT_DURATION_DAYS_SHORT = 1
AILMENT_DURATION_DAYS_LONG = 20
# DESIGN.md only says medium is "3+ days" - open-ended. We sample a
# bounded window (ASSUMPTION) so a medium ailment can actually be
# scheduled to end; 3-7 sits clearly above short's fixed 1 day and
# clearly below long's fixed 20 days without overlapping either.
AILMENT_DURATION_MEDIUM_MIN_DAYS = 3
AILMENT_DURATION_MEDIUM_MAX_DAYS = 7
# DESIGN-PINNED: "terminal 0.08% (death in 3-10 days)".
TERMINAL_DEATH_WINDOW_DAYS: tuple[int, int] = (3, 10)

# ── ASSUMPTION: work-capacity multipliers while sick ─────────────────
# DESIGN.md section 5 only says "sickness cuts work capacity" with no
# numbers. Ordered by severity: short barely slows you down, terminal
# means bedridden.
WORK_CAPACITY_BY_AILMENT: dict[AilmentKind, float] = {
    AilmentKind.SHORT: 0.5,
    AilmentKind.MEDIUM: 0.3,
    AilmentKind.LONG: 0.15,
    AilmentKind.TERMINAL: 0.0,
}
WORK_CAPACITY_HEALTHY = 1.0

# ── ASSUMPTION: untreated-chronic daily death hazard ──────────────────
# DESIGN.md section 5 lists "untreated-chronic" as a death path but
# gives no rate. At genesis the pharmacy only covers short ailments
# (DESIGN.md: "anything worse needs infrastructure the town doesn't
# have yet"), so every medium/long ailment is untreated by construction
# until the town builds a hospital. Long is twice medium's hazard: it
# has run for longer by the time it would kill, matching "more advanced
# illness is more dangerous."
CHRONIC_UNTREATED_DAILY_HAZARD: dict[AilmentKind, float] = {
    AilmentKind.MEDIUM: 0.005,
    AilmentKind.LONG: 0.01,
}

# ── ASSUMPTION: 85+ hazard ────────────────────────────────────────────
# DESIGN-PINNED age threshold (section 5: "85+ hazard"); ASSUMPTION rate.
ELDERLY_HAZARD_AGE_YEARS = 85
ELDERLY_DAILY_HAZARD = 0.004

# ── DESIGN-PINNED: pharmacy only treats short ailments at genesis ────
PHARMACY_TREATABLE_AILMENTS = frozenset({AilmentKind.SHORT})
# ASSUMPTION: DESIGN.md never prices the pharmacy visit itself.
PHARMACY_SHORT_TREATMENT_COST_SIM = 500

# ── DESIGN-PINNED: cognitive poverty / dormancy / revival ────────────
# "at $0 the agent goes dormant; any agent can revive them by paying
# 5k sim (buying them $1 of runway); unrevived for 24 game-hours ->
# death" (DESIGN.md section 3, re-rated 2026-09-04; the section said
# 10,000 sim until the code and the doc were reconciled on 2026-09-14).
#
# DERIVED from the rate as of the 2026-09-04 re-rating to 5,000:1, not
# hand-written. The number in DESIGN's sentence was only ever a spelling
# of "one dollar of runway" at the rate of the day, and pinning the
# spelling instead of the meaning is what made a rate change break the
# import. Reviving someone buys them exactly $1 of thinking, whatever a
# dollar costs in sim - which is the sentence that was always intended.
DORMANCY_DEATH_HOURS = 24.0
# The balance at or below which a living resident stops thinking, in USD
# cents. DESIGN.md section 3 says "at $0 the agent goes dormant", and
# every place that decides it compared against a bare literal zero of its
# own: the runner's sweep, the server's sweep, and the burn path. Named
# here because ECON-24 needs the law block to be able to TELL a resident
# what the number is, and a law that reads its number from a different
# copy than the code does is the drift that finding is about.
# ECON-21 raised this from 0, and the reason is the broker, not the
# design doc. The broker refuses to dispatch when the wallet is below its
# CEILING estimate for the call - one to two cents at realistic token
# counts. Sweeping only at zero left a resident holding ONE CENT in a gap
# it could not climb out of: it could not think (every call refused), was
# not dormant (balance above zero), could not be revived (`revive`
# requires `is_dormant`), still owed upkeep and head tax every day, and
# never started the 24-hour clock. No death, and no life either - and for
# exactly the residents the dormancy -> revive -> `buy_energy` chain
# exists to create stakes for.
#
# Two rather than one: the threshold has to REACH the broker's refusal
# ceiling, or the dead zone just moves down by a cent.
#
# Read as "at or below this you are dormant" - the sense the other three
# sites already use, and the sense `soul_seed` promises the resident in
# words ("when your wallet reaches $0.02 you go dormant").
DORMANCY_THRESHOLD_USD_CENTS = 2
REVIVAL_COST_SIM = SIM_PER_USD
# "5,000 sim-currency -> $1 API credit" (DESIGN.md section 3, re-rated
# 2026-09-04; the section still said 10,000 until this comment did).
# Imported from money.py, the single source the whole exchange rate
# derives from, rather than re-declared here, so this and the
# government-store rate can never drift apart. Revival converts the
# payer's sim fee into USD credit for the target at this same rate, so
# reviving someone is literally buying them $1 of runway - which is why
# the fee and the rate must be read together and never typed twice.
EXCHANGE_SIM_PER_USD_CENT = SIM_PER_USD_CENT
REVIVAL_USD_CREDIT_CENTS = REVIVAL_COST_SIM // EXCHANGE_SIM_PER_USD_CENT

# ── DESIGN-PINNED: aging ──────────────────────────────────────────────
# "Minors age 1 year per game-hour until 18, adults static" (DESIGN.md
# section 5 and section 4).
MINOR_AGE_LIMIT_YEARS = 18.0
YEARS_AGED_PER_GAME_HOUR = 1.0
GAME_HOURS_PER_DAY = 24.0  # 144 ticks/day x 10 game-minutes (DESIGN.md section 1)

# ── DESIGN-PINNED: birth flow ─────────────────────────────────────────
# "Baby blessing: 5,000 sim to god" (DESIGN.md section 3, re-rated
# 2026-08-01 from the original 50,000 sim figure).
BABY_BLESSING_COST_SIM = 5_000

# ── ASSUMPTION: marriage ──────────────────────────────────────────────
# DESIGN.md section 4 says "temple ceremony (fee to temple)" with no
# number.
TEMPLE_WEDDING_FEE_SIM = 1_000
MARRIAGE_MIN_AGE_YEARS = 18.0

# ── DESIGN-PINNED: the $1000 ledger (DESIGN.md section 3, app/CONVENTIONS.md) ─
GENESIS_AGENT_WALLET_USD_CENTS = 200  # $2 per agent
GENESIS_TREASURY_USD_CENTS = 50_000  # $500
GENESIS_POPULATION = 250

# ── ASSUMPTION: sim-currency starting balances ────────────────────────
# DESIGN.md pins the USD genesis split ($2/agent, $500 treasury) but
# never states a genesis sim-currency balance - the sim economy has to
# start somewhere so agents can pay wages, temple fees, and pharmacy
# bills before anyone has earned anything. Flagged as an open question
# for the ledger/economy module owner in this module's report; treated
# here as a placeholder, not a spec value.
GENESIS_AGENT_STARTING_SIM_BALANCE = 5_000
GENESIS_BUSINESS_STARTING_SIM_BALANCE = 20_000

# ── ASSUMPTION: population governor target band ───────────────────────
# "target band (start 250-300)" (DESIGN.md section 5).
DEFAULT_TARGET_BAND = (250, 300)
