"""A run that cannot think must stop, not tick harder.

Two failures, both observed on real runs:

  The broker refused every call for 44 minutes after its spend cap tripped -
  10,465 SpendCapExceededError, 34% of that run's calls, every one of them
  after the money was gone. The broker capped at SPEND_CAP_USD=$40; the
  runner's own governor capped at budget_cap_cents, which defaults to $400.
  Two caps that never spoke to each other.

  Thirty engines once died on a port collision while the world ticked on
  above them, reporting a rising tick number the whole way.

Both look identical from every panel: engines "alive", tick climbing, spend
flat. Only the dispatch result says otherwise.
"""

from __future__ import annotations

from world.run_genesis import (
    BARREN_TICKS_BEFORE_STOP,
    GenesisRunnerConfig,
    halt_reserve_cents,
)


class TestTheTwoCapsAgree:
    def test_the_broker_cap_sets_the_runner_cap(self):
        """Set the broker to $40 and the run must stop near $40, not $400.

        CORE-25 changed this from "at $40" to "near $40". The two caps
        being EQUAL fixed the original bug (a run that ticked on against a
        broker refusing every call) and left a smaller one behind: two
        counters race to the same number and the broker's gets there
        first, so the run still ended with the broker halted, every engine
        turn 500ing, and a whole barren tick passing before the probe
        noticed. The runner now aims a reserve below, and stops cleanly
        while the broker will still answer.

        What this test is for is unchanged: the broker's cap DRIVES the
        runner's. $400 would still be the bug.
        """
        config = GenesisRunnerConfig.from_env({"SPEND_CAP_USD": "40.00"})
        assert config.budget_cap_cents == 4000 - halt_reserve_cents(4000)
        assert 3000 < config.budget_cap_cents < 4000

    def test_an_explicit_runner_cap_still_wins(self):
        """A run that deliberately wants the two to differ can say so."""
        config = GenesisRunnerConfig.from_env(
            {"SPEND_CAP_USD": "40.00", "KASUKABE_BUDGET_CAP_CENTS": "9999"}
        )
        assert config.budget_cap_cents == 9999

    def test_neither_set_keeps_the_old_default(self):
        assert GenesisRunnerConfig.from_env({}).budget_cap_cents == 40_000

    def test_a_malformed_cap_does_not_crash_the_runner(self):
        """The broker reports its own bad config; this must not also die."""
        config = GenesisRunnerConfig.from_env({"SPEND_CAP_USD": "forty dollars"})
        assert config.budget_cap_cents == 40_000

    def test_fractional_dollars_survive_the_conversion(self):
        """The cents must not be lost in the float. Measured against the
        broker's own number, before CORE-25's reserve comes off it."""
        cents = GenesisRunnerConfig.from_env(
            {"SPEND_CAP_USD": "33.33"}).budget_cap_cents
        assert cents + halt_reserve_cents(3333) == 3333


class TestTheBarrenTickGuard:
    def test_the_threshold_is_a_real_number_of_ticks(self):
        """Long enough to ride out a broker restart or a throttling burst,
        short enough that a run whose money is gone does not spend another
        forty minutes finding out."""
        assert 5 <= BARREN_TICKS_BEFORE_STOP <= 60
