"""A minimal S3 client: PUT object, signed with AWS Signature Version 4,
over httpx. No boto3.

app/CONVENTIONS.md reserves boto3 for broker/ ("It is the only package
allowed to import boto3" - DESIGN.md section 9: "Broker is the ONLY path
to Bedrock"), and app/tests/test_module_boundaries.py enforces that with
an AST scan, no exceptions. supabase/ (the trace store's gzip payload
archiver, archiver.py) and research/ (the per-game-day Parquet export
job) both need to put objects in S3 in production; neither is broker/,
so neither may pull in boto3 or aioboto3 (which is boto3 underneath) to
get there. AWS's Signature Version 4 process is public, documented, and
short enough (canonical request, string to sign, HMAC signing-key chain,
sign) to implement directly against httpx + hashlib/hmac from the
standard library, which is what this module does. It is deliberately not
a general S3 SDK: one operation, PUT object, because that is the only
thing anything in this repo needs S3 for.

Credential resolution matches the order every AWS SDK uses, so the exact
same env this project already documents (AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN) works unmodified, and so does
running with no credentials configured at all on an EC2 host that has an
instance profile attached (the m5.metal genesis host, DESIGN.md section
9) - see S3Client.credentials()'s docstring.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel

# IMDSv2 endpoint. Link-local, only reachable from inside an EC2 instance
# (or a Firecracker microVM configured to proxy it) - unreachable
# anywhere else, including every CI runner and every developer laptop,
# which is exactly why credential resolution tries static env vars first
# and only falls through to this with a short timeout.
IMDS_BASE = "http://169.254.169.254/latest"
IMDS_TIMEOUT_S = 2.0
# Refresh instance-role credentials this long before they actually
# expire, so an in-flight upload never races the credential expiring
# mid-request.
IMDS_REFRESH_SKEW_S = 60

# Fixed S3 key prefixes under the one genesis bucket - not a free per-call
# choice. infra/scripts/01-setup-s3-and-iam.sh provisions the bucket with
# exactly this layout and prints it as "the interface app-layer code
# should target" for this track's S3 client and litestream.yml; encoding
# it here (rather than letting each caller invent its own prefix) means a
# future rename only touches one file, and this project's S3 layout is
# never split between a shell script's comment and Python behavior
# actually agreeing on it by coincidence. archiver.py's two logical bucket
# names ("llm-payloads", "world-snapshots") map onto the first two;
# research/'s Parquet export job uses PARQUET_EXPORT_PREFIX directly.
ARCHIVE_BUCKET_PREFIXES: dict[str, str] = {
    "llm-payloads": "archives/payloads",
    "world-snapshots": "archives/checkpoints",
}
PARQUET_EXPORT_PREFIX = "exports/parquet"


class S3Config(BaseModel):
    """Where puts go. Credentials are never a field here (see
    resolve_credentials) so this model is safe to log or repr().
    """

    bucket: str
    region: str
    prefix: str = ""
    request_timeout_s: float = 10.0


class S3Error(RuntimeError):
    """A PUT to S3 failed. Carries enough to debug without a retry."""

    def __init__(self, *, key: str, detail: str, status_code: int | None = None) -> None:
        self.key = key
        self.detail = detail
        self.status_code = status_code
        where = f"key={key!r}"
        if status_code is not None:
            where += f" status={status_code}"
        super().__init__(f"s3 put_object failed ({where}): {detail}")


@dataclass
class Credentials:
    access_key_id: str
    secret_access_key: str
    session_token: str | None
    # None for static (env-var) credentials, which never expire from this
    # client's point of view; set for IMDS-sourced role credentials.
    expires_at_epoch_s: float | None = None

    def expired(self) -> bool:
        if self.expires_at_epoch_s is None:
            return False
        return time.time() >= self.expires_at_epoch_s - IMDS_REFRESH_SKEW_S


async def fetch_imds_role_credentials(http: httpx.AsyncClient) -> Credentials:
    """The instance-role fallback: token -> role name -> role credentials.

    This is IMDSv2 (token-gated; IMDSv1's tokenless GETs are disabled on
    a properly hardened instance), the same three-request flow boto3's
    own instance-metadata provider performs, reimplemented here without
    boto3 because that is the whole point of this module. Raises
    httpx.HTTPError (uncaught) on any step's failure - resolve_credentials
    wraps that into a clear S3Error, since "IMDS unreachable" almost
    always means "not running on EC2", not a transient fault worth hiding.
    """
    token_response = await http.put(
        f"{IMDS_BASE}/api/token",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
        timeout=IMDS_TIMEOUT_S,
    )
    token_response.raise_for_status()
    token = token_response.text

    headers = {"X-aws-ec2-metadata-token": token}
    role_response = await http.get(
        f"{IMDS_BASE}/meta-data/iam/security-credentials/", headers=headers, timeout=IMDS_TIMEOUT_S
    )
    role_response.raise_for_status()
    role_name = role_response.text.strip().splitlines()[0]

    creds_response = await http.get(
        f"{IMDS_BASE}/meta-data/iam/security-credentials/{role_name}",
        headers=headers,
        timeout=IMDS_TIMEOUT_S,
    )
    creds_response.raise_for_status()
    body: dict[str, Any] = creds_response.json()

    expires_at = datetime.strptime(body["Expiration"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return Credentials(
        access_key_id=body["AccessKeyId"],
        secret_access_key=body["SecretAccessKey"],
        session_token=body.get("Token"),
        expires_at_epoch_s=expires_at.timestamp(),
    )


def static_credentials_from_env(env: dict[str, str]) -> Credentials | None:
    """AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, the
    same names every AWS SDK reads first. Returns None (not an error) when
    they are absent, so resolve_credentials can fall through to IMDS.
    """
    access_key_id = env.get("AWS_ACCESS_KEY_ID", "").strip()
    secret_access_key = env.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if not access_key_id or not secret_access_key:
        return None
    session_token = env.get("AWS_SESSION_TOKEN", "").strip() or None
    return Credentials(
        access_key_id=access_key_id, secret_access_key=secret_access_key, session_token=session_token
    )


def sign_v4(
    *,
    method: str,
    host: str,
    canonical_uri: str,
    payload: bytes,
    region: str,
    service: str,
    credentials: Credentials,
    extra_headers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Returns the full header set (Authorization plus every header that
    was signed) for one SigV4-signed request. Pure function of its
    inputs - no I/O - so it is unit-testable by re-deriving the same
    signature independently rather than pinning a magic constant copied
    from documentation (see test_supabase_s3_client.py).

    Follows the four-step process AWS documents for SigV4: build the
    canonical request, build the string to sign, derive the signing key
    through the AWS4-HMAC-SHA256 chain (date -> region -> service ->
    "aws4_request"), then HMAC that key over the string to sign.
    """
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    if credentials.session_token:
        headers["x-amz-security-token"] = credentials.session_token
    if extra_headers:
        headers.update({k.lower(): v for k, v in extra_headers.items()})

    signed_header_names = sorted(headers.keys())
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in signed_header_names)
    signed_headers = ";".join(signed_header_names)

    canonical_request = "\n".join(
        [method, canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def hmac_sha256(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = hmac_sha256(f"AWS4{credentials.secret_access_key}".encode("utf-8"), date_stamp)
    k_region = hmac_sha256(k_date, region)
    k_service = hmac_sha256(k_region, service)
    k_signing = hmac_sha256(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    result = dict(headers)
    result["Authorization"] = authorization
    return result


def encode_s3_key(key: str) -> str:
    """URI-encode an S3 object key the way SigV4's CanonicalURI requires:
    every path segment percent-encoded, the "/" segment separators left
    alone. A bare urllib.parse.quote(key) would also encode the slashes
    in an archive key like "agent-001/0000000042_7.json.gz" and produce a
    canonical request that does not match what S3 itself computes from
    the request line, which fails the signature check, not with "bad key"
    but with the deeply unhelpful SignatureDoesNotMatch.
    """
    return "/" + "/".join(quote(segment, safe="~") for segment in key.split("/"))


class S3Client:
    """Puts objects in one S3 bucket. See module docstring for why this
    exists instead of boto3.
    """

    def __init__(self, config: S3Config, *, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.host = f"{config.bucket}.s3.{config.region}.amazonaws.com"
        # http_client is an injection seam for tests: pass an
        # httpx.AsyncClient built with httpx.MockTransport instead of a
        # real network transport (app/CONVENTIONS.md: "Never call a real
        # ... API in a test" extends to S3, same as it did to the deleted
        # PostgrestClient's Supabase project).
        self.http = http_client or httpx.AsyncClient(timeout=config.request_timeout_s)
        self.owns_http = http_client is None
        self.cached_credentials: Credentials | None = None

    async def credentials(self) -> Credentials:
        """Static env credentials first, falling back to the EC2 instance
        role via IMDSv2 (module docstring). Cached across calls; an
        instance-role credential set refetches once it is close to
        expiring (Credentials.expired()), a static one never does.
        """
        if self.cached_credentials is not None and not self.cached_credentials.expired():
            return self.cached_credentials
        static = static_credentials_from_env(dict(os.environ))
        if static is not None:
            self.cached_credentials = static
            return static
        try:
            role_credentials = await fetch_imds_role_credentials(self.http)
        except httpx.HTTPError as exc:
            raise S3Error(
                key="<credential resolution>",
                detail=(
                    "no AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in the environment and the EC2 "
                    f"instance metadata service was unreachable ({exc}) - this only works on an "
                    "EC2 host with an instance profile, or with static credentials set"
                ),
            ) from exc
        self.cached_credentials = role_credentials
        return role_credentials

    async def put_object(self, *, key: str, data: bytes, content_type: str) -> None:
        full_key = f"{self.config.prefix.rstrip('/')}/{key}" if self.config.prefix else key
        credentials = await self.credentials()
        canonical_uri = encode_s3_key(full_key)
        headers = sign_v4(
            method="PUT",
            host=self.host,
            canonical_uri=canonical_uri,
            payload=data,
            region=self.config.region,
            service="s3",
            credentials=credentials,
            extra_headers={"content-type": content_type},
        )
        response = await self.http.put(
            f"https://{self.host}{canonical_uri}", content=data, headers=headers
        )
        if response.status_code >= 400:
            raise S3Error(key=full_key, detail=response.text, status_code=response.status_code)

    async def close(self) -> None:
        if self.owns_http:
            await self.http.aclose()
