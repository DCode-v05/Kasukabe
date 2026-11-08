"""BrokerService - wires every other module into the one request pipeline
POST /v1/messages runs, for both the streaming and non-streaming case.

Pipeline (broker-lanes track revision - six lanes + spend governor
replace the original single rate bucket):

  1. auth.resolve_agent_id       - agent id from the auth header
  2. allowlist.check_model_allowed - 403 hard fail outside the allowlist
  3. spend.SpendGovernor.should_block_dispatch - 429 once the process-wide
                                  hard USD cap has been reached (see
                                  broker/spend.py's module docstring)
  4. lanes.classify_purpose + LanePool.select - which of the six lanes
                                  (model x region) serves this call; see
                                  broker/lanes.py's module docstring for
                                  why this is purpose-driven, not simply
                                  "whatever model the client asked for"
  5. translate.build_converse_request - Anthropic wire -> Bedrock converse,
                                  using the SELECTED lane's model
  6. pricing.estimate_cost_cents_ceiling + ledger.get_payer_balance
                                  - pre-dispatch wallet check, refuse if
                                    insufficient (DESIGN.md section 4:
                                    resolves to the parent for a minor);
                                    priced against the lane's actual model,
                                    since that is what will really be
                                    billed
  7. lane.bucket.acquire()       - the fair per-lane FIFO queue
  8. lane.gateway.converse / converse_stream - the actual call
  9. pricing.exact_cost_nano_dollars + CostAccountant.debit_cents +
     ledger.debit               - post-response exact metering
  10. lane.circuit.record_success/record_throttle/record_other_failure
                                  - feeds broker/lanes.py's CircuitBreaker
                                    the real outcome
  11. spend.SpendGovernor.record_spend - real cost only, may flip the
                                  governor to WARNING or HALTED (one-shot
                                  broker.spend.warned / system.sim.halted
                                  events on a state transition)
  12. trace.BatchWriter.record (x2: the call trace, and a broker.* event)
                                  - buffered, flushed async, never
                                    blocking the response already being
                                    returned to herald

Every step through step 6 either succeeds or raises a
broker.errors.BrokerError; broker/app.py turns that into the matching
HTTP status + Anthropic-shaped error body. A rejection at any of steps
2/3/4/6 still gets a trace + event record (so "how many calls got
refused, and why" is itself measurable) - only step 8 dispatching is
money-in-flight; everything before it is free to record and retry. A
lane reserved in step 4 (LanePool.select may claim a circuit breaker's
half-open trial slot) that then gets refused at step 5 or 6 releases that
reservation (`lane.circuit.release_unused_reservation()`) rather than
holding it hostage for a call that never actually dispatched.

Step 9's ledger.debit() is the one place a failure must NOT follow that
"raise and let app.py turn it into an error response" rule: by the time
settle() runs, Bedrock has already produced (and this module has already
built) the response the caller is about to receive. world's real Ledger
enforces a strict non-negative balance floor (world/ledger/core.py's
apply_movement, confirmed while building this module - see
broker/ledger.py's docstring) and CAN reject a debit outright, most
plausibly when two of one payer's concurrent calls race between their
own pre-dispatch checks and their own debits. settle() catches that,
credits the un-collected cost back onto CostAccountant's remainder (see
pricing.py's credit_back_cents - the cost is billed on this payer's next
call instead of lost), logs it, and still returns the real inference
result. A ledger outage or a genuine accounting bug must never turn a
successful, already-paid-for-in-real-AWS-dollars model response into a
500 the agent's herald then has to retry (retrying would call Bedrock
AGAIN, doubling the real spend for a bookkeeping problem that has
nothing to do with whether the model call itself worked).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from broker.allowlist import check_model_allowed
from broker.auth import resolve_agent_id
from broker.config import BrokerSettings
from broker.fit import fit_to_context_window
from broker.errors import (
    BrokerError,
    InsufficientFundsError,
    InvalidRequestError,
    SpendCapExceededError,
    UpstreamOverloadedError,
)
from broker.lanes import CallClass, Lane, LanePool, classify_purpose
from broker.purpose import purpose_and_source
from broker.ledger import LedgerClient
from broker.models import MessagesRequest
from broker.pricing import CostAccountant, estimate_cost_cents_ceiling, exact_cost_nano_dollars
from broker.spend import SpendGovernor, SpendState
from broker.tokens import estimate_tokens
from broker.trace import BatchWriter, BrokerEvent, TraceRecord, new_event_id, new_trace_id, now_s
from broker.translate import (
    anthropic_sse_events_from_converse_stream,
    build_converse_request,
    converse_response_to_anthropic,
    exact_usage_from_converse_response,
)

logger = logging.getLogger("broker.service")

Clock = Callable[[], float]


def get_header(headers: dict[str, str], name: str, default: str = "") -> str:
    lowered_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered_name:
            return value
    return default


def request_text_chars(req: MessagesRequest) -> int:
    """Sum of every text-bearing character in the request, for the
    input-token estimate. Only text content contributes - tool_use/
    tool_result/image blocks are real input tokens too, but char-counting
    a JSON input dict or a base64 image blob would wildly overestimate
    (base64 alone is ~1.3 chars/byte, nowhere near ~4 chars/token), so
    they're deliberately excluded from this estimate. Undercounting the
    wallet-check estimate is the direction that matters here: the
    max_tokens-as-output-ceiling half of the estimate (see
    prepare_call below) is what keeps the overall estimate conservative,
    not text-character precision on the input half.
    """
    total = 0
    for message in req.messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(content)
        else:
            for block in content:
                if block.get("type") == "text":
                    total += len(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    tr_content = block.get("content", "")
                    if isinstance(tr_content, str):
                        total += len(tr_content)
                    else:
                        total += sum(
                            len(sub.get("text", ""))
                            for sub in tr_content
                            if sub.get("type") == "text"
                        )
    if isinstance(req.system, str):
        total += len(req.system)
    elif isinstance(req.system, list):
        total += sum(len(b.get("text", "")) for b in req.system)

    if req.tools:
        try:
            total += len(json.dumps(req.tools, default=str))
        except (TypeError, ValueError):
            pass
    return total


@dataclass
class PreparedCall:
    agent_id: str
    payer_agent_id: str
    model: str
    requested_model: str
    region: str
    call_class: str
    lane: Lane
    purpose: str
    bedrock_request: dict[str, Any]
    request_body: dict[str, Any]
    estimated_input_tokens: int
    rate_limit_wait_s: float
    start_monotonic: float
    purpose_source: "str | None" = None
    tick: "int | None" = None


class StreamAccumulator:
    """Reassembles a best-effort final response body from the Anthropic
    SSE events service.py forwards, for the trace record's response_body
    field only - never for billing (see translate.py's on_usage callback
    for the exact numbers that feed the ledger debit)."""

    DELTA_FIELDS: dict[str, tuple[str, str]] = {
        "text_delta": ("text", "text"),
        "thinking_delta": ("thinking", "thinking"),
        "signature_delta": ("signature", "thinking"),
        "redacted_thinking_delta": ("data", "thinking"),
        "input_json_delta": ("partial_json", "tool_use"),
    }

    OPENING_BLOCK_FOR_DELTA: dict[str, dict[str, Any]] = {
        "text_delta": {"type": "text", "text": ""},
        "thinking_delta": {"type": "thinking", "thinking": ""},
        "signature_delta": {"type": "thinking", "thinking": ""},
        "redacted_thinking_delta": {"type": "redacted_thinking", "data": ""},
        "input_json_delta": {"type": "tool_use", "id": "", "name": "", "input": {}},
    }

    def __init__(self) -> None:
        self.blocks: dict[int, dict[str, Any]] = {}
        self.order: list[int] = []
        self.stop_reason: "str | None" = None
        self.deltas_seen: dict[str, int] = {"text": 0, "thinking": 0, "tool_use": 0}

    def open_block(self, index: int, block: dict[str, Any]) -> dict[str, Any]:
        if index not in self.blocks:
            self.order.append(index)
        stored = dict(block)
        if stored.get("type") == "tool_use":
            stored["partial_json"] = ""
        self.blocks[index] = stored
        return stored

    def ingest(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "content_block_start":
            self.open_block(event["index"], event["content_block"])
        elif etype == "content_block_delta":
            index = event["index"]
            delta = event.get("delta", {})
            dtype = delta.get("type", "")
            field_and_counter = self.DELTA_FIELDS.get(dtype)
            if field_and_counter is None:
                return
            field, counter = field_and_counter
            block = self.blocks.get(index)
            if block is None:
                opening = self.OPENING_BLOCK_FOR_DELTA.get(dtype)
                if opening is None:
                    return
                block = self.open_block(index, opening)
            self.deltas_seen[counter] = self.deltas_seen.get(counter, 0) + 1
            block[field] = block.get(field, "") + delta.get(field, "")
        elif etype == "content_block_stop":
            self.seal(self.blocks.get(event["index"]))
        elif etype == "message_delta":
            stop_reason = event.get("delta", {}).get("stop_reason")
            if stop_reason:
                self.stop_reason = stop_reason

    def seal(self, block: "dict[str, Any] | None") -> None:
        """Turn a tool block's accumulated JSON text into its input dict.

        Split out of the content_block_stop branch so content() can also
        call it: a stream cut off before its stop event still holds a
        complete, parseable argument list often enough to be worth
        keeping, and a half-recorded tool call is a finding while a
        silently dropped one is not.
        """
        if block is None or block.get("type") != "tool_use":
            return
        raw = block.pop("partial_json", "")
        try:
            block["input"] = json.loads(raw) if raw else block.get("input", {})
        except json.JSONDecodeError:
            block["input"] = block.get("input", {})

    def content(self) -> list[dict[str, Any]]:
        blocks = [self.blocks[i] for i in self.order if i in self.blocks]
        for block in blocks:
            self.seal(block)
        return blocks

    def delta_counts(self) -> dict[str, int]:
        """How many deltas of each kind this turn actually carried.

        Build Spec v2 section 7.2. Recorded alongside the assembled body so
        an assembly fault shows up as a mismatch between what arrived and
        what was kept, rather than as a plausible-looking empty result that
        reads exactly like a model with nothing to say.
        """
        return dict(self.deltas_seen)


TEXT_BEARING_KEYS: dict[str, str] = {
    "text": "text",
    "thinking": "thinking",
    "redacted_thinking": "data",
}


def reconcile_usage_and_content(
    output_tokens: int, body: "dict[str, Any] | None"
) -> "str | None":
    """Did we record what we were billed for? Returns the problem, or None.

    Build Spec v2 section 7.3, and the standing check that would have caught
    defect D1 on its first occurrence instead of its 938th. Bedrock reporting
    output tokens means something was generated; a body carrying no content,
    or only empty blocks, is a record that contradicts the invoice.

    Deliberately NOT an exception. A call whose content was lost still
    produced a real response for the agent and must still settle, be billed,
    and reach the caller - the wrong response to a bookkeeping failure is to
    throw away the thing that succeeded (the same reasoning settle() already
    applies to a failed ledger debit). This returns a string for the caller to
    record and alarm on.

    `body is None` is the legitimate no-content case: a call refused before
    dispatch never generated anything and owes no content.
    """
    if body is None or output_tokens <= 0:
        return None
    blocks = body.get("content") or []
    if not blocks:
        return (
            f"billed {output_tokens} output tokens and recorded no content blocks - "
            "streamed content was lost between Bedrock and the trace"
        )
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            return None
        key = TEXT_BEARING_KEYS.get(block.get("type", ""))
        if key is None or block.get(key):
            return None
    return (
        f"billed {output_tokens} output tokens and every recorded block is empty - "
        "the record shows structure and no words"
    )


@dataclass
class BrokerService:
    settings: BrokerSettings
    ledger: LedgerClient
    lane_pool: LanePool
    spend_governor: SpendGovernor
    cost_accountant: CostAccountant
    trace_writer: "BatchWriter[TraceRecord]"
    event_writer: "BatchWriter[BrokerEvent]"
    clock: Clock = field(default=time.monotonic)
    content_loss: list[dict[str, Any]] = field(default_factory=list)

    def emit_event(self, event_type: str, agent_id: str, model: str, payload: dict[str, Any]) -> None:
        self.event_writer.record(
            BrokerEvent(
                id=new_event_id(),
                type=event_type,
                agent_id=agent_id,
                model=model,
                payload=payload,
                created_at=now_s(),
            )
        )

    def note_served_tier(self, response: dict[str, Any]) -> None:
        """Say so when Bedrock served a tier other than the one we asked for.

        The whole defect this project just found was paying Standard while
        pricing Flex, and a silent downgrade would put it straight back
        there - the trace would go on reading $0.36 per million while the
        bill read $0.72. Bedrock reports the tier that actually served the
        request; this is the one place that reads it.

        A warning rather than a refusal: the answer is already paid for and
        already correct, and throwing it away would cost more than the
        mispricing it is warning about.
        """
        served = response.get("serviceTier")
        if isinstance(served, dict):
            served = served.get("type")
        asked = self.settings.bedrock_service_tier
        if served and served != asked:
            logger.warning(
                "Bedrock served serviceTier %r for a request that asked for %r - "
                "this call is priced at the %s rate and billed at the %s one",
                served, asked, asked, served,
            )

    def record_lane_outcome(self, lane: Lane, error: "BrokerError | None") -> None:
        """Feed a dispatch's real outcome back into its lane's circuit
        breaker - step 10 of the pipeline (module docstring). Only ever
        called for a lane that actually reached `lane.bucket.acquire()`
        and dispatched to `lane.gateway`; a call rejected earlier
        (allowlist, spend cap, wallet) never got this far and instead
        released its reservation via `release_unused_reservation()` in
        `prepare_call`. `UpstreamOverloadedError` is the ONLY outcome the
        breaker counts as a throttle (broker/lanes.py's CircuitBreaker
        docstring: "the ONLY failure kind this breaker counts"); every
        other error is a real dispatch that failed for an unrelated
        reason and must not extend a throttle-driven cooldown.
        """
        if error is None:
            lane.circuit.record_success()
        elif isinstance(error, UpstreamOverloadedError):
            lane.circuit.record_throttle()
        else:
            lane.circuit.record_other_failure()

    def record_spend(self, prepared: "PreparedCall", cost_nano_dollars: int) -> None:
        """Fold this call's REAL cost into the process-wide governor and
        emit a one-shot event exactly on a state transition - step 11 of
        the pipeline (module docstring), and broker/spend.py's module
        docstring: "signal the world to checkpoint needs to happen
        exactly once per threshold crossing". `record_spend` itself is a
        no-op for `cost_nano_dollars <= 0` (spend.py), so a rejected or
        zero-usage call never touches the governor's state or fires
        either event below.
        """
        previous_state = self.spend_governor.state
        new_state = self.spend_governor.record_spend(cost_nano_dollars)
        if new_state == previous_state:
            return
        snapshot = self.spend_governor.snapshot()
        payload = {
            "spent_usd": snapshot.spent_usd,
            "cap_usd": snapshot.cap_usd,
            "fraction_used": snapshot.fraction_used,
            "burn_rate_usd_per_hr": snapshot.burn_rate_usd_per_hr,
        }
        if new_state == SpendState.WARNING:
            self.emit_event("broker.spend.warned", prepared.agent_id, prepared.model, payload)
        elif new_state == SpendState.HALTED:
            self.emit_event("system.sim.halted", prepared.agent_id, prepared.model, payload)

    async def prepare_call(self, raw_body: dict[str, Any], headers: dict[str, str]) -> PreparedCall:
        agent_id = resolve_agent_id(
            headers,
            self.settings.auth_shared_secret,
            peer=headers.get("x-kasukabe-peer"),
        )
        purpose, purpose_source = purpose_and_source(
            get_header(headers, "X-Kasukabe-Purpose", ""), raw_body
        )
        call_class = classify_purpose(purpose)

        try:
            req = MessagesRequest.model_validate(raw_body)
        except ValidationError as e:
            errors = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
            )
            raise InvalidRequestError(f"request does not match the Messages API shape: {errors}") from None

        try:
            check_model_allowed(req.model)
        except BrokerError as e:
            self.record_rejection(
                agent_id, req.model, req.model, purpose, call_class, raw_body, e,
                purpose_source=purpose_source,
            )
            raise

        if self.spend_governor.should_block_dispatch():
            snapshot = self.spend_governor.snapshot()
            error = SpendCapExceededError(
                f"process-wide spend cap of ${snapshot.cap_usd:.2f} has been reached "
                f"(${snapshot.spent_usd:.2f} spent) - dispatch refused"
            )
            self.record_rejection(
                agent_id, req.model, req.model, purpose, call_class, raw_body, error,
                purpose_source=purpose_source,
            )
            raise error

        try:
            lane = self.lane_pool.select(call_class)
        except BrokerError as e:
            self.record_rejection(
                agent_id, req.model, req.model, purpose, call_class, raw_body, e,
                purpose_source=purpose_source,
            )
            raise

        try:
            raw_body, fit_note = fit_to_context_window(raw_body, lane.model)
        except BrokerError as e:
            lane.circuit.release_unused_reservation()
            self.record_rejection(
                agent_id, lane.model, req.model, purpose, call_class, raw_body, e,
                region=lane.region, purpose_source=purpose_source,
            )
            raise
        if fit_note is not None:
            req = MessagesRequest.model_validate(raw_body)
            logger.warning("%s: %s", agent_id, fit_note)

        try:
            bedrock_request = build_converse_request(
                model=lane.model,
                messages=req.messages,
                system=req.system,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                top_p=req.top_p,
                top_k=req.top_k,
                stop_sequences=req.stop_sequences,
                tools=req.tools,
                tool_choice=req.tool_choice,
                thinking=self.thinking_for(req, purpose),
                service_tier=self.settings.bedrock_service_tier,
            )
        except BrokerError as e:
            lane.circuit.release_unused_reservation()
            self.record_rejection(
                agent_id, lane.model, req.model, purpose, call_class, raw_body, e,
                region=lane.region, purpose_source=purpose_source,
            )
            raise

        estimated_input_tokens = estimate_tokens("x" * request_text_chars(req))
        estimate_cents = estimate_cost_cents_ceiling(lane.model, estimated_input_tokens, req.max_tokens)

        try:
            payer = await self.ledger.get_payer_balance(
                agent_id, purpose=purpose, amount_cents=estimate_cents,
            )
        except BrokerError as e:
            lane.circuit.release_unused_reservation()
            self.record_rejection(
                agent_id, lane.model, req.model, purpose, call_class, raw_body, e,
                region=lane.region, purpose_source=purpose_source,
            )
            raise

        if payer.balance_cents < estimate_cents:
            error = InsufficientFundsError(
                f"payer {payer.payer_agent_id!r} balance {payer.balance_cents} cents is below "
                f"the {estimate_cents} cent worst-case estimate for this call"
            )
            lane.circuit.release_unused_reservation()
            self.record_rejection(
                agent_id,
                lane.model,
                req.model,
                purpose,
                call_class,
                raw_body,
                error,
                payer.payer_agent_id,
                region=lane.region,
                tick=payer.tick,
            )
            raise error

        wait_s = await lane.bucket.acquire()
        if wait_s > 0:
            self.emit_event(
                "broker.rpm.throttled",
                agent_id,
                lane.model,
                {"waited_ms": int(wait_s * 1000), "region": lane.region, "lane_id": lane.id},
            )

        return PreparedCall(
            agent_id=agent_id,
            payer_agent_id=payer.payer_agent_id,
            model=lane.model,
            requested_model=req.model,
            region=lane.region,
            call_class=call_class.value,
            lane=lane,
            purpose=purpose,
            purpose_source=purpose_source,
            bedrock_request=bedrock_request,
            request_body=raw_body,
            estimated_input_tokens=estimated_input_tokens,
            rate_limit_wait_s=wait_s,
            start_monotonic=self.clock(),
            tick=payer.tick,
        )

    def thinking_for(self, req, purpose: str) -> "dict[str, Any] | None":
        """The `thinking` parameter this call should carry, if any.

        INFRA-06. The caller's own `thinking` always wins - a client that
        asked for reasoning explicitly is not overruled by an operator
        default, and a client that asked for it to be DISABLED is not
        quietly re-enabled. `model_extra`, not getattr-with-a-default,
        for the reason the line below it already gives: Build Spec v2
        clause 4.4 exists because a silent default is how the dormancy
        counter reported zero for every run ever made.

        Otherwise: inject only when the operator has turned the knob on
        AND this purpose is one of the ones it covers. `auto:agent_turn`
        alone by default - a summariser that reasons is paying thinking
        tokens to compress text, and threshold 9.5 asks about the turn.

        `reasoning_config_from_thinking` still caps the budget against
        max_tokens downstream, so this returns the request and not the
        final number. Two places deciding one budget is how they drift.
        """
        asked = (req.model_extra or {}).get("thinking")
        if asked is not None:
            return asked
        if not self.settings.request_reasoning:
            return None
        if purpose not in self.settings.request_reasoning_purposes:
            return None
        return {
            "type": "enabled",
            "budget_tokens": self.settings.request_reasoning_budget_tokens,
        }

    def record_rejection(
        self,
        agent_id: str,
        model: str,
        requested_model: str,
        purpose: str,
        call_class: CallClass,
        raw_body: dict[str, Any],
        error: BrokerError,
        payer_agent_id: "str | None" = None,
        region: "str | None" = None,
        tick: "int | None" = None,
        purpose_source: "str | None" = None,
    ) -> None:
        """INFRA-02 note on `tick`.

        Six of the seven rejection sites fire BEFORE `get_payer_balance`
        has been called - a bad model, a malformed body, a tripped lane -
        and the payer response is the only place the world's clock reaches
        the broker. So those rows honestly have no tick, and pass None.

        That is a real limit, not an oversight: a rejection that happens
        before the broker has spoken to the world cannot know what game
        time it is. The one site that rejects AFTER the balance is known
        (the spend-cap refusal, which is what a broke resident produces)
        passes it, and that is the rejection anyone will actually want to
        bucket by day.
        """
        self.trace_writer.record(
            TraceRecord(
                id=new_trace_id(),
                agent_id=agent_id,
                payer_agent_id=payer_agent_id or agent_id,
                tick=tick,
                model=model,
                requested_model=requested_model,
                region=region,
                call_class=call_class.value,
                purpose=purpose,
                purpose_source=purpose_source,
                request_body=raw_body,
                response_body=error.detail or error.to_anthropic_body(),
                stop_reason=None,
                input_tokens=0,
                output_tokens=0,
                cost_nano_dollars=0,
                cents_debited=0,
                latency_ms=0,
                rate_limit_wait_ms=0,
                success=False,
                error_type=type(error).__name__,
                error_message=error.message,
                failed_at=error.failed_at,
                created_at=now_s(),
            )
        )
        self.emit_event(
            "broker.call.rejected",
            agent_id,
            model,
            {"error_type": type(error).__name__, "error_message": error.message},
        )

    async def settle(
        self,
        prepared: PreparedCall,
        input_tokens: int,
        output_tokens: int,
        stop_reason: "str | None",
        response_body: "dict[str, Any] | None",
        error: "BrokerError | None",
    ) -> int:
        """Post-response exact metering: compute the real cost, debit the
        ledger, record the trace + event. Called for both success and a
        Bedrock-side failure that happened after dispatch (a failed call
        can still have burned partial tokens depending on where in
        generation Bedrock failed; 0/0 usage is passed for a failure with
        no usable partial usage, which costs and therefore debits
        nothing - see the call sites in handle_messages/handle_stream).

        Returns the cents actually debited (0 on failure with no usage).
        """
        latency_ms = int((self.clock() - prepared.start_monotonic) * 1000)
        cost_nano = exact_cost_nano_dollars(prepared.model, input_tokens, output_tokens)
        self.record_spend(prepared, cost_nano)
        cents_debited = 0
        if cost_nano > 0:
            cents_debited = self.cost_accountant.debit_cents(prepared.payer_agent_id, cost_nano)
            if cents_debited > 0:
                try:
                    await self.ledger.debit(
                        prepared.agent_id,
                        cents_debited,
                        purpose=prepared.purpose,
                        metadata={
                            "model": prepared.model,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        },
                    )
                except Exception:
                    logger.exception(
                        "broker.service: ledger debit failed for payer %r (agent %r), "
                        "%d cents credited back to be re-billed on the next call",
                        prepared.payer_agent_id,
                        prepared.agent_id,
                        cents_debited,
                    )
                    self.cost_accountant.credit_back_cents(prepared.payer_agent_id, cents_debited)
                    cents_debited = 0

        problem = reconcile_usage_and_content(output_tokens, response_body)
        if problem is not None:
            self.content_loss.append(
                {
                    "agent_id": prepared.agent_id,
                    "model": prepared.model,
                    "region": prepared.region,
                    "output_tokens": output_tokens,
                    "problem": problem,
                }
            )
            logger.error(
                "broker.service: CONTENT LOSS for agent %r on %s@%s - %s",
                prepared.agent_id,
                prepared.model,
                prepared.region,
                problem,
            )
            self.emit_event(
                "broker.content.lost",
                prepared.agent_id,
                prepared.model,
                {"output_tokens": output_tokens, "problem": problem},
            )

        self.trace_writer.record(
            TraceRecord(
                id=new_trace_id(),
                agent_id=prepared.agent_id,
                payer_agent_id=prepared.payer_agent_id,
                tick=prepared.tick,
                model=prepared.model,
                requested_model=prepared.requested_model,
                region=prepared.region,
                call_class=prepared.call_class,
                purpose=prepared.purpose,
                purpose_source=prepared.purpose_source,
                request_body=prepared.request_body,
                response_body=response_body,
                stop_reason=stop_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_nano_dollars=cost_nano,
                cents_debited=cents_debited,
                latency_ms=latency_ms,
                rate_limit_wait_ms=int(prepared.rate_limit_wait_s * 1000),
                success=error is None,
                error_type=type(error).__name__ if error else None,
                error_message=error.message if error else None,
                failed_at=error.failed_at if error else None,
                created_at=now_s(),
            )
        )
        self.emit_event(
            "broker.call.completed" if error is None else "broker.call.rejected",
            prepared.agent_id,
            prepared.model,
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cents_debited": cents_debited,
                "latency_ms": latency_ms,
            },
        )
        return cents_debited

    async def handle_messages(self, raw_body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """Non-streaming POST /v1/messages."""
        prepared = await self.prepare_call(raw_body, headers)
        try:
            response = await prepared.lane.gateway.converse(prepared.bedrock_request)
        except BrokerError as e:
            self.record_lane_outcome(prepared.lane, e)
            await self.settle(prepared, 0, 0, None, e.detail or e.to_anthropic_body(), e)
            raise
        self.record_lane_outcome(prepared.lane, None)
        self.note_served_tier(response)

        input_tokens, output_tokens = exact_usage_from_converse_response(response)
        anthropic_response = converse_response_to_anthropic(response, prepared.model)
        await self.settle(
            prepared,
            input_tokens,
            output_tokens,
            anthropic_response["stop_reason"],
            anthropic_response,
            None,
        )
        return anthropic_response

    async def handle_messages_stream(
        self, raw_body: dict[str, Any], headers: dict[str, str]
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming POST /v1/messages. Yields Anthropic SSE event dicts;
        broker/app.py serializes each with translate.sse_line.

        A BrokerError raised before the first event is yielded (auth,
        allowlist, wallet, or Bedrock rejecting the call before any
        tokens streamed) propagates to app.py, which still has a chance
        to return a proper error status. A BrokerError raised mid-stream
        (Bedrock failing after already sending events) cannot change an
        HTTP status the client has already started receiving as 200 -
        it ends the SSE stream by letting the exception propagate out of
        this generator, which is exactly how herald's own transport
        layer treats a mid-stream drop from citadel (see
        engine/src/herald/transport.py's stream_messages: "mid-stream
        (would double-emit) or out of attempts -> raise").
        """
        prepared = await self.prepare_call(raw_body, headers)
        usage_holder: dict[str, int] = {}

        def on_usage(input_tokens: int, output_tokens: int) -> None:
            usage_holder["input_tokens"] = input_tokens
            usage_holder["output_tokens"] = output_tokens

        accumulator = StreamAccumulator()
        try:
            bedrock_events = prepared.lane.gateway.converse_stream(prepared.bedrock_request)
            async for event in anthropic_sse_events_from_converse_stream(
                bedrock_events, prepared.model, prepared.estimated_input_tokens, on_usage
            ):
                accumulator.ingest(event)
                yield event
        except BrokerError as e:
            self.record_lane_outcome(prepared.lane, e)
            input_tokens = usage_holder.get("input_tokens", 0)
            output_tokens = usage_holder.get("output_tokens", 0)
            await self.settle(
                prepared,
                input_tokens,
                output_tokens,
                accumulator.stop_reason,
                {"content": accumulator.content()} if accumulator.order else None,
                e,
            )
            raise
        self.record_lane_outcome(prepared.lane, None)

        input_tokens = usage_holder.get("input_tokens", 0)
        output_tokens = usage_holder.get("output_tokens", 0)
        await self.settle(
            prepared,
            input_tokens,
            output_tokens,
            accumulator.stop_reason,
            {"content": accumulator.content(), "stop_reason": accumulator.stop_reason},
            None,
        )

    async def close(self) -> None:
        await self.trace_writer.close()
        await self.event_writer.close()
        close_ledger: "Callable[[], Awaitable[None]] | None" = getattr(self.ledger, "close", None)
        if close_ledger is not None:
            await close_ledger()
