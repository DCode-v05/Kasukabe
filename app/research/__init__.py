"""Research package.

Metric jobs, dream probes, and control businesses for the 7 research
tracks (DESIGN.md section 10): political science, economics, sociology,
religion, machine psychology, business readiness, MAS engineering. Also
parquet_export.py: the per-game-day Parquet export job (DESIGN.md section
9) that turns the trace store's SQLite tables into Parquet files for
offline DuckDB analysis - see that module's docstring.

Read-only against the trace store (supabase/) and the world server's read
HTTP API - this package never mutates world state directly and never
imports boto3 (parquet_export.py's S3 upload goes through
supabase.s3_client.S3Client, the same boto3-free client archiver.py uses,
not a second AWS integration). Where a track needs an LLM call (e.g.
dream probes), it goes through broker/'s HTTP API like every other
caller.
"""

from __future__ import annotations
