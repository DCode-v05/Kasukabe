from __future__ import annotations

import pytest

from broker.allowlist import check_model_allowed
from broker.config import MODEL_ALLOWLIST
from broker.errors import ModelNotAllowedError


@pytest.mark.parametrize("model_id", MODEL_ALLOWLIST)
def test_allowed_models_pass(model_id: str) -> None:
    check_model_allowed(model_id)


@pytest.mark.parametrize(
    "model_id",
    [
        "anthropic.claude-3-opus",
        "moonshotai.kimi-k2",
        "MOONSHOTAI.KIMI-K2.5",
        "deepseek.v3.2 ",
        "",
    ],
)
def test_disallowed_models_raise_403(model_id: str) -> None:
    with pytest.raises(ModelNotAllowedError) as excinfo:
        check_model_allowed(model_id)
    assert excinfo.value.status_code == 403
    assert excinfo.value.anthropic_error_type == "permission_error"


def test_allowlist_is_exactly_the_design_models() -> None:
    """Deliberately literal, not derived from MODEL_ALLOWLIST.

    Every other lane test now computes its expectations from the allowlist
    so a fourth model does not break them. This one must NOT: it is the
    law that says which models this town may think with, and a test that
    read the law off the law would pass no matter what anybody added.

    Qwen joined for throughput, not preference. The deployment holds
    profiles in ap-south-1 only, and Bedrock's RPM quota is per (model,
    region) - so models are the sole remaining way to buy capacity there.
    Routing order still puts it last (broker/lanes.py).

    The two Luna models joined for PRICE, and they are the first entries
    here not served by Bedrock at all. On this project's real traffic to
    date - 458.5M input and 6.0M output tokens over 32,739 calls - the same
    work costs $175.94 on Kimi and $48.87 on `gpt-6-luna`, because this town
    spends 98.7% of its money on input tokens and Luna prices those at
    $0.10/M against Kimi's $0.36. For a 50-resident run on a $60 cap that is
    the difference between stopping on game-day 2 and reaching game-day 4.

    Sol and Astra are NOT here. They are the same series at $2.00 and $10.00
    per million input - 20x and 100x Luna - and nothing measured so far says
    this town needs them.
    """
    assert MODEL_ALLOWLIST == (
        "moonshotai.kimi-k2.5",
        "deepseek.v3.2",
        "qwen.qwen3-coder-480b",
        "gpt-6-luna",
        "gpt-5.6-luna",
    )
