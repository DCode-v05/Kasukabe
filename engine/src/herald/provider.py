"""Citadel-routed provider — Anthropic-Messages wire over raw httpx.

Single concrete LLM implementation. All inference flows over the citadel
gateway (settings.cdt_url) using the Anthropic Messages API as the
canonical wire shape. Citadel handles per-key cooldown, sticky routing,
and cross-provider transforms server-side, so this client holds one
bearer token (settings.cdt_key) and never rotates keys locally.

No vendor SDK: requests + SSE decoding go through ``herald/transport.py``
(``CitadelClient``). The wire bytes are identical to what the anthropic SDK
sent; only the client library is gone. ``build_kwargs`` already produces the
exact JSON body, so the transport just POSTs it.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from src.config import Settings
from src.herald.defaults import ModelRole, resolve_model
from src.herald.transport import CitadelAPIError, CitadelClient
from src.herald.types import (
    ContentBlock,
    ImageBlock,
    LLMResponse,
    Message,
    PersistedBlock,
    StopReason,
    StreamEvent,
    SystemPromptBlock,
    TextBlock,
    ThinkingBlock,
    ToolCall,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    build_tool_name_map,
    format_persisted_block_xml,
    sanitize_tool_name,
)
from src.herald import quota_cooldown
from src.herald.usage_tracker import record_quota_snapshot, record_usage
from src.engine_core.cached_snip import (
    CACHE_EDITING_BETA_HEADER,
    CachedSnipState,
    apply_cache_edits_to_messages,
    cached_snip_prepass_enabled,
    consume_pending_deletes,
    detect_unexpected_cache_break,
    is_model_supported_for_cache_editing,
    queue_deletions,
    reset_state,
)

logger = logging.getLogger(__name__)


class CitadelQuotaExhausted(Exception):
    """Citadel rejected with 429 + code=QUOTA_EXCEEDED.

    Distinct from a transient rate limit: quota is depleted for the
    plan-tier window and won't recover until reset. Callers must not
    retry; surface to the end user as an upgrade-plan prompt.
    """

    def __init__(
        self,
        message: str = "citadel quota exhausted",
        *,
        used_tokens: int = 0,
        max_tokens: int = 0,
        window_seconds: int = 0,
    ) -> None:
        super().__init__(message)
        self.used_tokens = used_tokens
        self.max_tokens = max_tokens
        self.window_seconds = window_seconds


class CitadelBudgetExhausted(Exception):
    """Citadel rejected with 402 + code=BUDGET_EXCEEDED.

    Metered (pay-per-use) plans carry an optional dollar budget ceiling.
    When the subscription's spend over the budget window hits the cap,
    citadel returns 402; the turn won't recover until the budget is
    raised or the window resets. Callers must not retry; surface to the
    end user as a raise-budget prompt.
    """

    def __init__(
        self,
        message: str = "citadel budget exhausted",
        *,
        spent_usd: float = 0.0,
        budget_usd: float = 0.0,
        window_seconds: int = 0,
    ) -> None:
        super().__init__(message)
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        self.window_seconds = window_seconds


class CitadelSubscriptionSuspended(Exception):
    """Citadel rejected with 403 + code=SUBSCRIPTION_SUSPENDED.

    The subscription is suspended or cancelled, so no turn will succeed
    until an administrator reinstates it. Callers must not retry; surface
    to the end user as a contact-your-administrator prompt.
    """

    def __init__(
        self,
        message: str = "citadel subscription suspended",
        *,
        subscription_id: str = "",
        status: str = "",
    ) -> None:
        super().__init__(message)
        self.subscription_id = subscription_id
        self.status = status


def maybe_raise_quota_exhausted(exc: CitadelAPIError) -> None:
    """Convert citadel QUOTA_EXCEEDED 429s into ``CitadelQuotaExhausted``.

    Retry is off for quota exhaustion ONLY. Citadel always labels a
    quota-driven 429 with the stable ``QUOTA_EXCEEDED`` body code, so that is
    the one and only signal that converts to the terminal, never-retried
    ``CitadelQuotaExhausted``. Every other 429 — provider rate limits, opaque
    intermediary responses, body-less errors — passes through untouched and
    keeps the normal transient/persistent retry machinery (as does genuine
    upstream overload, which arrives as 529 and is never inspected here).
    """
    if exc.status_code != 429:
        return
    body = exc.body if isinstance(exc.body, dict) else {}
    err = body.get("error") or {}
    if err.get("code") != "QUOTA_EXCEEDED":
        return
    quota_exc = CitadelQuotaExhausted(
        err.get("message", "citadel quota exhausted"),
        used_tokens=int(err.get("used_tokens", 0) or 0),
        max_tokens=int(err.get("max_tokens", 0) or 0),
        window_seconds=int(err.get("window_seconds", 0) or 0),
    )
    # Arm the engine-local cooldown so background consumers stop firing doomed
    # calls until the citadel-reported window resets. Foreground turns ignore it.
    quota_cooldown.enter_cooldown(quota_exc)
    raise quota_exc from exc


def maybe_raise_budget_exhausted(exc: CitadelAPIError) -> None:
    """Convert citadel BUDGET_EXCEEDED 402s into ``CitadelBudgetExhausted``.

    Inspects the error's parsed body for the citadel-stable error code. Other
    402s pass through untouched.
    """
    if exc.status_code != 402:
        return
    body = exc.body
    if not isinstance(body, dict):
        return
    err = body.get("error") or {}
    if err.get("code") != "BUDGET_EXCEEDED":
        return
    budget_exc = CitadelBudgetExhausted(
        err.get("message", "citadel budget exhausted"),
        spent_usd=float(err.get("spent_usd", 0) or 0),
        budget_usd=float(err.get("budget_usd", 0) or 0),
        window_seconds=int(err.get("window_seconds", 0) or 0),
    )
    # Mirror the quota path: arm the engine-local cooldown so background
    # consumers stop firing doomed calls until the citadel-reported budget
    # window resets. Foreground turns ignore it. enter_cooldown is duck-typed
    # on window_seconds, which CitadelBudgetExhausted also carries.
    quota_cooldown.enter_cooldown(budget_exc)
    raise budget_exc from exc


def maybe_raise_subscription_suspended(exc: CitadelAPIError) -> None:
    """Convert citadel SUBSCRIPTION_SUSPENDED 403s into
    ``CitadelSubscriptionSuspended``.

    Inspects the error's parsed body for the citadel-stable error code. Other
    403s pass through untouched.
    """
    if exc.status_code != 403:
        return
    body = exc.body
    if not isinstance(body, dict):
        return
    err = body.get("error") or {}
    if err.get("code") != "SUBSCRIPTION_SUSPENDED":
        return
    raise CitadelSubscriptionSuspended(
        err.get("message", "citadel subscription suspended"),
        subscription_id=str(err.get("subscription_id", "") or ""),
        status=str(err.get("status", "") or ""),
    ) from exc


def apply_betas(kwargs: dict) -> None:
    """Pop ``betas`` list from kwargs and merge into ``extra_headers['anthropic-beta']``.

    ``build_kwargs`` stores betas as an inspectable list so tests can assert
    on ``kwargs["betas"]`` directly.  This function translates that list to the
    ``anthropic-beta`` HTTP header (a wire string citadel forwards) and stashes
    it under ``kwargs["extra_headers"]`` for the transport to send. Called in
    each of the three call-sites just before the transport call.
    """
    betas = kwargs.pop("betas", None)
    if not betas:
        return
    extra = kwargs.setdefault("extra_headers", {})
    existing = extra.get("anthropic-beta", "")
    merged = ", ".join(filter(None, [existing] + list(betas)))
    extra["anthropic-beta"] = merged


class Provider:
    """Anthropic-Messages client pointed at the citadel gateway.

    Single concrete LLM implementation in the engine. ``Provider`` is the
    only class the herald factory constructs. Citadel decides which
    upstream provider (Anthropic / OpenAI / Gemini) actually serves each
    request based on the model_id; engine speaks the Anthropic Messages
    wire to citadel either way — over ``CitadelClient`` (raw httpx, no SDK).
    """

    def __init__(self, settings: Settings, model_override: str | None = None) -> None:
        # CitadelClient validates cdt_url/cdt_key and builds the persistent
        # httpx client with the citadel auth + version headers.
        self.client = CitadelClient(settings)
        # Explicit override wins, then LLM_MODEL from settings, then the
        # herald launch default. Nothing here validates the id; boot-time
        # model validation (src/herald/model_validation.py) already has.
        self.model = model_override or resolve_model(ModelRole.PRIMARY, settings)
        self.max_tokens = settings.max_tokens
        # TTL for the single system-prompt cache breakpoint. Threaded from the
        # same Settings the rest of the engine reads (config -> bootstrap ->
        # ConsciousnessManager wires it onto the blocks; the wire send path
        # reads it here). Default "1h" keeps behavior unchanged unless
        # GLOBAL_CACHE_TTL=5m is set.
        self.global_cache_ttl = settings.global_cache_ttl
        # Tool schema session cache — avoids recomputing ~11K tokens of tool
        # schemas every turn. Recomputed only when the tool set changes.
        self.cached_tool_schemas: list[dict] | None = None
        self.cached_tool_names: frozenset[str] | None = None
        # Sanitised → internal tool name (e.g. "ad_gmail_send_message" →
        # "ad:gmail.send_message"). Refreshed alongside the schema cache so
        # tool_use responses from the model can be reverse-mapped back to
        # the dispatcher's expected names.
        self.tool_name_reverse_map: dict[str, str] = {}
        # Beta header latching — once thinking is sent in a request, keep
        # sending it for the session to avoid cache busting on toggle.
        self.latched_thinking: bool = False
        # Per-instance cache-aware snip state. Lazily initialised on first
        # queue_snip_cache_edits call so sessions that never trigger the
        # feature pay zero overhead.
        self.cached_snip_state: CachedSnipState | None = None
        # Prior-turn cache_read_input_tokens, used to detect server-side
        # cache breaks and reset stale cache_edits state before the next
        # request can send references the server no longer knows about.
        self.prev_cache_read: int = 0
        # Model support is fixed for the provider lifetime — evaluate once.
        self.model_supports_cache_editing: bool = is_model_supported_for_cache_editing(self.model)

    def queue_snip_cache_edits(self, tool_ids: list[str]) -> None:
        """Queue tool_use_ids for server-side deletion via cache_edits API."""
        if self.cached_snip_state is None:
            self.cached_snip_state = CachedSnipState()
        queue_deletions(self.cached_snip_state, tool_ids)

    def reset_snip_state(self) -> None:
        """Clear pending and pinned cache_edits state after full compaction.

        Preserves ``header_latched`` — once committed to the beta header for
        the session, keep sending it to avoid busting the prompt cache.
        """
        if self.cached_snip_state is not None:
            reset_state(self.cached_snip_state)

    def observe_usage_for_cache_break(self, cache_read_tokens: int) -> None:
        """Reset cached_snip state when the server cache has broken.

        Called after every API response. When ``cache_read_tokens`` collapses
        to zero while we still held pinned cache_edits from a prior warm
        turn, the server no longer knows the references — sending them again
        would 4xx on the next turn. Reset preemptively and log so the break
        is observable.
        """
        if detect_unexpected_cache_break(
            self.cached_snip_state, cache_read_tokens, self.prev_cache_read
        ):
            logger.warning(
                "cached_snip: server cache break detected "
                "(prev_cache_read=%d, cache_read=0) — resetting snip state",
                self.prev_cache_read,
            )
            self.reset_snip_state()
        self.prev_cache_read = cache_read_tokens

    async def generate(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
    ) -> str:
        kwargs = self.build_kwargs(messages, system, tools=None)
        apply_betas(kwargs)
        extra_headers = kwargs.pop("extra_headers", None)
        try:
            response = await self.client.post_messages(kwargs, extra_headers)
        except CitadelAPIError as e:
            maybe_raise_quota_exhausted(e)
            maybe_raise_budget_exhausted(e)
            maybe_raise_subscription_suspended(e)
            raise
        usage = response.get("usage", {}) or {}
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        self.observe_usage_for_cache_break(cache_read)
        record_usage(
            Usage(
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            ),
            model_id=self.model,
        )
        record_quota_snapshot(response.get("quota"))
        return "".join(
            block.get("text", "")
            for block in response.get("content", [])
            if block.get("type") == "text"
        )

    async def stream(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
    ) -> AsyncIterator[str]:
        kwargs = self.build_kwargs(messages, system, tools=None)
        apply_betas(kwargs)
        extra_headers = kwargs.pop("extra_headers", None)

        # Mirror stream_with_tools' accounting: input usage rides message_start,
        # output usage + the quota relay ride message_delta. The no-tools path
        # previously dropped both, so a plain streamed turn reported zero tokens.
        input_tokens = 0
        output_tokens = 0
        cache_read = 0
        cache_creation = 0
        latest_quota: dict | None = None

        try:
            async for event in self.client.stream_messages(kwargs, extra_headers):
                etype = event.get("type")
                if etype == "message_start":
                    msg = event.get("message", {}) or {}
                    u = msg.get("usage", {}) or {}
                    input_tokens = u.get("input_tokens", 0) or 0
                    cache_read = u.get("cache_read_input_tokens", 0) or 0
                    cache_creation = u.get("cache_creation_input_tokens", 0) or 0
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")
                elif etype == "message_delta":
                    u = event.get("usage", {}) or {}
                    if u.get("output_tokens") is not None:
                        output_tokens = u.get("output_tokens", 0) or 0
                    if u.get("cache_read_input_tokens") is not None:
                        cache_read = u.get("cache_read_input_tokens", 0) or 0
                    if u.get("cache_creation_input_tokens") is not None:
                        cache_creation = u.get("cache_creation_input_tokens", 0) or 0
                    q = event.get("quota")
                    if q is not None:
                        latest_quota = q

            usage = Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_creation,
            )
            self.observe_usage_for_cache_break(usage.cache_read_input_tokens)
            record_usage(usage, model_id=self.model)
            record_quota_snapshot(latest_quota)
        except CitadelAPIError as e:
            maybe_raise_quota_exhausted(e)
            maybe_raise_budget_exhausted(e)
            maybe_raise_subscription_suspended(e)
            raise

    async def generate_with_tools(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> LLMResponse:
        return await self.generate_with_tools_inner(
            messages,
            system,
            tools,
            super_thinking,
            max_output_tokens,
            skip_cache_write=skip_cache_write,
        )

    def build_kwargs(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None,
        tools: list[ToolDef] | None,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> dict:
        """Build Anthropic API kwargs with structured cache_control breakpoints.

        System prompt: each SystemPromptBlock becomes a separate text entry
        with per-block cache_control (scope + ttl from the block metadata).
        Messages: cache_control placed on the last content block of the last
        user message (last-block-only rule, skipping thinking blocks).
        Tools: session-cached schemas with cache_control on the last tool.

        ``skip_cache_write`` suppresses every one of those breakpoints. Callers
        set it for one-shot requests whose prefix nothing will ever replay -
        summarizer ticks, compaction retries, throwaway forks. Those requests
        diverge from the parent's prefix (a tool-less request shares nothing
        with a tool-bearing one), so writing a cache entry costs the 1.25x/2x
        write premium over the whole history for an entry no later request can
        read. Cache READS are unaffected: a prefix that happens to be cached is
        still served from cache.
        """
        anthropic_messages = to_wire_messages(messages)
        # Message-level cache marker on last user message's last content block
        if not skip_cache_write:
            place_message_cache_marker(anthropic_messages)

        # Cache-aware snip: inject cache_reference + cache_edits AFTER the
        # cache_control marker is placed (so positions are final).
        # header_latched sessions keep receiving the beta header (dropping
        # mid-session would bust the prompt cache), so the model gate only
        # blocks brand-new injection on an unsupported model.
        betas: list[str] = []
        state = self.cached_snip_state
        if state is not None and (
            (cached_snip_prepass_enabled() and self.model_supports_cache_editing)
            or state.header_latched
        ):
            new_deletes = consume_pending_deletes(state)
            anthropic_messages, any_edits = apply_cache_edits_to_messages(
                anthropic_messages, state, new_deletes
            )
            if any_edits or state.header_latched:
                state.header_latched = True
                betas.append(CACHE_EDITING_BETA_HEADER)

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_output_tokens if max_output_tokens is not None else self.max_tokens,
            "messages": anthropic_messages,
        }
        if betas:
            kwargs["betas"] = betas

        if system:
            # Single cache breakpoint on the LAST system block. Anthropic cache
            # breakpoints are cumulative prefixes, so one marker at the end
            # caches the whole (deploy-stable) system as a single prefix and
            # can never overflow Anthropic's 4-breakpoint cap no matter how many
            # blocks features add (soul + craft + citation guidance). The TTL is
            # the configured global_cache_ttl (default "1h") so the knob wired
            # through config/bootstrap/ConsciousnessManager actually reaches the
            # Anthropic request instead of a hardcoded literal.
            sys_blocks: list[dict[str, Any]] = [
                {"type": "text", "text": block.text} for block in system if block.text
            ]
            if sys_blocks and not skip_cache_write:
                sys_blocks[-1]["cache_control"] = {
                    "type": "ephemeral",
                    "ttl": self.global_cache_ttl,
                }
            kwargs["system"] = sys_blocks
            try:
                import hashlib as hl

                joined = "\n\n".join(b.text for b in system if b.text)
                sha = hl.sha256(joined.encode()).hexdigest()[:12]
                head = joined[:160].replace("\n", " ")
                logger.info(
                    "herald.anthropic: system_prompt blocks=%d chars=%d sha=%s head=%s",
                    sum(1 for b in system if b.text),
                    len(joined),
                    sha,
                    head,
                )
            except Exception:
                pass

        if tools:
            anthropic_tools = self.get_cached_tools(tools)
            if anthropic_tools and not skip_cache_write:
                anthropic_tools[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            kwargs["tools"] = anthropic_tools

        return kwargs

    def get_cached_tools(self, tools: list[ToolDef]) -> list[dict]:
        """Return cached tool schemas, recomputing only when the tool set changes."""
        tool_names = frozenset(t.name for t in tools)
        if self.cached_tool_names == tool_names and self.cached_tool_schemas is not None:
            # Only copy the last dict — that's the one the caller mutates
            # to add cache_control. The rest can share references safely.
            result = list(self.cached_tool_schemas)
            if result:
                result[-1] = dict(result[-1])
            return result
        self.cached_tool_schemas = to_wire_tools(tools)
        self.cached_tool_names = tool_names
        self.tool_name_reverse_map = build_tool_name_map(tools)
        result = list(self.cached_tool_schemas)
        if result:
            result[-1] = dict(result[-1])
        return result

    async def generate_with_tools_inner(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None,
        tools: list[ToolDef] | None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> LLMResponse:
        kwargs = self.build_kwargs(
            messages, system, tools, max_output_tokens, skip_cache_write=skip_cache_write
        )
        apply_thinking(kwargs, super_thinking, self)
        apply_betas(kwargs)
        extra_headers = kwargs.pop("extra_headers", None)

        try:
            response = await self.client.post_messages(kwargs, extra_headers)
        except CitadelAPIError as e:
            maybe_raise_quota_exhausted(e)
            maybe_raise_budget_exhausted(e)
            maybe_raise_subscription_suspended(e)
            raise

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                name = block.get("name", "")
                internal_name = self.tool_name_reverse_map.get(name, name)
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""), name=internal_name, input=block.get("input", {})
                    )
                )

        STOP_MAP = {
            "end_turn": StopReason.END_TURN,
            "tool_use": StopReason.TOOL_USE,
            "max_tokens": StopReason.MAX_TOKENS,
            # `StopReason.REFUSAL` has existed in types.py since the enum
            # was written and was produced by neither map, so a refused
            # turn arrived looking exactly like a finished one. The broker
            # already does its half: translate.py maps Bedrock's
            # `content_filtered` and `guardrail_intervened` onto "refusal"
            # before the stream leaves it. The signal reached this line
            # and was flattened here.
            #
            # It costs nothing on Kimi, which rarely refuses. It costs a
            # great deal on a GPT-class model, which refuses often: every
            # such turn is billed, produces nothing, and is recorded as
            # ordinary, and no instrument downstream can tell the town
            # "the model declined" from "the resident had nothing to do".
            "refusal": StopReason.REFUSAL,
        }
        stop = STOP_MAP.get(response.get("stop_reason") or "", StopReason.END_TURN)

        u = response.get("usage", {}) or {}
        q = response.get("quota")
        usage = Usage(
            input_tokens=u.get("input_tokens", 0) or 0,
            output_tokens=u.get("output_tokens", 0) or 0,
            cache_read_input_tokens=u.get("cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=u.get("cache_creation_input_tokens", 0) or 0,
        )
        self.observe_usage_for_cache_break(usage.cache_read_input_tokens)
        record_usage(usage, model_id=self.model)
        record_quota_snapshot(q)
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop,
            usage=usage,
            message_id=response.get("id", ""),
        )

    async def stream_with_tools(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        import json

        kwargs = self.build_kwargs(
            messages, system, tools, max_output_tokens, skip_cache_write=skip_cache_write
        )
        apply_thinking(kwargs, super_thinking, self)
        apply_betas(kwargs)
        extra_headers = kwargs.pop("extra_headers", None)

        STOP_MAP = {
            "end_turn": StopReason.END_TURN,
            "tool_use": StopReason.TOOL_USE,
            "max_tokens": StopReason.MAX_TOKENS,
            # `StopReason.REFUSAL` has existed in types.py since the enum
            # was written and was produced by neither map, so a refused
            # turn arrived looking exactly like a finished one. The broker
            # already does its half: translate.py maps Bedrock's
            # `content_filtered` and `guardrail_intervened` onto "refusal"
            # before the stream leaves it. The signal reached this line
            # and was flattened here.
            #
            # It costs nothing on Kimi, which rarely refuses. It costs a
            # great deal on a GPT-class model, which refuses often: every
            # such turn is billed, produces nothing, and is recorded as
            # ordinary, and no instrument downstream can tell the town
            # "the model declined" from "the resident had nothing to do".
            "refusal": StopReason.REFUSAL,
        }

        try:
            # Open tool calls, keyed by the content-block index the
            # provider gave them.
            #
            # This was three scalars - `current_tool_id`, `current_tool_name`,
            # `current_tool_json` - and `event["index"]` was read nowhere in
            # this method. Anthropic and Bedrock both happen to emit content
            # blocks one at a time, so the bug never fired: every start was
            # followed by its own deltas and its own stop.
            #
            # A provider that interleaves two tool blocks concatenates both
            # argument strings into one. The usual outcome is a
            # `JSONDecodeError`, which is loud and survivable. The outcome
            # that matters is the other one: if the two fragments happen to
            # concatenate into VALID json, the turn calls a real verb with
            # arguments nobody wrote, and nothing anywhere notices. That is
            # the shape TRADE-01 cost this project 6,424 calls to learn -
            # "it returns SUCCESS for an act nobody asked for".
            #
            # Keyed by index, each block accumulates into its own slot and
            # is emitted by its own stop. A stream that opens one block at a
            # time behaves exactly as before; `_NO_INDEX` keeps a provider
            # that omits the field on the single-slot path it already used.
            _NO_INDEX = -1
            open_tools: dict[object, dict[str, str]] = {}
            thinking_text = ""
            thinking_redacted = False
            # Final id/stop/usage are assembled from message_start +
            # message_delta (the SDK's get_final_message() equivalent).
            message_id = ""
            stop = StopReason.END_TURN
            input_tokens = 0
            output_tokens = 0
            cache_read = 0
            cache_creation = 0
            latest_quota: dict | None = None

            async for event in self.client.stream_messages(kwargs, extra_headers):
                etype = event.get("type")

                if etype == "message_start":
                    msg = event.get("message", {}) or {}
                    message_id = msg.get("id", "") or message_id
                    u = msg.get("usage", {}) or {}
                    input_tokens = u.get("input_tokens", 0) or 0
                    cache_read = u.get("cache_read_input_tokens", 0) or 0
                    cache_creation = u.get("cache_creation_input_tokens", 0) or 0

                elif etype == "content_block_start":
                    block = event.get("content_block", {}) or {}
                    btype = block.get("type")
                    if btype == "tool_use":
                        name = block.get("name", "")
                        open_tools[event.get("index", _NO_INDEX)] = {
                            "id": block.get("id", ""),
                            "name": self.tool_name_reverse_map.get(name, name),
                            "json": "",
                        }
                    elif btype in ("thinking", "redacted_thinking"):
                        thinking_text = ""
                        thinking_redacted = btype == "redacted_thinking"

                elif etype == "content_block_delta":
                    delta = event.get("delta", {}) or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        yield StreamEvent(type="text", text=delta.get("text", ""))
                    elif dtype == "input_json_delta":
                        # A delta for a block that was never opened has no
                        # name and no id, so there is nothing to call. It is
                        # dropped here rather than accumulated into whatever
                        # block happens to be open - which is what the old
                        # single buffer did, and is how one call's arguments
                        # reached another call's name.
                        slot = open_tools.get(event.get("index", _NO_INDEX))
                        if slot is not None:
                            slot["json"] += delta.get("partial_json", "")
                    elif dtype == "thinking_delta":
                        thinking_text += delta.get("thinking", "")
                        yield StreamEvent(type="thinking", text=delta.get("thinking", ""))

                elif etype == "content_block_stop":
                    slot = open_tools.pop(event.get("index", _NO_INDEX), None)
                    if slot is not None and slot["id"]:
                        yield StreamEvent(
                            type="tool_call",
                            tool_call=ToolCall(
                                id=slot["id"],
                                name=slot["name"],
                                input=json.loads(slot["json"]) if slot["json"] else {},
                            ),
                        )
                    if thinking_text or thinking_redacted:
                        yield StreamEvent(
                            type="thinking_block",
                            thinking_block=ThinkingBlock(
                                thinking=thinking_text,
                                is_redacted=thinking_redacted,
                            ),
                        )
                        thinking_text = ""
                        thinking_redacted = False

                elif etype == "message_delta":
                    d = event.get("delta", {}) or {}
                    if d.get("stop_reason"):
                        stop = STOP_MAP.get(d["stop_reason"], StopReason.END_TURN)
                    u = event.get("usage", {}) or {}
                    # message_delta carries the running/final output token count.
                    if u.get("output_tokens") is not None:
                        output_tokens = u.get("output_tokens", 0) or 0
                    if u.get("cache_read_input_tokens") is not None:
                        cache_read = u.get("cache_read_input_tokens", 0) or 0
                    if u.get("cache_creation_input_tokens") is not None:
                        cache_creation = u.get("cache_creation_input_tokens", 0) or 0
                    q = event.get("quota")
                    if q is not None:
                        latest_quota = q

            yield StreamEvent(type="stop", stop_reason=stop, message_id=message_id)
            usage = Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_creation,
            )
            self.observe_usage_for_cache_break(usage.cache_read_input_tokens)
            record_usage(usage, model_id=self.model)
            record_quota_snapshot(latest_quota)
            yield StreamEvent(type="usage", usage=usage)
        except CitadelAPIError as e:
            maybe_raise_quota_exhausted(e)
            maybe_raise_budget_exhausted(e)
            maybe_raise_subscription_suspended(e)
            raise


def to_wire_messages(messages: list[Message]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if isinstance(msg.content, str):
            out.append({"role": msg.role, "content": msg.content})
        else:
            out.append(
                {
                    "role": msg.role,
                    "content": blocks_to_wire(msg.content),
                }
            )
    return out


def tool_result_to_wire(
    content: "str | list[ContentBlock]",
) -> "str | list[dict]":
    """Translate ToolResultBlock.content to Anthropic's tool_result content shape.

    Anthropic accepts either a plain string or an array of typed
    content blocks for tool_result.content. When ``content`` is a
    string we pass it through unchanged (legacy shape, every existing
    call site). When it's a list of ContentBlock we walk it and emit
    the array form so multimodal attachments (images) and persisted
    output hints reach the model with full fidelity.

    PersistedBlock becomes a text block carrying an XML retrieval hint
    matching src's <persisted-output> format — the model knows the
    full output exists at the path and can ask to read it.
    """
    if isinstance(content, str):
        return content
    out: list[dict] = []
    for sub in content:
        if isinstance(sub, TextBlock):
            out.append({"type": "text", "text": sub.text})
        elif isinstance(sub, ImageBlock):
            out.append({"type": "image", "source": sub.source})
        elif isinstance(sub, PersistedBlock):
            out.append({"type": "text", "text": format_persisted_block_xml(sub)})
        # Other block types (ToolUseBlock, ThinkingBlock, nested
        # ToolResultBlock) shouldn't appear inside a tool_result
        # content list — silently skip if they do.
    return out


def blocks_to_wire(blocks: list[ContentBlock]) -> list[dict]:
    result: list[dict] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            # Names in history must match the sanitised form Anthropic accepted
            # on the original turn — otherwise the assistant message fails the
            # tools.*.custom.name pattern check on resend.
            result.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": sanitize_tool_name(block.name),
                    "input": block.input,
                }
            )
        elif isinstance(block, ToolResultBlock):
            result.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": tool_result_to_wire(block.content),
                    "is_error": block.is_error,
                }
            )
        elif isinstance(block, ThinkingBlock):
            if not block.is_redacted:
                result.append({"type": "thinking", "thinking": block.thinking})
    return result


def apply_thinking(
    kwargs: dict,
    super_thinking: bool,
    provider: Provider | None = None,
) -> None:
    """Enable extended thinking when super_thinking is active.

    Once thinking is enabled in any request during a session, it is latched
    ON for the remainder. A mid-session toggle would bust the cache prefix
    because the thinking config changes the request shape. Latching prevents
    that — the only reset is session end or compact.
    """
    from src.herald.types import SUPER_THINKING_BUDGET, THINKING_LATCH_BUDGET

    if super_thinking:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": SUPER_THINKING_BUDGET}
        if provider is not None:
            provider.latched_thinking = True
    elif provider is not None and provider.latched_thinking:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": THINKING_LATCH_BUDGET}


def place_message_cache_marker(messages: list[dict]) -> None:
    """Place cache_control on exactly ONE message — the last one.

    Src explicitly mandates one marker per request: having two wastes KV
    memory on the cache backend because the second-to-last position's
    local-attention pages survive an extra turn unnecessarily.

    For the last message (regardless of role):
    - User messages: marker on the last content block.
    - Assistant messages: marker on the last non-thinking content block
      (skip thinking/redacted_thinking so the breakpoint is at a clean
      content boundary).
    - String content: converted to a single text block to place the marker.
    """
    for msg in reversed(messages):
        content = msg.get("content")
        role = msg.get("role", "")

        # Convert string content to list form so the marker can be placed
        if isinstance(content, str):
            msg["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
            return

        if not isinstance(content, list) or not content:
            continue

        if role == "user":
            content[-1]["cache_control"] = {"type": "ephemeral"}
            return
        elif role == "assistant":
            for block in reversed(content):
                btype = block.get("type", "")
                if btype not in ("thinking", "redacted_thinking"):
                    block["cache_control"] = {"type": "ephemeral"}
                    return
        # If we get here, content was all thinking blocks — skip to previous message


def to_wire_tools(tools: list[ToolDef]) -> list[dict]:
    """Build the Anthropic-Messages ``tools`` array from provider-neutral ``ToolDef``s.

    Tools with ``defer_loading=True`` get the flag emitted on their schema —
    Anthropic API ships their name to the model but withholds the full schema
    until tool search is invoked. Cuts cache-write tokens on cold turns.

    Anthropic's Messages API enforces ``^[a-zA-Z0-9_-]{1,128}$`` on tool names
    and 400s on internal forms like ``ad:gmail.send_message`` (colon + dot).
    Sanitise on the way out; the provider instance reverse-maps responses
    via ``tool_name_reverse_map``.
    """
    out: list[dict] = []
    for t in tools:
        entry: dict = {
            "name": sanitize_tool_name(t.name),
            "description": t.description,
            "input_schema": t.input_schema,
        }
        if getattr(t, "defer_loading", False):
            entry["defer_loading"] = True
        out.append(entry)
    return out
