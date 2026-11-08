"""Run 29 threw away everything its residents learned.

The end-of-run learning drain gives every resident one last consolidation
batch over the signals its morning produced - and it needs model calls.
Run 29 was launched with `--budget-cap-cents 2000` against a $20.00 broker
cap: the runner's cap EQUAL to the broker's. The halt reserve CORE-25 built
(leave 5% of the broker's cap unspent, so the world stops itself while the
broker still answers) is only applied when the runner derives its cap from
SPEND_CAP_USD, so an explicit flag skipped it entirely. The broker, whose
counter runs ahead of the ledger's, halted first; all 14 end-of-run
`auto:learning` calls were refused; "0 of 20 resident(s) consolidated".

And the warning written for exactly this - "the broker had already halted
dispatch" - did not print, because it asked the RUNNER'S governor whether
the run had halted, and the governor never tripped: the broker stopped
first. The one line that would have said the learning was lost was keyed to
the wrong counter.
"""

from __future__ import annotations

from world.run_genesis import build_arg_parser, config_from_args, halt_reserve_cents


def test_an_explicit_cap_equal_to_the_brokers_leaves_room_for_the_drain(monkeypatch) -> None:
    monkeypatch.setenv("SPEND_CAP_USD", "20.00")
    monkeypatch.delenv("KASUKABE_BUDGET_CAP_CENTS", raising=False)

    config = config_from_args(build_arg_parser().parse_args(["--budget-cap-cents", "2000"]))

    assert config.budget_cap_cents == 2000 - halt_reserve_cents(2000), (
        "the runner will spend to the broker's cap, the broker halts first, and the "
        "end-of-run drain meets a broker that refuses every call - run 29"
    )


def test_a_cap_already_below_the_reserve_is_left_alone(monkeypatch) -> None:
    monkeypatch.setenv("SPEND_CAP_USD", "20.00")
    monkeypatch.delenv("KASUKABE_BUDGET_CAP_CENTS", raising=False)

    config = config_from_args(build_arg_parser().parse_args(["--budget-cap-cents", "1500"]))

    assert config.budget_cap_cents == 1500


def test_no_broker_cap_known_means_no_guess(monkeypatch) -> None:
    """Without SPEND_CAP_USD nothing says where the broker stops, and
    inventing a reserve against an unknown cap would be a guess."""
    monkeypatch.delenv("SPEND_CAP_USD", raising=False)
    monkeypatch.delenv("KASUKABE_BUDGET_CAP_CENTS", raising=False)

    config = config_from_args(build_arg_parser().parse_args(["--budget-cap-cents", "2000"]))

    assert config.budget_cap_cents == 2000


def test_the_deliberate_escape_hatch_still_works(monkeypatch) -> None:
    """KASUKABE_BUDGET_CAP_CENTS is the documented way to make the two caps
    equal on purpose (CORE-25). An operator who says so gets what they asked."""
    monkeypatch.setenv("SPEND_CAP_USD", "20.00")
    monkeypatch.setenv("KASUKABE_BUDGET_CAP_CENTS", "2000")

    config = config_from_args(build_arg_parser().parse_args(["--budget-cap-cents", "2000"]))

    assert config.budget_cap_cents == 2000


def test_the_warning_listens_to_the_broker_not_only_the_runner() -> None:
    """The halt that loses the learning is the BROKER'S. The runner's own
    governor may never trip when the broker stops first."""
    from world.run_genesis import dispatch_had_halted

    assert dispatch_had_halted(governor_tripped=False, broker_state="halted")
    assert dispatch_had_halted(governor_tripped=True, broker_state=None)
    assert not dispatch_had_halted(governor_tripped=False, broker_state="normal")
    assert not dispatch_had_halted(governor_tripped=False, broker_state=None)
