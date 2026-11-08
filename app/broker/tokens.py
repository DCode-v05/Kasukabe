"""Best-effort token estimation - used ONLY where an exact count isn't
available yet. Never used to compute what actually gets billed.

Two call sites need an estimate instead of a fact:

  1. The pre-dispatch wallet check (broker/service.py): we must decide
     whether to refuse a call BEFORE Bedrock has processed it, so there
     is no exact count to check against yet.
  2. The streaming response's `message_start` event (broker/translate.py):
     Bedrock's converse_stream API (unlike Anthropic's native streaming)
     does not report input token count until the terminal `metadata`
     event, but herald's SSE parser reads input_tokens ONLY from
     `message_start` (engine/src/herald/provider.py's stream_with_tools
     never updates input_tokens from message_delta). Sending 0 there
     would starve the engine's own context-budget tracking of a real
     signal; a same-order-of-magnitude estimate is far better than 0,
     and it is clearly a display/bookkeeping number on herald's side,
     not a billing one - see the module docstring in bedrock.py.

Neither of these is a tokenizer for Kimi or DeepSeek specifically (no
bundled offline tokenizer exists for either in this codebase, and Bedrock
does not expose a token-counting endpoint for third-party models the way
Anthropic's own API does). ~4 characters per token is the standard rough
heuristic for this family of models; it is deliberately conservative in
the wallet-check direction (see pricing.py's estimate_cost_cents_ceiling,
which rounds the resulting cost UP on top of this).
"""

from __future__ import annotations

CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE)


def estimate_tokens_for_texts(texts: list[str]) -> int:
    return sum(estimate_tokens(t) for t in texts)
