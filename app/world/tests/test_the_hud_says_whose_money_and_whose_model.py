"""The spend readout must name the budget the operator chose and the model
that is spending it.

Two ways it was misleading before.

`cap_usd` came straight from the broker, and `run-scenario.sh` deliberately
sets the broker $5 above the runner's cap so the end-of-run learning drain
has room. So a $60 run displayed "of $65.00" - a number nobody chose, which
invites the operator to expect five dollars of run that is not coming.

And it showed no model at all. The wrong model leading is silent and costs
3.6x on this town's token shape; a figure that does not say whose it is
cannot answer the question the readout exists for.
"""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[2]
LAUNCHER = APP / "run-scenario.sh"
SERVER = APP / "game" / "server.py"
BROKER = APP / "broker" / "app.py"
PAGE = APP / "game" / "static" / "township-v2.html"
SCRIPT = APP / "game" / "static" / "js" / "township" / "township-v2.js"


def test_the_broker_reports_which_model_is_serving() -> None:
    source = BROKER.read_text(encoding="utf-8")
    start = source.index("async def admin_spend")
    end = source.find("async def ", start + 10)
    spend = source[start: end if end != -1 else len(source)]

    assert '"model"' in spend, "/admin/spend does not say which model is spending"
    assert "only_model" in spend, "it should prefer the exclusive model when one is set"
    assert "model_is_exclusive" in spend, (
        "nothing distinguishes a model that merely leads from one nothing may overflow past"
    )


def test_the_viewer_shows_the_run_budget_not_the_broker_ceiling() -> None:
    """$60 asked for must not display as $65."""
    source = SERVER.read_text(encoding="utf-8")

    assert "RUN_BUDGET_USD" in source
    assert 'run_budget if run_budget else d.get("cap_usd")' in source, (
        "the run budget does not take precedence over the broker's ceiling"
    )
    assert '"broker_cap_usd"' in source, "the broker's own ceiling is no longer reported at all"


def test_the_launcher_passes_the_operators_cap_to_the_viewer() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    launch = source[source.index("game.server:app") - 400: source.index("game.server:app")]

    assert 'RUN_BUDGET_USD="$CAP"' in launch, (
        "the viewer is started without the run's budget, so it falls back to "
        "the broker's $5-higher ceiling"
    )


def test_the_page_has_somewhere_to_put_the_model() -> None:
    assert 'id="costModel"' in PAGE.read_text(encoding="utf-8")


def test_the_script_renders_the_model_and_marks_exclusivity() -> None:
    """A dot for ONLY_MODEL. "gpt-6-luna leads" and "nothing else may serve"
    are different guarantees and only the second is worth trusting a budget
    to, so the readout distinguishes them."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "costModel" in source
    assert "COST.model" in source and "COST.exclusive" in source
    assert "model_is_exclusive" in source


def test_a_dead_broker_clears_the_model_rather_than_leaving_a_stale_one() -> None:
    """A model name left on screen after the broker dies reads as "still
    spending on gpt-6-luna" when nothing is spending at all."""
    source = SCRIPT.read_text(encoding="utf-8")
    idle = source[source.index("el.className = 'cost-readout idle';"):]
    idle = idle[: idle.index("return")]

    assert "modelEl.textContent = ''" in idle, "the model name survives the broker dying"
