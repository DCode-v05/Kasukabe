"""DESIGN-PINNED money constants in world.life.constants (DESIGN.md
section 3, re-rated 2026-08-01): the revival fee, the baby blessing fee,
and the exchange-rate constant this module imports from money.py rather
than re-declaring, so the two can never drift apart.
"""

from __future__ import annotations

from world.ledger.money import SIM_PER_USD, SIM_PER_USD_CENT
from world.life.constants import (
    BABY_BLESSING_COST_SIM,
    EXCHANGE_SIM_PER_USD_CENT,
    REVIVAL_COST_SIM,
    REVIVAL_USD_CREDIT_CENTS,
)


def test_reviving_someone_buys_them_exactly_one_dollar_of_runway() -> None:
    """The meaning, not the spelling.

    DESIGN's "10,000 sim" was only ever a spelling of "one dollar of
    thinking" at the rate of the day. Pinning the spelling is what made
    the 2026-09-04 re-rating break an import. This pins what the sentence
    meant, so it survives the next one too.
    """
    from world.ledger.money import SIM_PER_USD

    assert REVIVAL_COST_SIM == SIM_PER_USD
    assert REVIVAL_USD_CREDIT_CENTS == 100


def test_baby_blessing_cost_matches_the_re_rated_design_figure() -> None:
    assert BABY_BLESSING_COST_SIM == 5_000


def test_exchange_sim_per_usd_cent_is_imported_from_money_not_redeclared() -> None:
    """The old code hard-coded 1_000 here, independently of money.py's
    SIM_PER_USD_CENT; a rate change had to be made in two places and
    could silently drift. Now it is the same object, so this can only
    ever be true or the import itself would already have failed.
    """
    assert EXCHANGE_SIM_PER_USD_CENT is SIM_PER_USD_CENT
    assert EXCHANGE_SIM_PER_USD_CENT == SIM_PER_USD // 100


def test_revival_grants_exactly_one_dollar_of_usd_credit() -> None:
    """DESIGN.md section 3: revival is "literally buying the dormant
    agent $1 of API runway" - true at both the old and the new rate,
    because REVIVAL_COST_SIM and EXCHANGE_SIM_PER_USD_CENT were re-rated
    by the same factor (10x). Exact integer division, no remainder.
    """
    assert REVIVAL_COST_SIM % EXCHANGE_SIM_PER_USD_CENT == 0
    assert REVIVAL_USD_CREDIT_CENTS == 100
