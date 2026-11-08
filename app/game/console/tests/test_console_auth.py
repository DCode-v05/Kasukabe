"""Tests for auth.py's token extraction and timing-safe comparison."""

from __future__ import annotations

import pytest

from game.console.auth import extract_operator_token, tokens_match
from game.console.errors import OperatorAuthError


def test_extract_prefers_authorization_bearer() -> None:
    token = extract_operator_token("Bearer secret-1", "secret-2")
    assert token == "secret-1"


def test_extract_tolerates_missing_bearer_prefix() -> None:
    token = extract_operator_token("secret-1", None)
    assert token == "secret-1"


def test_extract_falls_back_to_x_operator_token() -> None:
    token = extract_operator_token(None, "secret-2")
    assert token == "secret-2"


def test_extract_raises_when_both_headers_missing() -> None:
    with pytest.raises(OperatorAuthError):
        extract_operator_token(None, None)


def test_extract_raises_on_blank_headers() -> None:
    with pytest.raises(OperatorAuthError):
        extract_operator_token("   ", "")


def test_tokens_match_accepts_equal_strings() -> None:
    assert tokens_match("abc", "abc") is True


def test_tokens_match_rejects_different_strings() -> None:
    assert tokens_match("abc", "abd") is False


def test_tokens_match_rejects_when_expected_is_empty() -> None:
    assert tokens_match("", "") is False
    assert tokens_match("abc", "") is False
