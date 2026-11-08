"""The six-lane Bedrock routing pool.

TASK spec (broker-lanes track): "Replace the single rate bucket with a
SIX LANE pool: kimi@{ap-south-1,us-east-1,us-west-2} +
deepseek@{same three}. Each lane owns its own 100 RPM token bucket and
its own regional boto3 client. Routing: agent cognition prefers Kimi
lanes; background work (learning centre, dream probes, importance
scoring, summarizer) uses DeepSeek lanes; cognition may overflow to
DeepSeek when all Kimi lanes are saturated, and the trace MUST record
which lane+model actually served each call (research validity depends on
it). Circuit-break a lane on repeated throttles, never spin-retry."

Design decision worth flagging explicitly: routing here is driven ENTIRELY
by the call's `purpose` (via `classify_purpose` below, fed by the existing
`X-Kasukabe-Purpose` header - see broker/service.py's module docstring and
broker/README.md's "Purpose tagging" section), not by the `model` field
the client's request happens to name. broker/allowlist.py still hard-fails
a request whose `model` isn't one of the two allowlisted ids (that check
is unchanged, still runs first), but which of the six LANES actually
serves an allowed request is this module's decision alone. This makes
broker the single source of truth for the Kimi-preferred/DeepSeek-
background/overflow-on-saturation policy the TASK spec describes,
independent of whatever model string any given herald build happens to
send - important because this "broker-lanes" track does not control, and
has no guarantee is in sync with, whatever the engine/herald side chooses
to request per call type. broker/service.py's PreparedCall carries both
`requested_model` (what the client asked for) and `model` (the lane that
actually served it, i.e. what got billed) so a mismatch between the two
is itself a queryable research signal, not a hidden discrepancy.

Two building blocks:
  - CircuitBreaker: per-lane. Opens after `threshold` CONSECUTIVE Bedrock
    THROTTLING responses (not any error - see its docstring), stays open
    for `cooldown_s`, then allows exactly one half-open trial call before
    deciding to close (success) or re-open (still throttled). "Never
    spin-retry" is enforced one layer up: broker/service.py makes exactly
    ONE dispatch attempt per incoming call, no in-process retry loop: this
    class is what stops ROUTING NEW calls at a lane that just proved,
    repeatedly, that it can't serve them, rather than finding that out
    again on every subsequent call.
  - LanePool: owns all six Lane objects (bucket + circuit + gateway,
    keyed by (model, region)) and `select()`, the synchronous routing
    decision described above. `select()` never awaits and never consumes
    a rate-limit token itself - it only decides WHICH lane a caller
    should then `await lane.bucket.acquire()` against.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from broker.bedrock import BedrockGateway
from broker.config import (
    BEDROCK_LANE_REGIONS,
    DEEPSEEK_MODEL_ID,
    KIMI_MODEL_ID,
    MODEL_ALLOWLIST,
    QWEN_MODEL_ID,
)
from broker.config import BrokerSettings, OPENAI_MODELS, OPENAI_REGION
from broker.errors import UpstreamOverloadedError
from broker.rate_limit import TokenBucket

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class CallClass(str, Enum):
    COGNITION = "cognition"
    BACKGROUND = "background"


BACKGROUND_PURPOSE_PREFIXES: tuple[str, ...] = (
    "learning_centre",
    "dream_probe",
    "importance_scoring",
    "summarizer",
)


DERIVED_PREFIX = "auto:"


def derive_purpose(declared: str, body: dict) -> str:
    """What this call was FOR, from the request itself when nobody said.

    Every call in every run so far logged `purpose="unspecified"`, because
    the header that carries it is set by nothing: the engine's own call
    site is in frozen engine_core, and `create_llm` offers no seam to add
    a default header. Cost could therefore be attributed to WHO burned it
    and never to WHAT FOR - the one dimension a spend report most needs,
    and the one it could not give.

    So it is DERIVED, and named as derived: the returned tag is prefixed
    `auto:` whenever it was inferred rather than declared, so a reader can
    never mistake a guess for a statement. A caller that does send
    X-Kasukabe-Purpose keeps its own word, untouched.

    The shape of a request says plainly what it is. An agent's turn carries
    the world's tool schemas; a summarise pass carries none and a system
    prompt that says so; the learning centre asks about episodes. These are
    the three the trace has to tell apart to answer "where did the money
    go".
    """
    if declared and declared.strip().lower() != "unspecified":
        return declared

    tools = body.get("tools") or []
    system = body.get("system")
    if isinstance(system, list):
        system_text = " ".join(
            str(block.get("text", "")) for block in system if isinstance(block, dict)
        )
    else:
        system_text = str(system or "")
    lowered = system_text.lower()[:4000]

    if any(word in lowered for word in ("summarise", "summarize", "compact")):
        return "auto:compaction"
    if "episode" in lowered or "learning" in lowered:
        return "auto:learning"
    if tools:
        return "auto:agent_turn"
    return "auto:other"


def classify_purpose(purpose: str) -> CallClass:
    """Everything NOT recognized as background work is cognition - the
    common case (an agent's own in-tick thinking) has no obligation to
    tag itself; the default `purpose="unspecified"` (broker/service.py's
    get_header default) classifies as cognition, matching DESIGN.md
    section 4's framing of agent cognition as the primary, foreground
    workload.

    An `auto:` prefix is stripped before matching. It marks a tag as
    inferred rather than declared (see `derive_purpose` and
    broker/purpose.py) and says nothing about what the call is FOR, so
    leaving it on would make every inferred tag unclassifiable - which
    was the state of the world before broker/purpose.py: the shape tags
    this function is not entitled to act on, and the marker tags it is,
    were indistinguishable at the point of routing.
    """
    lowered = (purpose or "").strip().lower()
    if lowered.startswith(DERIVED_PREFIX):
        lowered = lowered[len(DERIVED_PREFIX):]
    if any(lowered.startswith(prefix) for prefix in BACKGROUND_PURPOSE_PREFIXES):
        return CallClass.BACKGROUND
    return CallClass.COGNITION


OVERFLOW_ONLY_MODELS: tuple[str, ...] = (QWEN_MODEL_ID,)

MAY_BE_ABSENT_MODELS: tuple[str, ...] = (QWEN_MODEL_ID, *OPENAI_MODELS)

MODEL_PREFERENCE_ORDER: dict[CallClass, tuple[str, ...]] = {
    CallClass.COGNITION: (KIMI_MODEL_ID, DEEPSEEK_MODEL_ID, QWEN_MODEL_ID),
    CallClass.BACKGROUND: (DEEPSEEK_MODEL_ID, QWEN_MODEL_ID),
}


logger = logging.getLogger("broker.lanes")


def lane_id(model: str, region: str) -> str:
    return f"{model}@{region}"


@dataclass
class CircuitBreaker:
    """Per-lane circuit breaker: CLOSED (normal) -> OPEN (after
    `threshold` consecutive throttles) -> HALF-OPEN (after `cooldown_s`,
    exactly one trial call allowed through) -> CLOSED (trial succeeded)
    or OPEN again (trial also throttled).

    `try_reserve()` / `release_unused_reservation()` exist because
    LanePool.select scans multiple lanes per call and only ends up
    dispatching to ONE of them: reserving the half-open trial slot the
    instant a half-open lane is considered, and releasing it again if
    that lane isn't the one ultimately chosen, is what stops two
    concurrently-selecting agents from both treating the same
    just-recovered lane as "the" trial and re-tripping it immediately
    (asyncio's single-threaded cooperative scheduling makes this
    reserve/release pair race-free with no lock needed: nothing else runs
    between them within one synchronous `select()` call).
    """

    threshold: int = 3
    cooldown_s: float = 30.0
    clock: Clock = field(default=time.monotonic)

    consecutive_failures: int = field(default=0, init=False)
    opened_at: "float | None" = field(default=None, init=False)
    trial_in_flight: bool = field(default=False, init=False)
    trip_count: int = field(default=0, init=False)

    def is_open(self) -> bool:
        """True while the lane is refusing all new calls (OPEN, not yet
        in its half-open cooldown-elapsed window)."""
        return self.opened_at is not None and (self.clock() - self.opened_at) < self.cooldown_s

    def try_reserve(self) -> bool:
        """Claim this lane for one dispatch attempt.

        True and side-effect-free for a CLOSED circuit (the common case).
        For an OPEN circuit: False while still cooling down; once cooled
        down (HALF-OPEN), True exactly once (claims `trial_in_flight`)
        and False for any other caller until that one trial resolves via
        `record_success`/`record_throttle`/`record_other_failure` - or is
        released unused via `release_unused_reservation`.
        """
        if self.opened_at is None:
            return True
        if (self.clock() - self.opened_at) < self.cooldown_s:
            return False
        if self.trial_in_flight:
            return False
        self.trial_in_flight = True
        return True

    def release_unused_reservation(self) -> None:
        """Undo a `try_reserve()` claim that LanePool.select decided not
        to use after all (this lane lost out to a better candidate found
        later in the scan). No-op for a CLOSED circuit - nothing was
        claimed there in the first place."""
        if self.opened_at is not None:
            self.trial_in_flight = False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self.trial_in_flight = False

    def record_throttle(self) -> None:
        """A dispatch on this lane came back as Bedrock throttling
        (broker/bedrock.py's UpstreamOverloadedError) - the ONLY failure
        kind this breaker counts, per the TASK spec's "repeated
        throttles"."""
        self.consecutive_failures += 1
        self.trial_in_flight = False
        if self.consecutive_failures >= self.threshold:
            if self.opened_at is None:
                self.trip_count += 1
            self.opened_at = self.clock()

    def record_other_failure(self) -> None:
        """A dispatch failed for a reason OTHER than Bedrock throttling
        (bad gateway, a translation bug, etc). Releases a claimed
        half-open trial without touching the failure count or the
        cooldown clock - an unrelated transient error must not extend a
        ban that a real throttle didn't cause."""
        self.trial_in_flight = False

    def status(self) -> dict[str, object]:
        """Admin-API-shaped snapshot - broker/app.py's `GET /admin/lanes`."""
        if self.opened_at is None:
            circuit_state = "closed"
        elif self.is_open():
            circuit_state = "open"
        else:
            circuit_state = "half_open"
        return {
            "circuit_state": circuit_state,
            "consecutive_failures": self.consecutive_failures,
            "trip_count": self.trip_count,
        }


@dataclass
class Lane:
    model: str
    region: str
    bucket: TokenBucket
    circuit: CircuitBreaker
    gateway: BedrockGateway

    @property
    def id(self) -> str:
        return lane_id(self.model, self.region)

    def status(self) -> dict[str, object]:
        return {
            "lane_id": self.id,
            "model": self.model,
            "region": self.region,
            "tokens_available": round(self.bucket.tokens, 2),
            "capacity": self.bucket.capacity,
            **self.circuit.status(),
        }


@dataclass
class LanePool:
    """Owns all six lanes and the routing policy across them."""

    lanes_by_model: dict[str, list[Lane]]
    cognition_first: "str | None" = None

    only_model: "str | None" = None

    def __post_init__(self) -> None:
        self.rotation_cursor: dict[str, int] = dict.fromkeys(self.lanes_by_model, 0)

    def preference_for(self, call_class: CallClass) -> tuple[str, ...]:
        order = MODEL_PREFERENCE_ORDER[call_class]
        if self.only_model:
            return (self.only_model,)
        first = self.cognition_first
        if call_class is not CallClass.COGNITION or not first:
            return order
        if not self.lanes_by_model.get(first):
            logger.warning(
                "cognition_first=%s has no lanes; falling back to %s", first, order[0]
            )
            return order
        return (first, *(m for m in order if m != first))

    def all_lanes(self) -> list[Lane]:
        return [lane for lanes in self.lanes_by_model.values() for lane in lanes]

    def lane_by_id(self, lid: str) -> "Lane | None":
        for lane in self.all_lanes():
            if lane.id == lid:
                return lane
        return None

    def rotated(self, model: str) -> list[Lane]:
        """This model's lanes starting from a rotating cursor, so
        repeated calls spread load evenly across the three regions
        instead of always favoring region[0] - the cursor advances by one
        on every call regardless of what select() ends up choosing, a
        simple round-robin over candidate ORDER, not over actual
        dispatches."""
        lanes = self.lanes_by_model.get(model) or []
        if not lanes:
            return []
        start = self.rotation_cursor[model] % len(lanes)
        self.rotation_cursor[model] = (start + 1) % len(lanes)
        return lanes[start:] + lanes[:start]

    def select(self, call_class: CallClass) -> Lane:
        """Pick ONE lane for a new call. Synchronous and non-blocking:
        never awaits, never consumes a rate-limit token itself (the
        caller does that against the returned lane's own bucket, via
        `await lane.bucket.acquire()`).

        Three passes, in preference order (TASK spec routing policy):
          1. Any non-broken lane with a token available RIGHT NOW - the
             common case, zero wait, and where "cognition prefers Kimi"
             is enforced (Kimi's lanes are scanned before DeepSeek's for
             CallClass.COGNITION).
          2. No lane had an instantly-available token: fall back to the
             first non-broken lane in preference order and let the caller
             wait on ITS fair FIFO queue (TokenBucket.acquire already
             implements that wait correctly). This IS the "overflow to
             DeepSeek when all Kimi lanes are saturated" moment for
             cognition - it degrades to "wait for the best remaining
             option" rather than erroring.
          3. Every lane for this call class is circuit-open (repeated
             Bedrock throttling on all of them): raises
             UpstreamOverloadedError instead of dispatching into a lane
             that has already, repeatedly, proven it can't serve right
             now - "never spin-retry" applies here too, not just to a
             single call's own retry behavior.
        """
        preference = self.preference_for(call_class)

        for model in preference:
            for lane in self.rotated(model):
                if not lane.circuit.try_reserve():
                    continue
                if lane.bucket.peek_available():
                    return lane
                lane.circuit.release_unused_reservation()

        for model in preference:
            for lane in self.rotated(model):
                if lane.circuit.try_reserve():
                    return lane

        raise UpstreamOverloadedError(
            f"every lane for {call_class.value} calls is circuit-open "
            f"(models tried: {', '.join(preference)}) - Bedrock has been "
            "throttling repeatedly on all of them; refusing to dispatch "
            "instead of spin-retrying a lane that just proved it can't "
            "serve right now"
        )

    def status(self) -> list[dict[str, object]]:
        """Admin-API-shaped snapshot of every lane - broker/app.py's
        `GET /admin/lanes`."""
        return [lane.status() for model in self.lanes_by_model for lane in self.lanes_by_model[model]]


def build_lane_pool(
    settings: BrokerSettings,
    gateway_factory: Callable[[str, str], BedrockGateway],
    clock: Clock = time.monotonic,
    sleep: "Sleep | None" = None,
) -> LanePool:
    """Builds the canonical six lanes: MODEL_ALLOWLIST x
    BEDROCK_LANE_REGIONS (broker/config.py). `gateway_factory(model,
    region) -> BedrockGateway` is how broker/app.py's build_service picks
    mock vs real per lane WITHOUT this module needing to know about boto3
    or MockBedrockGateway - keeps the boto3-import boundary exactly where
    app/CONVENTIONS.md puts it (broker/bedrock.py only). Called once per
    lane even when two lanes share a region (kimi@ap-south-1 and
    deepseek@ap-south-1 each get their OWN gateway instance) - TASK spec:
    "each lane owns its own ... regional boto3 client", literally.
    """
    sleep = sleep or asyncio.sleep
    profiles = settings.bedrock_inference_profiles
    skipped: list[str] = []
    lanes_by_model: dict[str, list[Lane]] = {}
    for model in MODEL_ALLOWLIST:
        lanes: list[Lane] = []
        if model in OPENAI_MODELS:
            if not settings.openai_api_key:
                lanes_by_model[model] = []
                continue
            lanes.append(Lane(
                model=model, region=OPENAI_REGION,
                bucket=TokenBucket(settings.openai_rpm, clock=clock, sleep=sleep),
                circuit=CircuitBreaker(
                    threshold=settings.circuit_breaker_threshold,
                    cooldown_s=settings.circuit_breaker_cooldown_s, clock=clock),
                gateway=gateway_factory(model, OPENAI_REGION)))
            lanes_by_model[model] = lanes
            continue
        for region in BEDROCK_LANE_REGIONS:
            if profiles and lane_id(model, region) not in profiles:
                skipped.append(lane_id(model, region))
                continue
            bucket = TokenBucket(settings.rpm_per_lane, clock=clock, sleep=sleep)
            circuit = CircuitBreaker(
                threshold=settings.circuit_breaker_threshold,
                cooldown_s=settings.circuit_breaker_cooldown_s,
                clock=clock,
            )
            gateway = gateway_factory(model, region)
            lanes.append(Lane(model=model, region=region, bucket=bucket, circuit=circuit, gateway=gateway))
        lanes_by_model[model] = lanes
    if skipped:
        logger.warning(
            "%d of %d lanes have no inference profile and were NOT built: %s. "
            "Aggregate throughput is %d RPM, not %d.",
            len(skipped),
            len(MODEL_ALLOWLIST) * len(BEDROCK_LANE_REGIONS),
            ", ".join(skipped),
            sum(len(v) for v in lanes_by_model.values()) * settings.rpm_per_lane,
            len(MODEL_ALLOWLIST) * len(BEDROCK_LANE_REGIONS) * settings.rpm_per_lane,
        )
    if absent := [m for m in OVERFLOW_ONLY_MODELS if m in lanes_by_model and not lanes_by_model[m]]:
        served = sum(len(v) for v in lanes_by_model.values()) * settings.rpm_per_lane
        logger.warning(
            "overflow model(s) %s have no inference profile on this deployment and "
            "will NOT be routed to. Capacity is %d RPM without them - enough for a "
            "20-resident run, not for 50 (measured peak ~282 RPM). Add a profile to "
            "BEDROCK_INFERENCE_PROFILES to restore the overflow tier.",
            ", ".join(absent), served,
        )
    if unusable := [m for m, v in lanes_by_model.items() if not v and m not in MAY_BE_ABSENT_MODELS]:
        raise ValueError(
            "no inference profile for any region of: "
            + ", ".join(unusable)
            + " - that model cannot be served at all. Add a profile or remove it "
            "from MODEL_ALLOWLIST."
        )
    if settings.only_model and not lanes_by_model.get(settings.only_model):
        raise ValueError(
            f"ONLY_MODEL={settings.only_model} has no lanes, so this broker can "
            "serve nothing at all. For an OpenAI model the usual cause is a "
            "missing or empty OPENAI_API_KEY; for a Bedrock one, no inference "
            "profile in any region. Refusing to start rather than falling back "
            "to a model this run said not to use."
        )
    return LanePool(
        lanes_by_model=lanes_by_model,
        cognition_first=settings.cognition_first_model or None,
        only_model=settings.only_model or None,
    )
