"""Kasukabe's trace store: the SQLite (WAL) database DESIGN.md section 9
names as the production system of record, its async client, and the
gzip payload archiver that ships raw LLM payloads and world checkpoints
to S3 (app/CONVENTIONS.md's supabase/ module boundary).

This is the local Python package `supabase`, not the `supabase-py` PyPI
SDK. The directory is named `supabase/` for history, not architecture:
this project talked to a real hosted Supabase project over PostgREST
until DESIGN.md section 9's 2026-07-30 decision replaced that with a
single SQLite file replicated to S3 by Litestream, and renaming the
directory now would be a bigger, riskier change than the actual
persistence work for no functional gain - see README.md's "Why still
called supabase/" for the full reasoning. Anyone adding `supabase` (the
PyPI package) as a dependency later should be aware this local package
would shadow it on sys.path and should import it under an alias.

Public surface:

- config.SupabaseConfig, config.SupabaseConfigError: the trace store's
  SQLite path, crash-safety pragmas, and S3 archive target, from
  environment variables or explicit construction.
- client.SupabaseClient: the async interface (insert/select/update/
  upload_archive), plus the eq/neq/gt/.../in_ filter helpers.
- factory.get_client(): the one function callers use to obtain a client;
  never construct SqliteClient directly.
- archiver.PayloadArchiver, archiver.ArchiveResult: gzip + hash + upload
  for raw LLM payloads and world checkpoints.
- s3_client.S3Client, s3_client.S3Config: the boto3-free S3 PUT client
  archiver.py's SqliteClient backend uses in production, and that
  research/'s Parquet export job reuses for its own uploads.

migrations/ (Postgres DDL, dormant - see README.md) and sqlite_schema.sql
(the trace store's actual, live schema) are not imported from Python;
sqlite_schema.sql is applied by SqliteClient's own automatic first-use
bootstrap.
"""

from __future__ import annotations

from supabase.archiver import (
    ArchiveResult,
    PayloadArchiver,
    build_llm_call_archive_key,
    build_world_snapshot_archive_key,
)
from supabase.client import (
    Row,
    SupabaseClient,
    SupabaseError,
    eq,
    gt,
    gte,
    ilike,
    in_,
    is_,
    like,
    lt,
    lte,
    neq,
)
from supabase.config import SupabaseConfig, SupabaseConfigError
from supabase.factory import get_client
from supabase.s3_client import S3Client, S3Config, S3Error

__all__ = [
    "ArchiveResult",
    "PayloadArchiver",
    "Row",
    "S3Client",
    "S3Config",
    "S3Error",
    "SupabaseClient",
    "SupabaseConfig",
    "SupabaseConfigError",
    "SupabaseError",
    "build_llm_call_archive_key",
    "build_world_snapshot_archive_key",
    "eq",
    "get_client",
    "gt",
    "gte",
    "ilike",
    "in_",
    "is_",
    "like",
    "lt",
    "lte",
    "neq",
]
