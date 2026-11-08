"""Broker settings - env-driven, fully validated, aggregated errors.

Mirrors the shape of engine/src/config.py's Settings (CONVENTIONS.md calls
that module out by name as the pattern to match): every field is declared
once with its env var and its check, and load_validated() raises ONE
ConfigError carrying every problem at once instead of failing on the
first bad value. Broker's config surface is much smaller than the
engine's (no citadel, no sandbox, no channels) so this is a plain
dataclass instead of engine's declarative `setting()` field-metadata
system - that machinery earns its keep at engine's scale, not here.

MOCK_MODE is the field every other module in this package reads to decide
which side of a Protocol to construct (real boto3 client vs canned
responses, real world-ledger HTTP client vs in-memory fake). It defaults
to True on purpose: a broker that boots without AWS credentials, a real
world server, or a real Supabase project should still boot and serve
`/health`, because `make test` and local dev both need that. Only the
deploy path (infra/) sets MOCK_MODE=false.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """Aggregated configuration failure - carries every error at once.

    Same rationale as engine/src/config.py's ConfigError: a bad SPEND_CAP_USD
    and a bad TRACE_BATCH_SIZE should both be visible in one boot attempt,
    not one restart per mistake.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid broker configuration ({len(self.errors)} error(s)):\n  - {joined}")


def parse_bool(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    raise ValueError(f"{name}={value!r} - must be 'true' or 'false' (case-insensitive)")


def parse_int(name: str, value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise ValueError(f"{name}={value!r} - must be an integer") from None


def parse_float(name: str, value: str) -> float:
    try:
        return float(value.strip())
    except ValueError:
        raise ValueError(f"{name}={value!r} - must be a number") from None


def read_env_file(env_path: Path | None = None) -> dict[str, str]:
    """Parse a .env file into a dict. Missing file returns empty."""
    if env_path is None:
        env_path = Path(".env")
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    return env_vars


# TASK spec (broker-lanes track, 2026-07-31): both models are live in the
# six-lane pool - Kimi preferred for agent cognition, DeepSeek for
# background work, DeepSeek also the overflow target when every Kimi lane
# is saturated (see broker/lanes.py). Named constants instead of bare
# strings so lanes.py's routing tables and this allowlist can never drift
# apart. This tuple is deliberately NOT env-configurable: "any other model
# id -> 403 hard fail" is a law, not a deployment knob.
KIMI_MODEL_ID = "moonshotai.kimi-k2.5"
DEEPSEEK_MODEL_ID = "deepseek.v3.2"
# A THIRD lane in ap-south-1, and the only kind of capacity this
# deployment can still buy.
#
# The quota is per (model, region) - `RPM_PER_LANE`'s docstring below, and
# the quota code in `infra/kimi-quota-request.md` names one MODEL - so a
# deployment holding profiles in one region only cannot add RPM by adding
# regions. It can add models. Kimi and DeepSeek give ap-south-1 two
# independent 100 RPM buckets; this is the third.
#
# The string is the RESEARCH RECORD's name for the model, not what boto3
# is handed: `bedrock.py:185` replaces `modelId` with the lane's inference
# profile ARN when one is configured, precisely so a profile swap cannot
# make the record disagree with what was actually served.
QWEN_MODEL_ID = "qwen.qwen3-coder-480b"

# The OpenAI lane, and the one model in this file that is not served through
# Bedrock at all - `broker/openai_gateway.py` speaks to api.openai.com.
#
# It is here rather than in an env var for the reason the allowlist below
# gives: which models this project may spend money on is a decision in the
# code, reviewable in a diff, not a string somebody can put in a .env on a
# shared box.
#
# Why it was added: on this project's real traffic to date - 458.5M input
# and 6.0M output tokens across 32,739 calls - Kimi K2.5 costs $175.94 and
# `gpt-6-luna` $48.87. Same tokens, 3.6x cheaper, because this town's spend
# is 98.7% input and Luna prices input at $0.10/M against Kimi's $0.36.
OPENAI_LUNA6_MODEL_ID = "gpt-6-luna"
OPENAI_LUNA56_MODEL_ID = "gpt-5.6-luna"

#: Which allowlisted models are served by OpenAI rather than Bedrock. Lanes
#: for these have no region and no inference profile, so `build_lane_pool`
#: builds exactly one each instead of one per region.
OPENAI_MODELS: tuple[str, ...] = (OPENAI_LUNA6_MODEL_ID, OPENAI_LUNA56_MODEL_ID)

#: The pseudo-region an OpenAI lane carries, so `lane_id` and every log line
#: that prints "model@region" keeps working without a special case.
OPENAI_REGION = "openai"

#: The allowlisted models that are actually served by Bedrock. Anything that
#: reasons about regions, inference profiles or IAM grants means THIS list,
#: not the whole allowlist - an OpenAI model has none of those three.
BEDROCK_MODELS: tuple[str, ...] = (KIMI_MODEL_ID, DEEPSEEK_MODEL_ID, QWEN_MODEL_ID)

MODEL_ALLOWLIST: tuple[str, ...] = (
    KIMI_MODEL_ID,
    DEEPSEEK_MODEL_ID,
    QWEN_MODEL_ID,
    OPENAI_LUNA6_MODEL_ID,
    OPENAI_LUNA56_MODEL_ID,
)

# DESIGN.md section 11: flex-tier prices, USD per 1,000,000 tokens.
# pricing.py converts these to exact integer nano-dollars/token; kept here
# alongside the allowlist because both are "the law", not tunables.
MODEL_PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    KIMI_MODEL_ID: (0.36, 1.80),
    DEEPSEEK_MODEL_ID: (0.37, 1.11),
    # Dearer than both on input and cheaper than Kimi on output, so a turn
    # that overflows here is not a turn that costs the same. Run 29 spent
    # 96% of its money on INPUT tokens (53.4M in against 467k out), so on
    # this town's actual shape Qwen is the expensive lane - roughly 1.7x
    # Kimi per input token - and the report's per-model cost breakdown is
    # what will say how much of the bill it took.
    QWEN_MODEL_ID: (0.60, 1.44),
    # OpenAI list price, 29 September 2026, read from the published page
    # rather than assumed. Luna is the cheap tier of each series; the Sol
    # and Astra tiers are 20x and 100x its input price and are deliberately
    # not offered here.
    OPENAI_LUNA6_MODEL_ID: (0.10, 0.50),
    OPENAI_LUNA56_MODEL_ID: (0.20, 1.20),
}

# TASK spec MEASURED FACTS (broker-lanes track): "Kimi K2.5 and DeepSeek
# V3.2: 100 RPM per model PER REGION, enforced... Both verified invokable
# in ap-south-1, us-east-1, us-west-2. Per-region quotas = independent
# lanes." Hard constant for the same reason MODEL_ALLOWLIST is one: this
# is the physical set of regions the quota was actually verified in, not
# a deployment preference. broker/lanes.py builds exactly
# len(MODEL_ALLOWLIST) * len(BEDROCK_LANE_REGIONS) == 6 lanes from the
# cross product of these two tuples.
BEDROCK_LANE_REGIONS: tuple[str, ...] = ("ap-south-1", "us-east-1", "us-west-2")

# TASK spec: "100 RPM per model PER REGION" - i.e. per LANE, not per model
# aggregated across all three of its regions. Renamed from the old
# single-bucket RPM_PER_MODEL now that broker/lanes.py gives each of the
# six (model, region) pairs its own independent token bucket at this rate
# (6 lanes * 100 RPM = 600 RPM aggregate, matching DESIGN.md section 9's
# revised throughput math). Not env-configurable, same reasoning as above.
RPM_PER_LANE = 100

# TASK spec: "process-wide hard USD cap (default 400.00)... warn at 80%".
# These ARE operator knobs (unlike the law constants above) - default
# values live here so broker/spend.py and BrokerSettings agree on them
# without duplicating the numbers.
DEFAULT_SPEND_CAP_USD = 400.0
DEFAULT_SPEND_WARN_FRACTION = 0.8

# TASK spec: "Circuit-break a lane on repeated throttles, never
# spin-retry." Also operator knobs, not laws - a slower/faster cooldown
# is a deployment tuning choice, not a Bedrock physical constraint.
# Wallet, in cents, that MOCK_MODE's in-memory ledger provisions for an
# agent it has never seen. 0 - the default - disables provisioning, so an
# unknown agent is an error exactly as before.
#
# OFF by default deliberately. "An agent the ledger never heard of cannot
# spend" is a real invariant, asserted at both levels
# (broker/tests/test_ledger.py and
# test_messages_rejects_unknown_agent_before_dispatch), and a default that
# quietly provisioned wallets would delete it everywhere to fix one local
# workflow. Local bring-up turns it on explicitly instead - see .env and
# docker-compose.yml's broker service.
#
# 200 is the value to set: exactly world/genesis.json's usd_wallet_cents,
# so a mock-mode agent starts with the same $2.00 a real one does.
DEFAULT_MOCK_LEDGER_BALANCE_CENTS = 0

# The per-request tiers Bedrock's `serviceTier` accepts. `reserved` is set
# at the account level rather than per call, so it is deliberately absent.
BEDROCK_SERVICE_TIERS: tuple[str, ...] = ("priority", "default", "flex")

DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3
DEFAULT_CIRCUIT_BREAKER_COOLDOWN_S = 30.0


# AWS credentials are NOT BrokerSettings fields, on purpose. broker/
# bedrock.py never passes keys to boto3.client() - it relies on the
# standard boto3 credential chain, which reads the PROCESS ENVIRONMENT.
# So honouring a .env means exporting these into os.environ before the
# first client is built, which is what export_aws_env() does. Keeping
# them out of the settings dataclass also keeps them out of every repr(),
# log line and error message that prints settings.
AWS_ENV_VARS: tuple[str, ...] = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
)


def export_aws_env(env_path: Path | None = None) -> list[str]:
    """Copy AWS_* entries from the .env file into os.environ for boto3.

    Returns the NAMES it set, never the values. This function is the one
    place in the package that handles secret material, so it is built to
    be un-loggable: a caller that prints the return value prints
    "AWS_SECRET_ACCESS_KEY", not the key.

    A variable already set in the real environment is left alone, matching
    every other setting's precedence (os.environ wins over the file), so an
    operator can override one credential for one process without editing
    the file. Blank entries are skipped rather than exported as empty
    strings - an empty AWS_ACCESS_KEY_ID in the environment does not mean
    "unset", it means "try to authenticate with nothing" and produces a
    confusing signature error instead of falling through to ~/.aws.
    """
    exported: list[str] = []
    env_vars = read_env_file(env_path)
    for name in AWS_ENV_VARS:
        value = env_vars.get(name, "").strip()
        if not value or os.environ.get(name):
            continue
        os.environ[name] = value
        exported.append(name)
    return exported


def lane_key(model: str, region: str) -> str:
    """The "<model>@<region>" key identifying one lane.

    Deliberately duplicated from broker/lanes.py's lane_id() rather than
    imported: lanes.py imports THIS module, so importing back would be a
    cycle. Same reasoning as world/run_genesis.py carrying its own copy of
    the supervisor watchdog (ROADMAP.md Law 1's boundary rule 3) - and,
    like that copy, it is held to the original by a parity test, see
    broker/tests/test_config.py::test_lane_key_matches_lanes_lane_id.
    """
    return f"{model}@{region}"


def expected_lane_keys() -> tuple[str, ...]:
    """Every BEDROCK lane the pool will build: BEDROCK_MODELS x regions.

    BEDROCK_MODELS rather than MODEL_ALLOWLIST, because this tuple's only
    job is to say which lanes need an inference profile. An OpenAI lane has
    no region and no profile - listing it here would demand a Bedrock ARN
    for `gpt-6-luna` and refuse to boot without one.
    """
    return tuple(lane_key(m, r) for m in BEDROCK_MODELS for r in BEDROCK_LANE_REGIONS)


def parse_inference_profiles(name: str, value: str) -> dict[str, str]:
    """Parse "<lane>=<profile>,<lane>=<profile>" into a mapping.

    Newlines are accepted as separators too, so the value stays readable
    in a compose file or a .env. Profile ARNs contain ':' and '/' but
    never ',' or '=', so partitioning on the first '=' is unambiguous.
    """
    profiles: dict[str, str] = {}
    entries: list[str] = []
    for line in value.splitlines():
        entries.extend(line.split(","))
    for chunk in entries:
        entry = chunk.strip()
        if not entry:
            continue
        key, sep, profile = entry.partition("=")
        if not sep:
            raise ValueError(
                f"{name}: entry {entry!r} is not of the form '<model>@<region>=<profile>'"
            )
        key, profile = key.strip(), profile.strip()
        if not key or not profile:
            raise ValueError(f"{name}: entry {entry!r} has an empty lane key or profile")
        if key in profiles:
            raise ValueError(f"{name}: lane {key!r} is configured twice")
        profiles[key] = profile
    return profiles


@dataclass
class BrokerSettings:
    # ── Mode ────────────────────────────────────────────────────────
    # True: MockBedrockGateway + InMemoryLedgerClient + InMemoryTraceSink.
    # False: real boto3 bedrock-runtime + HTTP calls to world + Supabase.
    # See module docstring for why the default is True.
    # REAL BEDROCK IS THE ONLY WAY THIS BOOTS.
    #
    # Defaults False and is no longer readable from MOCK_MODE: a deployment
    # cannot fall into canned "mock response" text by forgetting an env
    # var, which is exactly how a run can look healthy - engines up, ticks
    # flowing, wallets debited - while nothing is thinking.
    #
    # Still a field, not deleted, because the test suite constructs it
    # explicitly (BrokerSettings(mock_mode=True)) to exercise the gateway,
    # ledger and lane code without AWS. Tests may ask for it; the
    # environment may not.
    mock_mode: bool = False

    # ── World ledger (DESIGN.md section 3: wallet checks + debits) ───
    # world's HTTP API base URL (app/CONVENTIONS.md: default dev port 8100).
    # Only read when mock_mode is False - broker's tests and default local
    # dev never require a running world process.
    world_base_url: str = "http://localhost:8100"
    world_http_timeout_s: float = 10.0

    # ── Auth (DESIGN.md section 9: agent id from auth header) ────────
    # HMAC secret used to verify the "<agent_id>.<hex-hmac>" bearer token
    # format (see broker/auth.py). Empty means "trust the bearer token's
    # agent_id field unsigned" - the correct default for local dev/tests
    # and for the current single-host deployment (DESIGN.md section 9:
    # everything lives inside one m5.metal box, there is no network
    # boundary between herald and broker to defend against yet). Set this
    # once agents run anywhere broker doesn't already trust the network.
    auth_shared_secret: str = ""

    # ── Trace + event batching (DESIGN.md section 9: "batches trace
    # writes async, never blocks inference") ──────────────────────────
    trace_batch_size: int = 20
    trace_flush_interval_s: float = 2.0
    # SQLite replaced Supabase as the trace store (DESIGN.md section 9).
    # Real mode writes llm_calls/events straight into this database file;
    # Litestream streams its WAL to S3 for durability.
    # ── INFRA-06: ask for reasoning, or do not ────────────────────────
    # Build Spec threshold 9.5 wants reasoning captured on 100% of turns.
    # Everything needed to capture it has been in place and dormant:
    # `translate.py` turns Anthropic's `thinking` into Bedrock's
    # `reasoning_config` and carries `reasoningContent` back on both the
    # streaming and non-streaming paths, with tests either side. What was
    # missing is that nobody ever ASKED - `MessagesRequest.thinking` only
    # arrives if herald sets it, and herald sets it only for
    # `super_thinking` or a latched session, tiers this sim never resolves.
    #
    # So "the model did not reason" and "we never requested reasoning" were
    # indistinguishable in all 23 runs. Run 23's logs show Kimi emitting
    # `</think>` INSIDE text - the model reasons in band, and the record
    # cannot separate reasoning from speech.
    #
    # OFF by default, and that is deliberate. The review's fix is "decide
    # F1", and F1 is a research decision with a live-call price attached:
    # whether Kimi on Bedrock honours `reasoning_config` at all is not
    # known and can only be settled by spending money against it. Turning
    # this on is how an operator takes that decision. Leaving it off keeps
    # every existing run comparable.
    #
    # It couples routing policy to a research setting, which the review
    # says plainly and is the cost of the broker-only option. The
    # alternative was a seam wrapper in `engine/src/sim/model_profile.py`;
    # this one is reversible with an environment variable, which is what
    # an undecided question should be.
    request_reasoning: bool = False
    # Only for turns, never for housekeeping. A summariser that reasons is
    # paying thinking tokens to compress text, and 9.5 asks about the
    # agent's turn.
    request_reasoning_purposes: tuple[str, ...] = ("auto:agent_turn",)
    request_reasoning_budget_tokens: int = 2048

    trace_sqlite_path: str = "/data/kasukabe.db"
    supabase_url: str = ""
    supabase_service_key: str = ""

    # ── Rate limiting (TASK spec, broker-lanes track) ─────────────────
    # Per-LANE now, not per-model - see RPM_PER_LANE's docstring above.
    rpm_per_lane: int = RPM_PER_LANE
    #: Requests per minute the OpenAI lane may use.
    #:
    #: Separate from `rpm_per_lane` because that number is Bedrock's quota -
    #: 100 per (model, region), enforced by AWS - and OpenAI's is per account
    #: and typically several hundred to several thousand. A 50-resident run
    #: peaks at ~282 RPM, so leaving the OpenAI lane on Bedrock's 100 would
    #: throttle the one lane the run is meant to be carried by. Set it to
    #: what the account's own rate-limit header says; 500 is a safe default
    #: for a paid tier and still well under most limits.
    openai_rpm: int = 500
    #: The OpenAI key, when an OpenAI model is in play. Absent is normal and
    #: not an error: a deployment with no key simply builds no OpenAI lane,
    #: exactly as one with no Qwen profile builds no Qwen lane.
    openai_api_key: str = ""
    #: Which model cognition should prefer, when not the default. Empty keeps
    #: MODEL_PREFERENCE_ORDER as written. See `LanePool.cognition_first`.
    cognition_first_model: str = ""
    #: The ONLY model this broker may serve, for every call class. Empty is
    #: normal and means the usual preference order with its overflow.
    #:
    #: Preferring is not the same as restricting, and the difference is a
    #: bill. `cognition_first_model` moves a model to the front and leaves the
    #: rest of the order behind it, so a peak still overflows to Bedrock - and
    #: it only touches COGNITION, while every BACKGROUND call (learning
    #: centre, dream probes, importance scoring, summarizer) goes to Bedrock
    #: regardless. On trial-day2-20 that was $1.43 of $20.16 on deepseek while
    #: Kimi was the "chosen" model.
    #:
    #: So a run told to use one model and no other sets THIS. A model with no
    #: lanes here is a boot-time refusal rather than a quiet fallback, because
    #: quietly falling back is the entire thing it exists to prevent.
    only_model: str = ""

    # ── Spend governor (TASK spec, broker-lanes track) ────────────────
    # Process-wide hard USD cap computed from REAL response usage, never
    # a pre-dispatch estimate - see broker/spend.py's module docstring.
    spend_cap_usd: float = DEFAULT_SPEND_CAP_USD
    spend_warn_fraction: float = DEFAULT_SPEND_WARN_FRACTION

    # ── Circuit breaker (TASK spec, broker-lanes track) ────────────────
    # "Circuit-break a lane on repeated throttles, never spin-retry" -
    # `threshold` consecutive Bedrock throttling responses trips a lane
    # open for `cooldown_s` - see broker/lanes.py's CircuitBreaker.
    # ── Mock ledger (MOCK_MODE=true only) ────────────────────────────
    # Real engines call the broker with real genesis agent ids that the
    # in-memory fake has never been seeded with, so every inference 404s
    # and the engine turns that into a 500 from its own /tick. Giving the
    # fake a starting wallet lets a credential-free local run actually
    # exercise the loop. Ignored entirely when mock_mode is False, where
    # HttpLedgerClient talks to world's real ledger.
    mock_ledger_balance_cents: int = DEFAULT_MOCK_LEDGER_BALANCE_CENTS

    circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    circuit_breaker_cooldown_s: float = DEFAULT_CIRCUIT_BREAKER_COOLDOWN_S

    # ── Bedrock inference profiles (deployment data, NOT a law) ───────
    # MODEL_ALLOWLIST above is the law: which models this town may think
    # with. This is the opposite kind of thing - the account-and-region
    # specific handle a lane must actually put in `converse`'s modelId,
    # for an account whose IAM grants bedrock:InvokeModel through an
    # inference profile rather than on the bare foundation model. Same
    # two models either way; only the string reaching boto3 changes, and
    # only at the boto3 boundary (broker/bedrock.py). The allowlist,
    # pricing, lane ids and every trace row stay keyed on the logical
    # model id, so nothing downstream can drift.
    #
    # Keyed by lane_key() because a profile is per model PER REGION: an
    # application inference profile ARN names its own account and region,
    # so kimi@ap-south-1 and kimi@us-east-1 are two different ARNs.
    #
    # Empty (the default) means "send the bare model id" - correct for an
    # account that may invoke foundation models directly. Discover the
    # ARNs with `python -m broker.discover_profiles`.
    bedrock_inference_profiles: dict[str, str] = field(default_factory=dict)
    # Which Bedrock service tier every request asks for.
    #
    # `flex`, because `MODEL_PRICES_USD_PER_MILLION` above is the flex-tier
    # table and DESIGN.md section 11 chose it - and until 2026-09-14 nothing
    # ever SET this field, so AWS served every call at Standard (double the
    # price in ap-south-1) while the trace priced it at Flex. Three runs
    # recorded $25.04 between them and cost about $45 at list.
    #
    # `default` means Standard and is sent as no field at all, which is
    # exactly what a model that has never heard of the parameter needs.
    # `priority` is faster and dearer. `reserved` is an account-level
    # commitment rather than a per-request choice, so it is not offered here.
    bedrock_service_tier: str = "flex"

    # A cheap profile used ONLY by broker/preflight.py to prove the
    # credentials, the IAM policy and profile invocation all work before a
    # real run starts spending on the models the town thinks with.
    #
    # Deliberately NOT in MODEL_ALLOWLIST: nothing routes agent cognition
    # here, no lane is built for it, and it has no entry in
    # MODEL_PRICES_USD_PER_MILLION - so it cannot become a thinking model
    # by accident. It is a canary, not a lane.
    bedrock_control_profile_arn: str = ""

    def validate(self) -> list[str]:
        """Full semantic validation. Returns ALL errors (empty = valid)."""
        errors: list[str] = []

        if self.bedrock_service_tier not in BEDROCK_SERVICE_TIERS:
            errors.append(
                f"BEDROCK_SERVICE_TIER={self.bedrock_service_tier!r} - must be one of "
                f"{', '.join(BEDROCK_SERVICE_TIERS)}. Bedrock refuses anything else, and "
                "it refuses it forty minutes into a run rather than at boot."
            )

        if self.trace_batch_size <= 0:
            errors.append(f"TRACE_BATCH_SIZE={self.trace_batch_size} - must be greater than 0")
        if self.trace_flush_interval_s <= 0:
            errors.append(
                f"TRACE_FLUSH_INTERVAL_S={self.trace_flush_interval_s} - must be greater than 0"
            )
        if self.rpm_per_lane <= 0:
            errors.append(f"RPM_PER_LANE={self.rpm_per_lane} - must be greater than 0")
        if self.spend_cap_usd <= 0:
            errors.append(f"SPEND_CAP_USD={self.spend_cap_usd} - must be greater than 0")
        if not (0.0 < self.spend_warn_fraction <= 1.0):
            errors.append(
                f"SPEND_WARN_FRACTION={self.spend_warn_fraction} - must be greater than 0 and "
                "at most 1"
            )
        if self.circuit_breaker_threshold <= 0:
            errors.append(
                f"CIRCUIT_BREAKER_THRESHOLD={self.circuit_breaker_threshold} - must be greater "
                "than 0"
            )
        if self.circuit_breaker_cooldown_s <= 0:
            errors.append(
                f"CIRCUIT_BREAKER_COOLDOWN_S={self.circuit_breaker_cooldown_s} - must be "
                "greater than 0"
            )
        if self.mock_ledger_balance_cents < 0:
            errors.append(
                f"MOCK_LEDGER_BALANCE_CENTS={self.mock_ledger_balance_cents} - must be 0 or more "
                "(0 disables provisioning)"
            )
        if self.world_http_timeout_s <= 0:
            errors.append(
                f"WORLD_HTTP_TIMEOUT_S={self.world_http_timeout_s} - must be greater than 0"
            )

        if not self.mock_mode:
            # Every field below is load-bearing for a real deployment;
            # a mock-mode boot never needs any of it, so the check is
            # gated the same way the code paths that use them are gated.
            if not self.world_base_url:
                errors.append("WORLD_BASE_URL is required when MOCK_MODE=false")
            # Supabase is gone; the trace store is SQLite. A real run that
            # cannot write traces is not acceptable (the traces ARE the
            # research output), so an empty path is still a hard error.
            if not self.trace_sqlite_path:
                errors.append("TRACE_SQLITE_PATH is required when MOCK_MODE=false")

            # A PARTIAL mapping is legal and is the common case: an
            # account's application inference profiles are region-scoped,
            # and a least-privilege IAM user may hold them in one region
            # only (measured: this deployment has both models in
            # ap-south-1 and neither in us-east-1/us-west-2).
            #
            # The safety property is preserved a better way - broker/
            # lanes.py's build_lane_pool BUILDS ONLY the lanes that have a
            # profile, so a lane can never be left sending a bare model id
            # and failing with AccessDenied mid-run. Fewer lanes, all of
            # them real, instead of six lanes of which four are traps.
            #
            # A lane key that does not exist is still an error: that is a
            # typo, not a deployment shape.
            if self.bedrock_inference_profiles:
                expected = set(expected_lane_keys())
                configured = set(self.bedrock_inference_profiles)
                if unknown := sorted(configured - expected):
                    errors.append(
                        "BEDROCK_INFERENCE_PROFILES names lane(s) that do not exist: "
                        + ", ".join(unknown)
                        + " - the six real lanes are "
                        + ", ".join(expected_lane_keys())
                    )

        return errors

    @classmethod
    def build_from_env(cls, env_vars: dict[str, str]) -> tuple["BrokerSettings", list[str]]:
        errors: list[str] = []
        kwargs: dict[str, object] = {}

        def get(name: str) -> str | None:
            return os.environ.get(name, env_vars.get(name))

        # MOCK_MODE is deliberately NOT read. See BrokerSettings.mock_mode.
        # Rejected loudly rather than ignored: an operator who sets it is
        # expecting canned responses, and silently giving them real Bedrock
        # spend instead is the worse of the two surprises.
        if get("MOCK_MODE") is not None:
            errors.append(
                "MOCK_MODE is no longer supported - this broker only talks to real "
                "Bedrock. Remove it from the environment/.env."
            )
        if (raw := get("BEDROCK_SERVICE_TIER")) is not None:
            kwargs["bedrock_service_tier"] = raw.strip().lower()
        if (raw := get("WORLD_BASE_URL")) is not None:
            kwargs["world_base_url"] = raw.strip()
        if (raw := get("WORLD_HTTP_TIMEOUT_S")) is not None:
            try:
                kwargs["world_http_timeout_s"] = parse_float("WORLD_HTTP_TIMEOUT_S", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("MOCK_LEDGER_BALANCE_CENTS")) is not None:
            try:
                kwargs["mock_ledger_balance_cents"] = parse_int("MOCK_LEDGER_BALANCE_CENTS", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("KASUKABE_REQUEST_REASONING")) is not None:
            try:
                kwargs["request_reasoning"] = parse_bool(
                    "KASUKABE_REQUEST_REASONING", raw
                )
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("KASUKABE_REASONING_BUDGET_TOKENS")) is not None:
            try:
                kwargs["request_reasoning_budget_tokens"] = parse_int(
                    "KASUKABE_REASONING_BUDGET_TOKENS", raw
                )
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("AUTH_SHARED_SECRET")) is not None:
            kwargs["auth_shared_secret"] = raw
        if (raw := get("BEDROCK_CONTROL_PROFILE_ARN")) is not None:
            kwargs["bedrock_control_profile_arn"] = raw.strip()
        if (raw := get("BEDROCK_INFERENCE_PROFILES")) is not None:
            try:
                kwargs["bedrock_inference_profiles"] = parse_inference_profiles(
                    "BEDROCK_INFERENCE_PROFILES", raw
                )
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("TRACE_BATCH_SIZE")) is not None:
            try:
                kwargs["trace_batch_size"] = parse_int("TRACE_BATCH_SIZE", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("TRACE_FLUSH_INTERVAL_S")) is not None:
            try:
                kwargs["trace_flush_interval_s"] = parse_float("TRACE_FLUSH_INTERVAL_S", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("TRACE_SQLITE_PATH")) is not None:
            kwargs["trace_sqlite_path"] = raw.strip()
        if (raw := get("SUPABASE_URL")) is not None:
            kwargs["supabase_url"] = raw.strip()
        if (raw := get("SUPABASE_SERVICE_KEY")) is not None:
            kwargs["supabase_service_key"] = raw
        if (raw := get("OPENAI_RPM")) is not None:
            try:
                kwargs["openai_rpm"] = parse_int("OPENAI_RPM", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("OPENAI_API_KEY")) is not None:
            kwargs["openai_api_key"] = raw.strip()
        if (raw := get("ONLY_MODEL")) is not None:
            want = raw.strip()
            if want and want not in MODEL_ALLOWLIST:
                errors.append(
                    f"ONLY_MODEL={want!r} is not in MODEL_ALLOWLIST "
                    f"{MODEL_ALLOWLIST} - a model this broker may not spend on "
                    "cannot be the only one it serves"
                )
            else:
                kwargs["only_model"] = want
        if (raw := get("COGNITION_FIRST_MODEL")) is not None:
            want = raw.strip()
            if want and want not in MODEL_ALLOWLIST:
                errors.append(
                    f"COGNITION_FIRST_MODEL={want!r} is not in MODEL_ALLOWLIST "
                    f"{MODEL_ALLOWLIST} - a model this broker may not spend on "
                    "cannot be the one it prefers"
                )
            else:
                kwargs["cognition_first_model"] = want
        if (raw := get("RPM_PER_LANE")) is not None:
            try:
                kwargs["rpm_per_lane"] = parse_int("RPM_PER_LANE", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("SPEND_CAP_USD")) is not None:
            try:
                kwargs["spend_cap_usd"] = parse_float("SPEND_CAP_USD", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("SPEND_WARN_FRACTION")) is not None:
            try:
                kwargs["spend_warn_fraction"] = parse_float("SPEND_WARN_FRACTION", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("CIRCUIT_BREAKER_THRESHOLD")) is not None:
            try:
                kwargs["circuit_breaker_threshold"] = parse_int("CIRCUIT_BREAKER_THRESHOLD", raw)
            except ValueError as e:
                errors.append(str(e))
        if (raw := get("CIRCUIT_BREAKER_COOLDOWN_S")) is not None:
            try:
                kwargs["circuit_breaker_cooldown_s"] = parse_float(
                    "CIRCUIT_BREAKER_COOLDOWN_S", raw
                )
            except ValueError as e:
                errors.append(str(e))

        return cls(**kwargs), errors  # type: ignore[arg-type]

    @classmethod
    def load(cls, env_path: Path | None = None) -> "BrokerSettings":
        """Parse env/.env only, no semantic validation. Raises ConfigError
        aggregating every parse error."""
        settings, errors = cls.build_from_env(read_env_file(env_path))
        if errors:
            raise ConfigError(errors)
        return settings

    @classmethod
    def load_validated(cls, env_path: Path | None = None) -> "BrokerSettings":
        """Boot entry point: parse AND validate, one aggregated report."""
        settings, errors = cls.build_from_env(read_env_file(env_path))
        errors.extend(settings.validate())
        if errors:
            raise ConfigError(errors)
        return settings
