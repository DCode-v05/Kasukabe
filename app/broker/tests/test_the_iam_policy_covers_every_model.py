"""The IAM policy has to name the models the broker actually invokes.

`INFRA-21`. `broker/config.py`'s allowlist is DOTTED -
`moonshotai.kimi-k2.5`, `deepseek.v3.2` - and
`infra/scripts/01-setup-s3-and-iam.sh` granted only the DASHED spelling,
`kimi-k2-5*` and `v3-2*`. If this account's Bedrock foundation-model ARNs
use the dotted form, the policy granted nothing and every direct model
invoke is denied.

Worse, it fails silently in the place you would not look: the wildcard
`inference-profile/*` line covers the path the broker normally takes, so a
wrong foundation-model ARN only bites on a lane with NO profile - which is
four of the six lanes today ("4 of 6 lanes have no inference profile ...
200 RPM, not 600", run 24).

Both spellings are now granted, because an ARN pattern matching no real
model grants no access, so covering both costs nothing. This test holds
the policy to the allowlist so adding a third model cannot quietly ship
without permission to call it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from broker.config import BEDROCK_MODELS, MODEL_ALLOWLIST, OPENAI_MODELS

POLICY = (
    Path(__file__).resolve().parents[2] / "infra" / "scripts" / "01-setup-s3-and-iam.sh"
)


@pytest.fixture(scope="module")
def policy_text() -> str:
    if not POLICY.exists():
        pytest.skip(f"no IAM script at {POLICY}")
    return POLICY.read_text(encoding="utf-8")


def test_the_allowlist_is_not_empty() -> None:
    """A vacuous parametrisation would make the check below meaningless."""
    assert BEDROCK_MODELS
    assert set(BEDROCK_MODELS) | set(OPENAI_MODELS) == set(MODEL_ALLOWLIST), (
        'every allowlisted model is either served by Bedrock or by OpenAI; '
        'a third kind would need its own grant story'
    )


@pytest.mark.parametrize("model_id", BEDROCK_MODELS)
def test_every_allowlisted_model_is_granted(model_id: str, policy_text: str) -> None:
    """The dotted id itself must appear as a foundation-model ARN."""
    assert f"foundation-model/{model_id}" in policy_text, (
        f"INFRA-21: the broker invokes {model_id!r} and the IAM policy does "
        "not name it, so a lane without an inference profile is denied"
    )


@pytest.mark.parametrize("model_id", BEDROCK_MODELS)
def test_the_dashed_spelling_is_granted_too(model_id: str, policy_text: str) -> None:
    """Nobody has confirmed which spelling this account's ARNs use, so both
    are allowed until somebody looks."""
    head, _, tail = model_id.rpartition(".")
    dashed = f"{head}-{tail}" if head else model_id
    assert f"foundation-model/{dashed}" in policy_text, (
        f"INFRA-21: {dashed!r} is not granted, so if this account uses the "
        "dashed ARN spelling the policy grants nothing"
    )


def test_the_inference_profile_wildcard_survives(policy_text: str) -> None:
    """The line that has been carrying every real call. Removing it while
    tightening the model ARNs would break the path that currently works."""
    assert re.search(r"inference-profile/\*", policy_text)
