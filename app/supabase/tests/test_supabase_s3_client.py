"""Tests for supabase/s3_client.py: the boto3-free S3 PUT client.

sign_v4's golden-vector test (test_sign_v4_matches_botocore_reference)
does not pin a value copied from AWS documentation - it was produced by
running botocore's own S3SigV4Auth signer (the reference implementation
every other AWS SDK is checked against) against the exact same inputs
this test feeds sign_v4, with its clock frozen, and pasting the resulting
Authorization/X-Amz-Content-SHA256 headers as the expected values. That
cross-check was run once, live, while writing this test - not re-run in
CI (this repo has no botocore/boto3 dependency in anything but broker/,
app/CONVENTIONS.md's module boundary, and pulling one in here just to
regenerate a constant on every test run would be exactly the boto3
dependency this module exists to avoid). If AWS ever changes SigV4's
algorithm this constant would need regenerating the same way; nothing
about supabase/'s own code would need to.

Every other test here follows app/CONVENTIONS.md's "never call a real
... API in a test": httpx.MockTransport stands in for both S3 and the
IMDSv2 metadata service, no network, no real AWS credentials anywhere in
this file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import unquote

import httpx
import pytest

from supabase.s3_client import (
    ARCHIVE_BUCKET_PREFIXES,
    PARQUET_EXPORT_PREFIX,
    Credentials,
    S3Client,
    S3Config,
    S3Error,
    encode_s3_key,
    fetch_imds_role_credentials,
    sign_v4,
    static_credentials_from_env,
)


def test_sign_v4_matches_botocore_reference() -> None:
    creds = Credentials(
        access_key_id="AKIDEXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        session_token=None,
    )
    now = datetime(2013, 5, 24, 0, 0, 0, tzinfo=timezone.utc)

    headers = sign_v4(
        method="PUT",
        host="examplebucket.s3.amazonaws.com",
        canonical_uri="/agent-001/0000000042_7.json.gz",
        payload=b"hello world payload",
        region="us-east-1",
        service="s3",
        credentials=creds,
        extra_headers={"content-type": "application/json"},
        now=now,
    )

    assert (
        headers["x-amz-content-sha256"]
        == "7fb3e538715ec8c05a9bc94f401f9317f8d9e8ea03af0fb93992c95a8c87c591"
    )
    assert headers["x-amz-date"] == "20130524T000000Z"
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, "
        "Signature=a503b36a81bfed415b06367397f49a894745bca30e765956f3fd027adac68da1"
    )


def test_sign_v4_is_deterministic_for_fixed_inputs() -> None:
    creds = Credentials(access_key_id="AKID", secret_access_key="secret", session_token=None)
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    kwargs = dict(
        method="PUT",
        host="bucket.s3.ap-south-1.amazonaws.com",
        canonical_uri="/x.json.gz",
        payload=b"payload",
        region="ap-south-1",
        service="s3",
        credentials=creds,
        now=now,
    )

    assert sign_v4(**kwargs) == sign_v4(**kwargs)


def test_sign_v4_signature_changes_when_payload_changes() -> None:
    creds = Credentials(access_key_id="AKID", secret_access_key="secret", session_token=None)
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    base = dict(
        method="PUT",
        host="bucket.s3.ap-south-1.amazonaws.com",
        canonical_uri="/x.json.gz",
        region="ap-south-1",
        service="s3",
        credentials=creds,
        now=now,
    )

    sig_a = sign_v4(payload=b"payload one", **base)["Authorization"]
    sig_b = sign_v4(payload=b"payload two", **base)["Authorization"]

    assert sig_a != sig_b


def test_sign_v4_signature_changes_when_secret_key_changes() -> None:
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    base = dict(
        method="PUT",
        host="bucket.s3.ap-south-1.amazonaws.com",
        canonical_uri="/x.json.gz",
        payload=b"payload",
        region="ap-south-1",
        service="s3",
        now=now,
    )

    sig_a = sign_v4(
        credentials=Credentials(access_key_id="AKID", secret_access_key="secret-one", session_token=None),
        **base,
    )["Authorization"]
    sig_b = sign_v4(
        credentials=Credentials(access_key_id="AKID", secret_access_key="secret-two", session_token=None),
        **base,
    )["Authorization"]

    assert sig_a != sig_b


def test_sign_v4_includes_session_token_header_when_present() -> None:
    creds = Credentials(
        access_key_id="AKID", secret_access_key="secret", session_token="a-session-token"
    )
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)

    headers = sign_v4(
        method="PUT",
        host="bucket.s3.ap-south-1.amazonaws.com",
        canonical_uri="/x.json.gz",
        payload=b"payload",
        region="ap-south-1",
        service="s3",
        credentials=creds,
        now=now,
    )

    assert headers["x-amz-security-token"] == "a-session-token"
    assert "x-amz-security-token" in headers["Authorization"]


def test_sign_v4_signed_headers_sorted_alphabetically() -> None:
    creds = Credentials(access_key_id="AKID", secret_access_key="secret", session_token=None)

    headers = sign_v4(
        method="PUT",
        host="bucket.s3.ap-south-1.amazonaws.com",
        canonical_uri="/x.json.gz",
        payload=b"payload",
        region="ap-south-1",
        service="s3",
        credentials=creds,
        extra_headers={"content-type": "application/json"},
    )

    signed_headers = headers["Authorization"].split("SignedHeaders=")[1].split(",")[0]
    assert signed_headers == "content-type;host;x-amz-content-sha256;x-amz-date"


def test_encode_s3_key_preserves_slashes() -> None:
    assert encode_s3_key("agent-001/0000000042_7.json.gz") == "/agent-001/0000000042_7.json.gz"


def test_encode_s3_key_percent_encodes_special_characters() -> None:
    assert encode_s3_key("agent 001/file name.json") == "/agent%20001/file%20name.json"


def test_encode_s3_key_leaves_unreserved_characters_alone() -> None:
    key = "agent-001_v2.test~/file.json.gz"
    assert encode_s3_key(key) == f"/{key}"


def test_static_credentials_from_env_returns_none_when_absent() -> None:
    assert static_credentials_from_env({}) is None


def test_static_credentials_from_env_reads_standard_names() -> None:
    creds = static_credentials_from_env(
        {
            "AWS_ACCESS_KEY_ID": "AKID",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "token",
        }
    )

    assert creds is not None
    assert creds.access_key_id == "AKID"
    assert creds.secret_access_key == "secret"
    assert creds.session_token == "token"


def test_static_credentials_from_env_requires_both_key_and_secret() -> None:
    assert static_credentials_from_env({"AWS_ACCESS_KEY_ID": "AKID"}) is None


def test_static_style_credentials_never_expire() -> None:
    creds = Credentials(access_key_id="a", secret_access_key="b", session_token=None)
    assert creds.expired() is False


def test_role_credentials_expired_past_the_skew_window() -> None:
    import time

    creds = Credentials(
        access_key_id="a", secret_access_key="b", session_token=None,
        expires_at_epoch_s=time.time() + 30,
    )
    assert creds.expired() is True


def test_role_credentials_not_expired_well_before_expiry() -> None:
    import time

    creds = Credentials(
        access_key_id="a", secret_access_key="b", session_token=None,
        expires_at_epoch_s=time.time() + 3600,
    )
    assert creds.expired() is False


def make_client(
    *, config: S3Config | None = None, handler
) -> S3Client:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return S3Client(
        config or S3Config(bucket="kasukabe-genesis-123-ap-south-1", region="ap-south-1"),
        http_client=http_client,
    )


@pytest.mark.asyncio
async def test_put_object_sends_signed_put_with_static_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["content"] = request.content
        return httpx.Response(200)

    client = make_client(handler=handler)
    await client.put_object(key="archives/payloads/agent-001/1.json.gz", data=b"gzipped-bytes", content_type="application/gzip")
    await client.close()

    assert seen["method"] == "PUT"
    assert seen["url"] == (
        "https://kasukabe-genesis-123-ap-south-1.s3.ap-south-1.amazonaws.com/"
        "archives/payloads/agent-001/1.json.gz"
    )
    assert seen["content"] == b"gzipped-bytes"
    assert seen["headers"]["content-type"] == "application/gzip"
    assert seen["headers"]["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")


@pytest.mark.asyncio
async def test_put_object_applies_configured_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200)

    config = S3Config(bucket="b", region="ap-south-1", prefix="genesis-2026-07-31")
    client = make_client(config=config, handler=handler)
    await client.put_object(key="exports/parquet/game_day=0/events.parquet", data=b"x", content_type="application/octet-stream")
    await client.close()

    assert unquote(seen["url"]).endswith(
        "/genesis-2026-07-31/exports/parquet/game_day=0/events.parquet"
    )


@pytest.mark.asyncio
async def test_put_object_raises_s3_error_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="AccessDenied")

    client = make_client(handler=handler)
    with pytest.raises(S3Error) as exc_info:
        await client.put_object(key="x.json.gz", data=b"x", content_type="application/json")
    await client.close()

    assert exc_info.value.status_code == 403
    assert "AccessDenied" in exc_info.value.detail


@pytest.mark.asyncio
async def test_put_object_falls_back_to_imds_role_when_no_static_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/latest/api/token":
            assert request.method == "PUT"
            return httpx.Response(200, text="imds-token-abc")
        if request.url.path == "/latest/meta-data/iam/security-credentials/":
            assert request.headers["x-aws-ec2-metadata-token"] == "imds-token-abc"
            return httpx.Response(200, text="kasukabe-genesis-host-role\n")
        if request.url.path == "/latest/meta-data/iam/security-credentials/kasukabe-genesis-host-role":
            return httpx.Response(
                200,
                json={
                    "AccessKeyId": "ROLEAKID",
                    "SecretAccessKey": "rolesecret",
                    "Token": "roletoken",
                    "Expiration": "2099-01-01T00:00:00Z",
                },
            )
        assert request.method == "PUT"
        return httpx.Response(200)

    client = make_client(handler=handler)
    await client.put_object(key="x.json.gz", data=b"x", content_type="application/json")
    await client.close()

    assert any("api/token" in c for c in calls)
    assert any("security-credentials/" in c for c in calls)
    assert client.cached_credentials is not None
    assert client.cached_credentials.access_key_id == "ROLEAKID"


@pytest.mark.asyncio
async def test_credentials_cached_across_calls_with_static_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    put_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        put_count["n"] += 1
        return httpx.Response(200)

    client = make_client(handler=handler)
    first = await client.credentials()
    second = await client.credentials()
    await client.close()

    assert first is second


@pytest.mark.asyncio
async def test_credential_resolution_raises_s3_error_when_imds_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    client = make_client(handler=handler)
    with pytest.raises(S3Error) as exc_info:
        await client.put_object(key="x.json.gz", data=b"x", content_type="application/json")
    await client.close()

    assert "instance metadata service was unreachable" in exc_info.value.detail


@pytest.mark.asyncio
async def test_fetch_imds_role_credentials_parses_expiration(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/latest/api/token":
            return httpx.Response(200, text="tok")
        if request.url.path == "/latest/meta-data/iam/security-credentials/":
            return httpx.Response(200, text="my-role")
        return httpx.Response(
            200,
            json={
                "AccessKeyId": "A",
                "SecretAccessKey": "S",
                "Token": "T",
                "Expiration": "2030-06-15T10:30:00Z",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    creds = await fetch_imds_role_credentials(http_client)
    await http_client.aclose()

    assert creds.access_key_id == "A"
    assert creds.session_token == "T"
    assert creds.expires_at_epoch_s is not None


def test_archive_bucket_prefixes_cover_both_archiver_buckets() -> None:
    assert ARCHIVE_BUCKET_PREFIXES == {
        "llm-payloads": "archives/payloads",
        "world-snapshots": "archives/checkpoints",
    }


def test_parquet_export_prefix() -> None:
    assert PARQUET_EXPORT_PREFIX == "exports/parquet"
