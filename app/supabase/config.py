"""Configuration for the supabase/ package: the trace store.

DESIGN.md section 9 (decided 2026-07-30) replaces the original two-backend
design (a real Supabase/PostgREST project, with SQLite as a local-dev
fallback) with a single one: **SQLite (WAL mode) on the genesis host is
the production trace store**, continuously replicated to S3 by Litestream
(app/infra/litestream.yml) rather than by talking to a second database
service over the network. There is no PostgREST client anymore
(postgrest_client.py was deleted with this decision) and no "which
backend" auto-detection to get wrong - every caller gets the same SQLite
client, in-process, whether that is a throwaway :memory: database in a
test or the real WAL file on the m5.metal genesis host.

What is still configurable is how that one SQLite database behaves:

- sqlite_path: ":memory:" (tests, the default) or a real file path (the
  genesis host, one WAL file Litestream tails).
- busy_timeout_ms: how long a writer blocks on SQLITE_BUSY before raising,
  instead of failing immediately - see sqlite_client.py's module
  docstring for why this matters even with a single-writer design (WAL
  readers never block a writer, but a second concurrent writer, e.g. a
  research/ job opening the same file read-write, does contend for the
  one write lock).
- s3_bucket / s3_prefix / s3_region: where the gzip payload archiver
  (archiver.py) uploads raw LLM payloads and world-checkpoint blobs in
  production. Empty s3_bucket (the default, and what every test uses)
  means "no S3 configured" - archiver.py's storage falls back to a local
  directory next to the SQLite file, which is also the dev/test path
  (app/CONVENTIONS.md: "Never call a real LLM API in a test" extends to
  never requiring real AWS credentials for a test either). AWS
  credentials themselves are never pydantic fields here - s3_client.py
  resolves them from the standard AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
  / AWS_SESSION_TOKEN env vars or, failing that, the EC2 instance role via
  IMDSv2, exactly where every other AWS tool looks, so this config model
  never has a secret sitting in a field that could be logged or repr()'d.

Mirrors engine/src/config.py's aggregate-error philosophy at a much
smaller scale: SupabaseConfigError carries every problem with a requested
configuration at once, not just the first one hit.
"""

from __future__ import annotations

import os
from typing import Mapping

from pydantic import BaseModel, Field

ENV_SQLITE_PATH = "KASUKABE_SQLITE_PATH"
ENV_BUSY_TIMEOUT_MS = "KASUKABE_SQLITE_BUSY_TIMEOUT_MS"
ENV_S3_BUCKET = "KASUKABE_S3_BUCKET"
ENV_S3_PREFIX = "KASUKABE_S3_PREFIX"
ENV_S3_REGION = "AWS_REGION"
ENV_S3_REGION_FALLBACK = "AWS_DEFAULT_REGION"


class SupabaseConfigError(ValueError):
    """Aggregated configuration failure, mirroring engine's ConfigError.

    Every problem with a requested configuration is collected before this
    is raised, so e.g. a non-numeric busy timeout and a bucket set without
    a region are both reported in one error instead of one restart per
    mistake.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid supabase configuration ({len(self.errors)} error(s)):\n  - {joined}")


class SupabaseConfig(BaseModel):
    """The trace store's configuration: one SQLite database, its crash-
    safety pragmas, and where its archived blobs go.
    """

    sqlite_path: str = ":memory:"

    busy_timeout_ms: int = Field(default=5000, ge=0)

    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_region: str = ""

    request_timeout_s: float = Field(default=10.0, gt=0)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SupabaseConfig:
        """Build a config from the environment (or an explicit mapping in
        tests). Raises SupabaseConfigError, aggregating every problem, if
        what was given cannot be used as-is.
        """
        source = env if env is not None else os.environ
        sqlite_path = source.get(ENV_SQLITE_PATH, ":memory:").strip() or ":memory:"
        busy_timeout_raw = source.get(ENV_BUSY_TIMEOUT_MS, "").strip()
        s3_bucket = source.get(ENV_S3_BUCKET, "").strip()
        s3_prefix = source.get(ENV_S3_PREFIX, "").strip()
        s3_region = (
            source.get(ENV_S3_REGION, "").strip() or source.get(ENV_S3_REGION_FALLBACK, "").strip()
        )

        errors: list[str] = []

        busy_timeout_ms = 5000
        if busy_timeout_raw:
            try:
                busy_timeout_ms = int(busy_timeout_raw)
                if busy_timeout_ms < 0:
                    errors.append(f"{ENV_BUSY_TIMEOUT_MS}={busy_timeout_raw!r} must be >= 0")
                    busy_timeout_ms = 5000
            except ValueError:
                errors.append(f"{ENV_BUSY_TIMEOUT_MS}={busy_timeout_raw!r} is not an integer")

        if s3_bucket and not s3_region:
            errors.append(
                f"{ENV_S3_BUCKET}={s3_bucket!r} is set but neither {ENV_S3_REGION} nor "
                f"{ENV_S3_REGION_FALLBACK} is - S3 requests must be signed for a specific region"
            )

        if errors:
            raise SupabaseConfigError(errors)

        return cls(
            sqlite_path=sqlite_path,
            busy_timeout_ms=busy_timeout_ms,
            s3_bucket=s3_bucket,
            s3_prefix=s3_prefix,
            s3_region=s3_region,
        )
