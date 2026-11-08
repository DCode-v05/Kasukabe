"""An OpenAI lane, speaking the same Converse shape every other lane speaks.

WHY HERE AND NOT SOMEWHERE EASIER. `broker/service.py` prepares one request
and hands it to `lane.gateway`, and everything downstream - the Anthropic
translation the engine reads, the token accounting, the pricing, the trace -
reads the Bedrock Converse shape that comes back. Implementing
`BedrockGateway` means an OpenAI model arrives through the same door as Kimi
and every one of those consumers is untouched. Translating anywhere else
would have meant a second path through the broker with its own bugs.

The name `BedrockGateway` is now slightly wrong - it is the gateway protocol,
and Bedrock is one implementation of it. Renaming it would touch the whole
broker for no behaviour, so it stays and this sentence explains it.

WHAT THIS IS NOT. It is not the OpenAI SDK. `httpx` is already a dependency
and the Chat Completions API is one POST; adding a second HTTP client to the
image to save writing forty lines would be a poor trade.

MEASURED REASON FOR EXISTING: at 458.5M input and 6.0M output tokens - the
project's real traffic to date - Kimi K2.5 costs $175.94 and `gpt-6-luna`
$48.87. Same tokens, 3.6x cheaper, which is the difference between a
50-resident run reaching game-day 2.5 and reaching game-day 4.5 on $60.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from broker.bedrock import classify_client_error
from broker.errors import UpstreamError, UpstreamOverloadedError

OPENAI_BASE_URL = "https://api.openai.com/v1"

FINISH_REASON = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "content_filtered",
}


def _text_of(blocks: list[dict[str, Any]]) -> str:
    """Converse content blocks flattened to the one string OpenAI takes."""
    return "\n".join(b["text"] for b in blocks if "text" in b)


def converse_request_to_openai(request: dict[str, Any], model: str) -> dict[str, Any]:
    """Bedrock Converse kwargs -> an OpenAI chat.completions body.

    `model` is the lane's, not the request's: `modelId` at this point is
    whatever the caller asked for, and routing has already decided this lane
    serves it. Passing the request's id straight through would send Bedrock's
    spelling to OpenAI and earn a 404.
    """
    messages: list[dict[str, Any]] = []

    system = _text_of(request.get("system") or [])
    if system:
        messages.append({"role": "system", "content": system})

    for turn in request.get("messages") or []:
        role = turn.get("role", "user")
        blocks = turn.get("content") or []

        results = [b["toolResult"] for b in blocks if "toolResult" in b]
        for result in results:
            body = result.get("content") or []
            messages.append({
                "role": "tool",
                "tool_call_id": result["toolUseId"],
                "content": _text_of(body) if isinstance(body, list) else str(body),
            })

        text = _text_of(blocks)
        calls = [b["toolUse"] for b in blocks if "toolUse" in b]
        if calls:
            messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": c["toolUseId"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("input") or {}),
                        },
                    }
                    for c in calls
                ],
            })
        elif text and not results:
            messages.append({"role": role, "content": text})

    body: dict[str, Any] = {"model": model, "messages": messages}

    inference = request.get("inferenceConfig") or {}
    if (cap := inference.get("maxTokens")) is not None:
        body["max_completion_tokens"] = cap
    if (temp := inference.get("temperature")) is not None:
        body["temperature"] = temp
    if (stops := inference.get("stopSequences")) :
        body["stop"] = stops

    tools = (request.get("toolConfig") or {}).get("tools") or []
    if tools:
        body["reasoning_effort"] = "none"
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["toolSpec"]["name"],
                    "description": t["toolSpec"].get("description", ""),
                    "parameters": t["toolSpec"].get("inputSchema", {}).get("json", {}),
                },
            }
            for t in tools
        ]
    return body


def openai_response_to_converse(payload: dict[str, Any]) -> dict[str, Any]:
    """An OpenAI completion -> the Converse response shape service.py reads."""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks: list[dict[str, Any]] = []

    if message.get("content"):
        blocks.append({"text": message["content"]})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (TypeError, ValueError):
            args = {"_unparsed_arguments": fn.get("arguments", "")}
        blocks.append({
            "toolUse": {
                "toolUseId": call.get("id", ""),
                "name": fn.get("name", ""),
                "input": args,
            }
        })

    usage = payload.get("usage") or {}
    return {
        "output": {"message": {"role": "assistant", "content": blocks}},
        "stopReason": FINISH_REASON.get(choice.get("finish_reason"), "end_turn"),
        "usage": {
            "inputTokens": usage.get("prompt_tokens", 0),
            "outputTokens": usage.get("completion_tokens", 0),
            "totalTokens": usage.get("total_tokens", 0),
        },
    }


class OpenAIGateway:
    """One OpenAI model, behind the gateway protocol the broker dispatches to.

    Holds no lane state: rate limiting, retries and routing are the pool's
    job (`broker/lanes.py`), exactly as they are for Bedrock.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = OPENAI_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _raise_for(self, status: int, body: str) -> None:
        """OpenAI's status codes, classified the way Bedrock's are.

        429 and 5xx are the pool's overload signal - it backs off and tries
        another lane. Anything else is the model answering, and a retry would
        just ask the same question twice.
        """
        if status == 429:
            raise UpstreamOverloadedError(f"openai rate limited: {body[:200]}")
        if status >= 500:
            raise UpstreamOverloadedError(f"openai {status}: {body[:200]}")
        raise UpstreamError(f"openai {status}: {body[:300]}")

    async def converse(self, request: dict[str, Any]) -> dict[str, Any]:
        body = converse_request_to_openai(request, self.model)
        client = await self._http()
        try:
            resp = await client.post(
                f"{self.base_url}/chat/completions", json=body, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise UpstreamOverloadedError(f"openai transport: {exc}") from exc
        if resp.status_code >= 400:
            self._raise_for(resp.status_code, resp.text)
        return openai_response_to_converse(resp.json())

    async def converse_stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yields Bedrock converse_stream events, built from OpenAI's SSE.

        The index bookkeeping is the whole difficulty. Converse numbers its
        content blocks and sends a start, deltas and a stop for each; OpenAI
        streams a flat sequence of deltas where a tool call is identified by
        its own `index` inside `tool_calls`. So text takes block 0, and each
        distinct tool-call index takes the next block, with starts emitted
        the first time each is seen and stops emitted for all of them at the
        end - which is what `translate.anthropic_sse_events_from_converse_stream`
        expects to see.
        """
        body = converse_request_to_openai(request, self.model)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

        client = await self._http()
        opened: dict[int, int] = {}
        text_open = False
        next_block = 0
        stop_reason = "end_turn"
        usage: dict[str, Any] = {}

        try:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions",
                json=body, headers=self._headers(),
            ) as resp:
                if resp.status_code >= 400:
                    self._raise_for(resp.status_code, (await resp.aread()).decode()[:400])
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue

                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choice = (chunk.get("choices") or [{}])[0]
                    if choice.get("finish_reason"):
                        stop_reason = FINISH_REASON.get(choice["finish_reason"], "end_turn")
                    delta = choice.get("delta") or {}

                    if delta.get("content"):
                        if not text_open:
                            text_open = True
                            text_block = next_block
                            next_block += 1
                        yield {"contentBlockDelta": {
                            "contentBlockIndex": text_block,
                            "delta": {"text": delta["content"]},
                        }}

                    for call in delta.get("tool_calls") or []:
                        oi = call.get("index", 0)
                        fn = call.get("function") or {}
                        if oi not in opened:
                            opened[oi] = next_block
                            next_block += 1
                            yield {"contentBlockStart": {
                                "contentBlockIndex": opened[oi],
                                "start": {"toolUse": {
                                    "toolUseId": call.get("id", f"call_{oi}"),
                                    "name": fn.get("name", ""),
                                }},
                            }}
                        if fn.get("arguments"):
                            yield {"contentBlockDelta": {
                                "contentBlockIndex": opened[oi],
                                "delta": {"toolUse": {"input": fn["arguments"]}},
                            }}
        except (UpstreamError, UpstreamOverloadedError):
            raise
        except httpx.HTTPError as exc:
            raise UpstreamOverloadedError(f"openai transport: {exc}") from exc

        if text_open:
            yield {"contentBlockStop": {"contentBlockIndex": text_block}}
        for block in opened.values():
            yield {"contentBlockStop": {"contentBlockIndex": block}}
        yield {"messageStop": {"stopReason": stop_reason}}
        yield {"metadata": {"usage": {
            "inputTokens": usage.get("prompt_tokens", 0),
            "outputTokens": usage.get("completion_tokens", 0),
        }}}

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


__all__ = [
    "OPENAI_BASE_URL",
    "OpenAIGateway",
    "converse_request_to_openai",
    "openai_response_to_converse",
]
