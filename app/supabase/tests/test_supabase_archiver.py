"""PayloadArchiver tests against a real SqliteClient backend (a temp file,
not mocked): what matters here is that the bytes actually round-trip
through gzip and land on disk correctly, and a mock would just assert
"archiver called upload_archive with some bytes" without verifying they
are the right bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from supabase.archiver import (
    PayloadArchiver,
    build_llm_call_archive_key,
    build_world_snapshot_archive_key,
)
from supabase.config import SupabaseConfig
from supabase.sqlite_client import SqliteClient


@pytest.fixture
def client(tmp_path: Path) -> SqliteClient:
    return SqliteClient(SupabaseConfig(sqlite_path=str(tmp_path / "kasukabe.sqlite3")))


@pytest.mark.asyncio
async def test_archive_payload_writes_gzip_that_decompresses_to_the_original_json(client: SqliteClient) -> None:
    archiver = PayloadArchiver(client)
    payload = {"model": "moonshotai.kimi-k2.5", "prompt": "what do you do at the market?", "response": "I sell bread."}

    result = await archiver.archive_payload(key="agent-001/1.json.gz", payload=payload)

    written = client.local_storage_root / "llm-payloads" / "agent-001" / "1.json.gz"
    decompressed = gzip.decompress(written.read_bytes())
    assert json.loads(decompressed) == payload
    assert result.storage_path == "llm-payloads/agent-001/1.json.gz"
    assert result.bucket == "llm-payloads"
    assert result.content_type == "application/json"


@pytest.mark.asyncio
async def test_archive_payload_reports_accurate_sizes(client: SqliteClient) -> None:
    archiver = PayloadArchiver(client)
    payload = {"a": "b" * 1000}
    raw_expected = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")

    result = await archiver.archive_payload(key="x.json.gz", payload=payload)

    assert result.original_size_bytes == len(raw_expected)
    assert result.compressed_size_bytes < result.original_size_bytes


@pytest.mark.asyncio
async def test_archive_payload_hash_matches_the_uncompressed_serialization(client: SqliteClient) -> None:
    archiver = PayloadArchiver(client)
    payload = {"z": 1, "a": 2}

    result = await archiver.archive_payload(key="x.json.gz", payload=payload)

    expected_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    assert result.sha256_hex == expected_hash


@pytest.mark.asyncio
async def test_archive_payload_serialization_is_key_order_independent(client: SqliteClient) -> None:
    """sort_keys=True means two dicts with the same content in different
    key order archive to byte-identical (and therefore hash-identical)
    output - the property that makes the hash meaningful for later
    integrity verification.
    """
    archiver = PayloadArchiver(client)

    result_a = await archiver.archive_payload(key="a.json.gz", payload={"prompt": "hi", "model": "kimi"})
    result_b = await archiver.archive_payload(key="b.json.gz", payload={"model": "kimi", "prompt": "hi"})

    assert result_a.sha256_hex == result_b.sha256_hex


@pytest.mark.asyncio
async def test_archive_bytes_handles_non_json_content(client: SqliteClient) -> None:
    archiver = PayloadArchiver(client, bucket="world-snapshots")
    raw = b"\x00\x01binary-world-state-blob\xff"

    result = await archiver.archive_bytes(key="snap.bin.gz", raw=raw, content_type="application/octet-stream")

    written = client.local_storage_root / "world-snapshots" / "snap.bin.gz"
    assert gzip.decompress(written.read_bytes()) == raw
    assert result.bucket == "world-snapshots"


@pytest.mark.asyncio
async def test_archive_many_archives_every_item_in_order(client: SqliteClient) -> None:
    archiver = PayloadArchiver(client)
    items = [(f"{i}.json.gz", {"n": i}) for i in range(3)]

    results = await archiver.archive_many(items)

    assert [r.key for r in results] == ["0.json.gz", "1.json.gz", "2.json.gz"]
    for i, result in enumerate(results):
        written = client.local_storage_root / "llm-payloads" / f"{i}.json.gz"
        assert json.loads(gzip.decompress(written.read_bytes())) == {"n": i}


def test_build_llm_call_archive_key_groups_by_agent_and_zero_pads_tick() -> None:
    key = build_llm_call_archive_key(agent_id="agent-001", tick=42, llm_call_id=7)

    assert key == "agent-001/0000000042_7.json.gz"


def test_build_world_snapshot_archive_key_zero_pads_tick() -> None:
    key = build_world_snapshot_archive_key(tick=144, reason="daily")

    assert key == "0000000144_daily.json.gz"
