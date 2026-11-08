"""Bedrock broker package.

The ONLY package in this monorepo allowed to import boto3 (DESIGN.md
section 9: "Broker is the ONLY path to Bedrock"). Owns the model
allowlist (kimi-k2.5, deepseek-v3.2 only), per-model RPM buckets, wallet
metering, parent-debit for minors, and async trace writes to Supabase.

Every other package reaches Bedrock exclusively through this package's
HTTP API - agents point their herald base_url here, they never hold
Bedrock credentials themselves.
"""

from __future__ import annotations
