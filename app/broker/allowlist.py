"""The model allowlist gate - DESIGN.md section 9's one hard law for
this package: "Hard-fails any model outside the allowlist."

Deliberately its own module, tiny as it is: the TASK spec calls the
allowlist out as a named, independently-testable requirement ("Model
allowlist EXACTLY [...] - any other model id -> 403 hard fail"), and
every other piece of broker (pricing, rate limiting, translation) is only
ever reached for a model that already passed this check - see
broker/service.py, where this is the first thing checked after auth.
"""

from __future__ import annotations

from broker.config import MODEL_ALLOWLIST
from broker.errors import ModelNotAllowedError


def check_model_allowed(model_id: str) -> None:
    """Raise ModelNotAllowedError unless model_id is EXACTLY one of
    MODEL_ALLOWLIST. Exact string match only - no prefix match, no
    case-folding, no version-suffix stripping. A model id that differs
    from the allowlist by even a trailing version tag is a different
    model as far as Bedrock billing and behavior are concerned, and
    DESIGN.md section 9 says "any other model id", not "any other model
    family"."""
    if model_id not in MODEL_ALLOWLIST:
        allowed = ", ".join(repr(m) for m in MODEL_ALLOWLIST)
        raise ModelNotAllowedError(
            f"model {model_id!r} is not in the broker allowlist - allowed models are {allowed}"
        )
