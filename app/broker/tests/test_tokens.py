from __future__ import annotations

from broker.tokens import estimate_tokens, estimate_tokens_for_texts


def test_empty_string_is_zero_tokens() -> None:
    assert estimate_tokens("") == 0


def test_short_text_is_at_least_one_token() -> None:
    assert estimate_tokens("hi") == 1


def test_roughly_four_chars_per_token() -> None:
    assert estimate_tokens("x" * 400) == 100


def test_partial_final_token_rounds_up() -> None:
    # 401 chars -> 100 full tokens + 1 leftover char -> must round up to 101,
    # never down (this feeds the wallet-check ceiling in pricing.py).
    assert estimate_tokens("x" * 401) == 101


def test_estimate_tokens_for_texts_sums_each() -> None:
    assert estimate_tokens_for_texts(["x" * 4, "x" * 8, ""]) == 1 + 2 + 0
