"""Unit tests for supabase/config.py: the trace store's SQLite path,
crash-safety pragmas, and S3 archive target, plus SupabaseConfigError's
error aggregation.
"""

from __future__ import annotations

import pytest

from supabase.config import (
    ENV_BUSY_TIMEOUT_MS,
    ENV_S3_BUCKET,
    ENV_S3_PREFIX,
    ENV_S3_REGION,
    ENV_S3_REGION_FALLBACK,
    ENV_SQLITE_PATH,
    SupabaseConfig,
    SupabaseConfigError,
)


def test_from_env_defaults_to_in_memory_sqlite_when_nothing_set() -> None:
    config = SupabaseConfig.from_env({})

    assert config.sqlite_path == ":memory:"
    assert config.busy_timeout_ms == 5000
    assert config.s3_bucket == ""


def test_from_env_respects_explicit_sqlite_path() -> None:
    config = SupabaseConfig.from_env({ENV_SQLITE_PATH: "/tmp/kasukabe-test.sqlite3"})

    assert config.sqlite_path == "/tmp/kasukabe-test.sqlite3"


def test_from_env_respects_explicit_busy_timeout() -> None:
    config = SupabaseConfig.from_env({ENV_BUSY_TIMEOUT_MS: "10000"})

    assert config.busy_timeout_ms == 10000


def test_from_env_rejects_non_integer_busy_timeout() -> None:
    with pytest.raises(SupabaseConfigError) as exc_info:
        SupabaseConfig.from_env({ENV_BUSY_TIMEOUT_MS: "not-a-number"})

    assert len(exc_info.value.errors) == 1
    assert ENV_BUSY_TIMEOUT_MS in exc_info.value.errors[0]


def test_from_env_rejects_negative_busy_timeout() -> None:
    with pytest.raises(SupabaseConfigError) as exc_info:
        SupabaseConfig.from_env({ENV_BUSY_TIMEOUT_MS: "-1"})

    assert "must be >= 0" in exc_info.value.errors[0]


def test_from_env_reads_s3_settings_when_region_present() -> None:
    config = SupabaseConfig.from_env(
        {ENV_S3_BUCKET: "kasukabe-genesis-123", ENV_S3_PREFIX: "genesis-2026-07-31", ENV_S3_REGION: "ap-south-1"}
    )

    assert config.s3_bucket == "kasukabe-genesis-123"
    assert config.s3_prefix == "genesis-2026-07-31"
    assert config.s3_region == "ap-south-1"


def test_from_env_falls_back_to_aws_default_region() -> None:
    config = SupabaseConfig.from_env({ENV_S3_BUCKET: "b", ENV_S3_REGION_FALLBACK: "us-east-1"})

    assert config.s3_region == "us-east-1"


def test_from_env_bucket_without_any_region_raises_aggregated_error() -> None:
    with pytest.raises(SupabaseConfigError) as exc_info:
        SupabaseConfig.from_env({ENV_S3_BUCKET: "kasukabe-genesis-123"})

    assert len(exc_info.value.errors) == 1
    assert ENV_S3_BUCKET in exc_info.value.errors[0]


def test_from_env_aggregates_multiple_errors_at_once() -> None:
    with pytest.raises(SupabaseConfigError) as exc_info:
        SupabaseConfig.from_env({ENV_BUSY_TIMEOUT_MS: "nope", ENV_S3_BUCKET: "b"})

    assert len(exc_info.value.errors) == 2


def test_from_env_empty_sqlite_path_falls_back_to_in_memory() -> None:
    config = SupabaseConfig.from_env({ENV_SQLITE_PATH: "  "})

    assert config.sqlite_path == ":memory:"


def test_default_construction_has_no_s3_configured() -> None:
    config = SupabaseConfig()

    assert config.s3_bucket == ""
    assert config.sqlite_path == ":memory:"
    assert config.busy_timeout_ms == 5000
