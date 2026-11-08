from __future__ import annotations

from broker.errors import (
    AuthenticationError,
    BrokerError,
    InsufficientFundsError,
    InvalidRequestError,
    ModelNotAllowedError,
    SpendCapExceededError,
    UnknownAgentError,
    UpstreamError,
    UpstreamOverloadedError,
)


def test_to_anthropic_body_shape() -> None:
    err = ModelNotAllowedError("model x is not allowed")
    body = err.to_anthropic_body()
    assert body == {
        "type": "error",
        "error": {"type": "permission_error", "message": "model x is not allowed"},
    }


def test_each_subclass_has_its_documented_status_and_type() -> None:
    expected = {
        AuthenticationError: (401, "authentication_error"),
        ModelNotAllowedError: (403, "permission_error"),
        InvalidRequestError: (400, "invalid_request_error"),
        InsufficientFundsError: (402, "insufficient_funds_error"),
        UnknownAgentError: (404, "not_found_error"),
        UpstreamOverloadedError: (529, "overloaded_error"),
        UpstreamError: (502, "api_error"),
        SpendCapExceededError: (429, "rate_limit_error"),
    }
    for cls, (status_code, error_type) in expected.items():
        instance = cls("message")
        assert instance.status_code == status_code
        assert instance.anthropic_error_type == error_type


def test_broker_error_base_defaults_to_500() -> None:
    err = BrokerError("boom")
    assert err.status_code == 500
    assert err.anthropic_error_type == "api_error"
    assert str(err) == "boom"
