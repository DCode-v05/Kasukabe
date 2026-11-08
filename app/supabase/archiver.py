"""Payload archiver: gzip raw LLM payloads (and full world checkpoints)
before they go to Storage.

Why this exists as its own module instead of inlining gzip.compress calls
at each call site (broker/ after every inference call, world/ once a
game-day): the archive path is a repeated shape - serialize, compress,
hash, upload, report what happened - and the hash is not optional. A
payload's sha256 is the only way a researcher re-reading an archived
prompt/response months later can confirm the blob Storage handed back is
byte-identical to what broker/ actually sent to Bedrock, which matters for
the "complete causal record" claim in DESIGN.md section 2: a record you
cannot verify was not tampered with in Storage is not actually complete.

Two entry points:

- archive_payload(): the one the module brief describes ("gzips raw LLM
  payloads for Storage upload"). Takes a JSON-serializable dict (the raw
  request+response broker/ sent/received) and a storage key.
- archive_bytes(): the lower-level building block, for anything that is
  not already a dict - world_snapshots.state_archive_path points at a
  full serialized world checkpoint (DESIGN.md section 9), which is
  whatever bytes world/'s own serializer produces, not necessarily JSON.

Both return an ArchiveResult with the storage path plus enough metadata
(sizes, hash) that the caller's own insert/update into llm_calls or
world_snapshots has everything it needs without a second round-trip to
Storage to ask "how big was that."
"""

from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any

from pydantic import BaseModel

from supabase.client import SupabaseClient


class ArchiveResult(BaseModel):
    """What archiving one blob produced. Crosses the world//broker
    boundary (whichever module called the archiver reads this to fill in
    its own row), so it is a pydantic model per app/CONVENTIONS.md.
    """

    bucket: str
    key: str
    storage_path: str
    content_type: str
    original_size_bytes: int
    compressed_size_bytes: int
    sha256_hex: str


class PayloadArchiver:
    """Gzips a payload and uploads it through a SupabaseClient's Storage
    surface: S3 in production (SqliteClient.upload_archive routes there
    once KASUKABE_S3_BUCKET is set, via s3_client.py) or a local-filesystem
    stand-in in dev/test - the archiver itself does not know or care
    which, that decision lives entirely in the client it was constructed
    with.
    """

    def __init__(self, client: SupabaseClient, *, bucket: str = "llm-payloads") -> None:
        self.client = client
        self.bucket = bucket

    async def archive_bytes(
        self, *, key: str, raw: bytes, content_type: str = "application/octet-stream"
    ) -> ArchiveResult:
        compressed = gzip.compress(raw, compresslevel=6)
        digest = hashlib.sha256(raw).hexdigest()
        storage_path = await self.client.upload_archive(
            bucket=self.bucket, path=key, data=compressed, content_type="application/gzip"
        )
        return ArchiveResult(
            bucket=self.bucket,
            key=key,
            storage_path=storage_path,
            content_type=content_type,
            original_size_bytes=len(raw),
            compressed_size_bytes=len(compressed),
            sha256_hex=digest,
        )

    async def archive_payload(self, *, key: str, payload: dict[str, Any]) -> ArchiveResult:
        """Archive a JSON-serializable raw LLM request/response payload.

        sort_keys=True makes the serialization (and therefore the hash)
        deterministic across two dicts with the same content but different
        key insertion order, which matters if a caller ever re-serializes
        the same logical payload to verify a hash later.
        """
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return await self.archive_bytes(key=key, raw=raw, content_type="application/json")

    async def archive_many(
        self, items: list[tuple[str, dict[str, Any]]]
    ) -> list[ArchiveResult]:
        """Archive several payloads sequentially, in call order.

        Not parallelized: broker/'s archiving happens off the hot
        inference path already (DESIGN.md section 9: "Broker ... batches
        trace writes async, never blocks inference"), so there is no
        latency budget this needs to protect, and sequential keeps the
        SQLite backend (a single connection) from needing its own
        internal locking here.
        """
        return [await self.archive_payload(key=key, payload=payload) for key, payload in items]


def build_llm_call_archive_key(*, agent_id: str, tick: int, llm_call_id: int) -> str:
    """Storage key convention for an archived LLM payload.

    Grouped by agent then by a zero-padded tick so a directory listing
    sorts into a per-agent timeline directly, the same access pattern
    idx_llm_calls_agent_tick exists to serve at the Postgres layer.
    """
    return f"{agent_id}/{tick:010d}_{llm_call_id}.json.gz"


def build_world_snapshot_archive_key(*, tick: int, reason: str) -> str:
    """Storage key convention for an archived world checkpoint."""
    return f"{tick:010d}_{reason}.json.gz"
