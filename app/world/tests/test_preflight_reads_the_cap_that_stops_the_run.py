"""Preflight printed one spend cap and the broker enforced another.

Run 25, verbatim from the checklist:

    [✓] Spend cap is set
          SPEND_CAP_USD = 10.00

The broker was enforcing **$20.00** — it had been started with that in its
own environment, and `GET /admin/spend` said so throughout:

    {"spent_usd": 0.0, "cap_usd": 20.0, ...}

Preflight was reading `app/.env` from the shell the RUNNER was launched
in. The broker is a separate process with a separate environment, so the
two can differ by any amount and neither notices.

That run's direction was harmless — the enforcer allowed more than the
checklist claimed. The dangerous direction is the same bug reversed: the
`.env` says 10.00, the broker was started without the variable and is on
`DEFAULT_SPEND_CAP_USD = 400.0`, and an operator who read the line and
walked away is forty times out. Only one number can actually stop a run.

So the check asks the governor. `/admin/spend`'s `cap_usd` is the value
`SpendGovernor` was constructed with — the number that will refuse the
call. The environment is kept as a fallback for a broker that is up but
whose admin route moved, and the line says which source it used, because
a preflight that quietly answers from the second-best source is how this
started.
"""

from __future__ import annotations

import pytest

from world import preflight


@pytest.fixture
def broker_says(monkeypatch):
    """Point `_broker_spend` at a chosen answer without a live broker."""

    def _install(payload):
        monkeypatch.setattr(preflight, "_broker_spend", lambda _url: payload)

    return _install


def _cap_line(checks) -> str:
    for check in checks:
        if check.name == "Spend cap is set":
            return check.detail
    raise AssertionError("preflight no longer has a 'Spend cap is set' check")


def _room_check(checks):
    return next((c for c in checks if c.name == "The cap has room left"), None)


def test_the_brokers_cap_wins_over_the_shell(broker_says, monkeypatch, tmp_path):
    """The exact run 25 disagreement: .env says 10, broker enforces 20."""
    monkeypatch.setenv("SPEND_CAP_USD", "10.00")
    broker_says({"cap_usd": 20.0, "spent_usd": 0.0})

    detail = _cap_line(_run(tmp_path))

    assert "20.00" in detail, f"preflight still trusts the shell: {detail}"
    assert "10.00" not in detail
    assert "broker" in detail.lower()


def test_the_dangerous_direction_is_caught_too(broker_says, monkeypatch, tmp_path):
    """.env says 10, the broker is on its 400 default. This is the one
    that costs money."""
    monkeypatch.setenv("SPEND_CAP_USD", "10.00")
    broker_says({"cap_usd": 400.0, "spent_usd": 0.0})

    detail = _cap_line(_run(tmp_path))

    assert "400.00" in detail


def test_a_silent_broker_falls_back_and_says_so(broker_says, monkeypatch, tmp_path):
    """A preflight that cannot answer is worse than one answering from a
    second-best source - as long as it names the source."""
    monkeypatch.setenv("SPEND_CAP_USD", "10.00")
    broker_says(None)

    detail = _cap_line(_run(tmp_path))

    assert "10.00" in detail
    assert "did not answer" in detail, (
        "the fallback is indistinguishable from the authoritative reading"
    )


def test_a_part_spent_governor_is_a_finding(broker_says, monkeypatch, tmp_path):
    """The governor resumes from the trace store it was given, so pointing
    a new run at a used database starts it part-spent. Run 25 was given a
    fresh store for exactly this reason and nothing would have said so."""
    monkeypatch.setenv("SPEND_CAP_USD", "20.00")
    broker_says({"cap_usd": 20.0, "spent_usd": 18.0})

    check = _room_check(_run(tmp_path))

    assert check is not None
    assert not check.ok
    assert "18.00" in check.detail and "20.00" in check.detail


def test_a_fresh_governor_passes(broker_says, monkeypatch, tmp_path):
    monkeypatch.setenv("SPEND_CAP_USD", "20.00")
    broker_says({"cap_usd": 20.0, "spent_usd": 0.0})

    check = _room_check(_run(tmp_path))

    assert check is not None and check.ok


def _run(tmp_path):
    """Preflight against a broker that is up, with everything else stubbed
    to whatever it likes - only the cap checks are under test here."""
    return preflight.run(
        broker_url="http://127.0.0.1:8200",
        world_url=None,
        expected_agents=20,
        genesis_path=str(tmp_path / "genesis.json"),
        home_root=str(tmp_path / "homes"),
        trace_db=str(tmp_path / "trace.db"),
        engine_repo=str(tmp_path / "engine"),
    ).checks
