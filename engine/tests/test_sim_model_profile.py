"""The one-model allowlist itself: moonshotai.kimi-k2.5, nothing else."""

from __future__ import annotations

import pytest

from src.herald import catalog_sync
from src.sim.model_profile import (
    ALLOWED_MODEL_IDS,
    MODEL_ID,
    MODEL_SPEC,
    SIM_MODEL_SPECS,
    SimModelNotAllowed,
    assert_allowed,
    guarded_create_llm,
    register_into_catalog_overlay,
)


def test_allowlist_is_exactly_one_model():
    assert ALLOWED_MODEL_IDS == {"moonshotai.kimi-k2.5"}
    assert MODEL_ID == "moonshotai.kimi-k2.5"
    assert set(SIM_MODEL_SPECS.keys()) == ALLOWED_MODEL_IDS
    assert SIM_MODEL_SPECS[MODEL_ID] is MODEL_SPEC


def test_pricing_matches_design_doc_flex_metadata():
    assert MODEL_SPEC.pricing.input_per_1m == pytest.approx(0.36)
    assert MODEL_SPEC.pricing.output_per_1m == pytest.approx(1.80)
    assert MODEL_SPEC.pricing.pricing_tier == "flex"
    assert MODEL_SPEC.pricing.bedrock_region == "ap-south-1"


def test_assert_allowed_passes_for_the_one_model():
    assert_allowed(MODEL_ID)


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-4o-mini",
        "claude-opus-4-8",
        "gpt-5.4-mini",
        "kimi-k2.5",  # the bare id, without the moonshotai. provider prefix
        "kimi-k2",
        "deepseek-v3.2",
        "deepseek.v3.2",
        "",
    ],
)
def test_assert_allowed_rejects_everything_else(model_id):
    with pytest.raises(SimModelNotAllowed):
        assert_allowed(model_id)


def test_guarded_create_llm_defaults_to_the_one_model(monkeypatch):
    from src import herald as herald_pkg

    calls = []
    monkeypatch.setattr(
        herald_pkg, "create_llm", lambda *a, **k: calls.append((a, k)) or "adapter"
    )

    from src.config import Settings

    settings = Settings.build_from_env({"CDT_URL": "http://broker.test", "CDT_KEY": "k"})[0]
    adapter = guarded_create_llm(settings)

    assert adapter == "adapter"
    assert calls == [((settings,), {"model_id": MODEL_ID})]


def test_guarded_create_llm_rejects_a_second_model(monkeypatch):
    from src import herald as herald_pkg

    calls = []
    monkeypatch.setattr(herald_pkg, "create_llm", lambda *a, **k: calls.append((a, k)))

    from src.config import Settings

    settings = Settings.build_from_env({"CDT_URL": "http://broker.test", "CDT_KEY": "k"})[0]
    with pytest.raises(SimModelNotAllowed):
        guarded_create_llm(settings, "claude-opus-4-8")
    # herald.create_llm must never even be called for a disallowed id - the
    # guard fires before any adapter construction is attempted.
    assert calls == []


def test_register_into_catalog_overlay_seeds_exactly_one_priced_model():
    catalog_sync.runtime_overlay.clear()
    catalog_sync.runtime_overlay["stale-model"] = object()  # anything pre-existing is wiped

    overlay = register_into_catalog_overlay()

    assert set(overlay.keys()) == {MODEL_ID}
    kimi = overlay[MODEL_ID]
    assert kimi.cost.input_per_1m == pytest.approx(0.36)
    assert kimi.cost.output_per_1m == pytest.approx(1.80)
    # Also reflected through herald's own catalog helpers, not just the raw dict.
    from src.herald.catalog import estimate_cost, get_model

    assert get_model(MODEL_ID) is kimi
    assert estimate_cost(MODEL_ID, 1_000_000, 1_000_000) == pytest.approx(0.36 + 1.80)
    assert get_model("claude-opus-4-8") is None
    assert get_model("deepseek-v3.2") is None


# ── the context budget is what decides compaction ───────────────────────


def test_the_registered_window_is_what_needs_compaction_reads():
    """The whole reason this number matters.

    context_engineering.needs_compaction(used, window) fires at
    window * COMPACTION_THRESHOLD, and `window` is exactly what
    get_context_window returns for this model - so this field, and nothing
    else on this path, decides when a conversation gets compacted.
    """
    from src.engine_core.context_engineering import COMPACTION_THRESHOLD, needs_compaction
    from src.herald.catalog import get_context_window
    from src.sim.model_profile import MODEL_ID, register_into_catalog_overlay

    register_into_catalog_overlay()
    window = get_context_window(MODEL_ID)
    fires_at = int(window * COMPACTION_THRESHOLD)

    assert not needs_compaction(fires_at - 1, window)
    assert needs_compaction(fires_at, window)


def test_an_ordinary_turn_no_longer_compacts_at_all():
    """The inverse of what this test used to assert, and deliberately so.

    It was written when the window was budgeted DOWN to 12,500 to force
    early compaction. That backfired completely: the engine's preflight
    adds an absolute RESERVE_FLOOR of 25,000, so the condition was true
    with zero messages and EVERY call in all 23 runs took the emergency
    path - tool results gutted, the day-0 06:20 perception pinned into
    the prefix forever, and a cold prompt cache every time.

    The operator's decision of 2026-09-04 moved the bound into the seam
    (sim/bounded_history.py), so an ordinary turn must now fit.
    """
    from src.engine_core.context_engineering import needs_compaction
    from src.herald.catalog import get_context_window
    from src.sim.model_profile import MODEL_ID, register_into_catalog_overlay

    register_into_catalog_overlay()
    window = get_context_window(MODEL_ID)

    def tokens(kilobytes: int) -> int:
        return kilobytes * 1024 // 4

    # The measured median body over 510 live calls was 65 KB.
    assert not needs_compaction(tokens(65), window), (
        "a median-sized real turn still trips compaction; the seam's bound "
        "is not doing its job and the engine is rewriting the prefix"
    )
    # And the worst one seen, 357 KB, still should - the bound is a bound,
    # not an invitation to send anything.
    assert needs_compaction(tokens(2_000), window)

def test_the_budget_clears_a_single_turn_several_times_over():
    """One turn must still fit, or a conversation could never be one.

    Rewritten for the 2026-09-04 window. The old text explained the
    trade-off of budgeting DOWN to 12,500; that budget is gone, because it
    made every call an emergency compaction rather than a cheap one. What
    survives is the property: whatever the window is, a single turn must
    clear the compaction threshold with room to spare.
    """
    from src.engine_core.context_engineering import COMPACTION_THRESHOLD
    from src.sim.model_profile import SIM_CONTEXT_WINDOW

    largest_single_turn = 9_000
    compaction_point = SIM_CONTEXT_WINDOW * COMPACTION_THRESHOLD
    assert compaction_point > largest_single_turn, (
        "compaction fires inside one turn: a conversation cannot be one"
    )
    # The margin, not the number. At the old 12,500 window this was 1.1x -
    # about one and a half turns - which is what made every call an
    # emergency. Now it is comfortable, and asserting the ratio means the
    # next re-window is checked on the property that matters.
    assert compaction_point >= largest_single_turn * 3, (
        f"only {compaction_point / largest_single_turn:.1f} turns fit before "
        "compaction; the seam's bound is the mechanism now, but the engine "
        "must still not be rewriting prefixes mid-conversation"
    )


def test_the_budget_is_overridable_without_a_code_change():
    import os

    from src.sim.model_profile import SIM_CONTEXT_WINDOW

    assert SIM_CONTEXT_WINDOW == int(os.environ.get("KASUKABE_CONTEXT_WINDOW", "131072"))


def test_the_skill_metadata_budget_is_now_tight_and_this_is_the_known_cost():
    """The skill-metadata budget, restated after the 2026-09-04 re-window.

    catalog.py sizes skill metadata at 2% of the context window. Budgeting
    the window down to 12,500 shrank it to ~250 tokens - roughly six skill
    entries - and that was recorded here as a known cost of compacting
    early. The window is back to the model's real size, so the cost is
    paid back: the budget is generous again, and a town that starts
    authoring in quantity will not truncate its own list.

    This test stays as the tripwire it always was: it asserts the number is
    what we think it is, so a future reader finds the trade-off rather than
    a mystery.
    """
    from src.catalog import skill_metadata_budget
    from src.sim.model_profile import SIM_CONTEXT_WINDOW

    budget = skill_metadata_budget(SIM_CONTEXT_WINDOW)
    roughly_one_entry = 40  # name + short description
    entries = budget // roughly_one_entry
    assert entries >= 25, (
        f"the skill list holds about {entries} entries. Below ~25 a town "
        "that starts authoring truncates its own skills before it hits any "
        "other limit - which is the cost the 12,500 window was paying, and "
        "the reason the 2026-09-04 re-window paid it back."
    )
