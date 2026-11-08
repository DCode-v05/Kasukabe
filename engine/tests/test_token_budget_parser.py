"""Tests for the token budget shorthand parser and nudge formatter.

Covers parse_token_budget (start shorthand, end shorthand, verbose form) and
get_budget_continuation_message (nudge text formatting with thousands separators).
"""

from src.herald.token_budget_parser import (
    parse_token_budget,
    get_budget_continuation_message,
)


# --- parse_token_budget: start shorthand ---


class TestStartShorthand:
    def test_plain_start_k(self):
        assert parse_token_budget("+500k refactor this module") == 500_000

    def test_plain_start_m(self):
        assert parse_token_budget("+2m do a big refactor") == 2_000_000

    def test_plain_start_b(self):
        assert parse_token_budget("+1b massive task") == 1_000_000_000

    def test_start_case_insensitive(self):
        assert parse_token_budget("+500K explain") == 500_000
        assert parse_token_budget("+2M do a thing") == 2_000_000
        assert parse_token_budget("+1B huge") == 1_000_000_000

    def test_start_with_leading_whitespace(self):
        assert parse_token_budget("   +500k explain") == 500_000

    def test_start_with_decimal(self):
        assert parse_token_budget("+1.5m refactor") == 1_500_000

    def test_start_with_decimal_k(self):
        assert parse_token_budget("+2.5k small task") == 2_500

    def test_start_without_following_text(self):
        assert parse_token_budget("+500k") == 500_000


# --- parse_token_budget: end shorthand ---


class TestEndShorthand:
    def test_plain_end_k(self):
        assert parse_token_budget("refactor this module +500k") == 500_000

    def test_plain_end_m(self):
        assert parse_token_budget("do a big refactor +2m") == 2_000_000

    def test_end_with_period(self):
        assert parse_token_budget("refactor this +500k.") == 500_000

    def test_end_with_exclamation(self):
        assert parse_token_budget("do it +500k!") == 500_000

    def test_end_with_question(self):
        assert parse_token_budget("can you do this +500k?") == 500_000

    def test_end_with_decimal(self):
        assert parse_token_budget("analyse +1.5m") == 1_500_000

    def test_end_case_insensitive(self):
        assert parse_token_budget("analyse +500K") == 500_000

    def test_end_with_trailing_whitespace(self):
        assert parse_token_budget("analyse +500k   ") == 500_000


# --- parse_token_budget: verbose form ---


class TestVerboseForm:
    def test_use_k_tokens(self):
        assert parse_token_budget("use 500k tokens for this") == 500_000

    def test_use_m_tokens(self):
        assert parse_token_budget("use 2m tokens") == 2_000_000

    def test_use_singular_token(self):
        assert parse_token_budget("use 500k token") == 500_000

    def test_spend_k_tokens(self):
        assert parse_token_budget("please spend 500k tokens") == 500_000

    def test_verbose_case_insensitive(self):
        assert parse_token_budget("USE 500K TOKENS") == 500_000
        assert parse_token_budget("Spend 2M Tokens") == 2_000_000

    def test_verbose_decimal(self):
        assert parse_token_budget("use 1.5m tokens to refactor") == 1_500_000

    def test_verbose_anywhere_in_text(self):
        assert parse_token_budget("this is a task, use 500k tokens please") == 500_000


# --- parse_token_budget: precedence and negative cases ---


class TestPrecedence:
    def test_start_wins_over_end_when_both_present(self):
        # Start shorthand is tried first; if it matches, that's what's used.
        assert parse_token_budget("+500k do a thing +1m") == 500_000

    def test_end_used_when_no_start(self):
        assert parse_token_budget("do a thing +1m") == 1_000_000

    def test_verbose_fallback(self):
        # No shorthand, verbose matches.
        assert parse_token_budget("please use 500k tokens here") == 500_000


class TestNoMatch:
    def test_empty_string(self):
        assert parse_token_budget("") is None

    def test_plain_text(self):
        assert parse_token_budget("refactor this module please") is None

    def test_no_suffix(self):
        # "+500" without k/m/b does not match any pattern.
        assert parse_token_budget("+500 tokens") is None

    def test_zero_suffix(self):
        # "+0k" matches the regex but yields 0 → treated as invalid (None).
        assert parse_token_budget("+0k") is None

    def test_negative_sign_does_not_match_shorthand(self):
        # Shorthand is "+digit", not "-digit". "-500k" should not match.
        assert parse_token_budget("-500k refactor") is None

    def test_verbose_without_tokens_word(self):
        assert parse_token_budget("use 500k cats") is None

    def test_verbose_without_use_or_spend(self):
        assert parse_token_budget("I have 500k tokens") is None

    def test_shorthand_without_digit(self):
        assert parse_token_budget("+k refactor") is None

    def test_start_requires_plus(self):
        assert parse_token_budget("500k refactor") is None


class TestEdgeCases:
    def test_very_large_b(self):
        assert parse_token_budget("+9b enormous") == 9_000_000_000

    def test_fractional_below_one(self):
        # "+0.5k" → 500. Regex matches; result is 500, which is > 0 so valid.
        assert parse_token_budget("+0.5k small") == 500

    def test_fractional_yielding_zero(self):
        # "+0.0k" → 0 → None
        assert parse_token_budget("+0.0k") is None


# --- get_budget_continuation_message ---


class TestContinuationMessage:
    def test_basic_format(self):
        msg = get_budget_continuation_message(pct=24, turn_tokens=120_000, budget=500_000)
        assert "24%" in msg
        assert "120,000" in msg
        assert "500,000" in msg
        assert "Keep working" in msg
        assert "do not summarize" in msg

    def test_zero_pct(self):
        msg = get_budget_continuation_message(pct=0, turn_tokens=0, budget=500_000)
        assert "0%" in msg
        assert "0 /" in msg
        assert "500,000" in msg

    def test_thousands_separators(self):
        msg = get_budget_continuation_message(pct=50, turn_tokens=1_000_000, budget=2_000_000)
        assert "1,000,000" in msg
        assert "2,000,000" in msg

    def test_large_budget(self):
        msg = get_budget_continuation_message(pct=10, turn_tokens=100_000_000, budget=1_000_000_000)
        assert "100,000,000" in msg
        assert "1,000,000,000" in msg

    def test_exact_src_format(self):
        # Match src formatter output shape exactly, including Unicode em-dash.
        msg = get_budget_continuation_message(pct=24, turn_tokens=120_000, budget=500_000)
        assert (
            msg
            == "Stopped at 24% of token target (120,000 / 500,000). Keep working \u2014 do not summarize."
        )

    def test_em_dash_is_unicode(self):
        # 1:1 parity with src/utils/tokenBudget.ts:72 which uses \u2014, not "--"
        msg = get_budget_continuation_message(pct=50, turn_tokens=1_000, budget=2_000)
        assert "\u2014" in msg
        assert "--" not in msg
