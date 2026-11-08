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


KIMI_MODEL_ID = "moonshotai.kimi-k2.5"
DEEPSEEK_MODEL_ID = "deepseek.v3.2"
QWEN_MODEL_ID = "qwen.qwen3-coder-480b"

OPENAI_LUNA6_MODEL_ID = "gpt-6-luna"
OPENAI_LUNA56_MODEL_ID = "gpt-5.6-luna"

OPENAI_MODELS: tuple[str, ...] = (OPENAI_LUNA6_MODEL_ID, OPENAI_LUNA56_MODEL_ID)

OPENAI_REGION = "openai"

BEDROCK_MODELS: tuple[str, ...] = (KIMI_MODEL_ID, DEEPSEEK_MODEL_ID, QWEN_MODEL_ID)

MODEL_ALLOWLIST: tuple[str, ...] = (
    KIMI_MODEL_ID,
    DEEPSEEK_MODEL_ID,
    QWEN_MODEL_ID,
    OPENAI_LUNA6_MODEL_ID,
    OPENAI_LUNA56_MODEL_ID,
)

MODEL_PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    KIMI_MODEL_ID: (0.36, 1.80),
    DEEPSEEK_MODEL_ID: (0.37, 1.11),
    QWEN_MODEL_ID: (0.60, 1.44),
    OPENAI_LUNA6_MODEL_ID: (0.10, 0.50),
    OPENAI_LUNA56_MODEL_ID: (0.20, 1.20),
}

BEDROCK_LANE_REGIONS: tuple[str, ...] = ("ap-south-1", "us-east-1", "us-west-2")

RPM_PER_LANE = 100

DEFAULT_SPEND_CAP_USD = 400.0
DEFAULT_SPEND_WARN_FRACTION = 0.8

DEFAULT_MOCK_LEDGER_BALANCE_CENTS = 0

BEDROCK_SERVICE_TIERS: tuple[str, ...] = ("priority", "default", "flex")

DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3
DEFAULT_CIRCUIT_BREAKER_COOLDOWN_S = 30.0


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
    mock_mode: bool = False

    world_base_url: str = "http://localhost:8100"
    world_http_timeout_s: float = 10.0

    auth_shared_secret: str = ""

    trace_batch_size: int = 20
    trace_flush_interval_s: float = 2.0
    request_reasoning: bool = False
    request_reasoning_purposes: tuple[str, ...] = ("auto:agent_turn",)
    request_reasoning_budget_tokens: int = 2048

    trace_sqlite_path: str = "/data/kasukabe.db"
    supabase_url: str = ""
    supabase_service_key: str = ""

    rpm_per_lane: int = RPM_PER_LANE
    openai_rpm: int = 500
    openai_api_key: str = ""
    cognition_first_model: str = ""
    only_model: str = ""

    spend_cap_usd: float = DEFAULT_SPEND_CAP_USD
    spend_warn_fraction: float = DEFAULT_SPEND_WARN_FRACTION

    mock_ledger_balance_cents: int = DEFAULT_MOCK_LEDGER_BALANCE_CENTS

    circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    circuit_breaker_cooldown_s: float = DEFAULT_CIRCUIT_BREAKER_COOLDOWN_S

    bedrock_inference_profiles: dict[str, str] = field(default_factory=dict)
    bedrock_service_tier: str = "flex"

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
            if not self.world_base_url:
                errors.append("WORLD_BASE_URL is required when MOCK_MODE=false")
            if not self.trace_sqlite_path:
                errors.append("TRACE_SQLITE_PATH is required when MOCK_MODE=false")

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
