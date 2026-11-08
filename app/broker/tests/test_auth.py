from __future__ import annotations

import pytest

from broker.auth import extract_bearer_token, resolve_agent_id, sign_agent_token
from broker.errors import AuthenticationError


def test_extract_bearer_token_prefers_authorization() -> None:
    token = extract_bearer_token(
        {"Authorization": "Bearer agent-123", "x-api-key": "agent-456"}
    )
    assert token == "agent-123"


def test_extract_bearer_token_falls_back_to_x_api_key() -> None:
    token = extract_bearer_token({"x-api-key": "agent-789"})
    assert token == "agent-789"


def test_extract_bearer_token_is_header_case_insensitive() -> None:
    token = extract_bearer_token({"AUTHORIZATION": "Bearer agent-abc"})
    assert token == "agent-abc"


def test_extract_bearer_token_without_bearer_prefix() -> None:
    token = extract_bearer_token({"Authorization": "agent-xyz"})
    assert token == "agent-xyz"


def test_extract_bearer_token_raises_when_absent() -> None:
    with pytest.raises(AuthenticationError):
        extract_bearer_token({})


def test_resolve_agent_id_unsigned_when_no_secret() -> None:
    agent_id = resolve_agent_id({"Authorization": "Bearer agent-42"}, auth_shared_secret="")
    assert agent_id == "agent-42"


def test_resolve_agent_id_unsigned_strips_any_dotted_suffix() -> None:
    agent_id = resolve_agent_id({"Authorization": "Bearer agent-42.deadbeef"}, auth_shared_secret="")
    assert agent_id == "agent-42"


def test_resolve_agent_id_requires_signature_when_secret_configured() -> None:
    with pytest.raises(AuthenticationError):
        resolve_agent_id({"Authorization": "Bearer agent-42"}, auth_shared_secret="s3cr3t")


def test_resolve_agent_id_verifies_valid_signature() -> None:
    token = sign_agent_token("agent-42", "s3cr3t")
    agent_id = resolve_agent_id({"Authorization": f"Bearer {token}"}, auth_shared_secret="s3cr3t")
    assert agent_id == "agent-42"


def test_resolve_agent_id_rejects_invalid_signature() -> None:
    with pytest.raises(AuthenticationError):
        resolve_agent_id(
            {"Authorization": "Bearer agent-42.notarealsignature"}, auth_shared_secret="s3cr3t"
        )


def test_resolve_agent_id_rejects_signature_for_wrong_secret() -> None:
    token = sign_agent_token("agent-42", "correct-secret")
    with pytest.raises(AuthenticationError):
        resolve_agent_id({"Authorization": f"Bearer {token}"}, auth_shared_secret="wrong-secret")


def test_resolve_agent_id_rejects_empty_agent_id() -> None:
    with pytest.raises(AuthenticationError):
        resolve_agent_id({"Authorization": "Bearer "}, auth_shared_secret="")
