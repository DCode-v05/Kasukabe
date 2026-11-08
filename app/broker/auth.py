"""Agent identity from the auth header - DESIGN.md section 9's "agent id
from auth header" requirement.

herald (engine/src/herald/transport.py's CitadelClient) sends every
request with both ``Authorization: Bearer <token>`` and ``x-api-key:
<token>`` carrying the same value - it mirrors whatever the Anthropic SDK
used to send so a gateway sees an identical request either way. It reads
the token from ``settings.cdt_key`` (``CDT_KEY`` env var on the engine
side), which is exactly the seam Kasukabe uses: each agent's microVM is
provisioned with CDT_URL pointed at broker and CDT_KEY set to that
agent's broker credential. herald itself needs zero code changes for
this to work - see DESIGN.md section 9, "herald base_url -> broker".

Token format is ``<agent_id>`` or ``<agent_id>.<hex-hmac>``. Unsigned
tokens are accepted when ``BrokerSettings.auth_shared_secret`` is empty
(the current single-host deployment - DESIGN.md section 9 puts herald and
broker on the same m5.metal box with no network boundary between them to
defend against yet). Once a secret is configured, every token must carry
a valid HMAC-SHA256(agent_id, secret) suffix - infra/'s job to mint these
at agent-provisioning time; this module only verifies.
"""

from __future__ import annotations

import hashlib
import hmac

from broker.errors import AuthenticationError


def extract_bearer_token(headers: dict[str, str]) -> str:
    """Pull the raw credential out of Authorization or x-api-key.

    Authorization takes precedence (matches transport.py's own comment:
    "the SDK also sent x-api-key by default, so keep it for parity" -
    Authorization is the primary channel, x-api-key the belt-and-suspenders
    duplicate). Headers are matched case-insensitively since HTTP header
    names are case-insensitive and different clients normalize differently.
    """
    lowered = {k.lower(): v for k, v in headers.items()}

    auth = lowered.get("authorization", "")
    if auth:
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
        if token:
            return token

    api_key = lowered.get("x-api-key", "").strip()
    if api_key:
        return api_key

    raise AuthenticationError(
        "no credential found - send 'Authorization: Bearer <token>' or 'x-api-key: <token>'"
    )


def sign_agent_token(agent_id: str, secret: str) -> str:
    """Mint a signed bearer token for an agent. Used by infra/ provisioning
    and by tests; not called from the request path."""
    mac = hmac.new(secret.encode("utf-8"), agent_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{agent_id}.{mac}"


# INFRA-05. Which peers may use an UNSIGNED credential.
#
# With no AUTH_SHARED_SECRET the broker trusts the bearer token's agent_id
# field as-is, so any process that can open a socket to it can bill any
# resident's wallet by naming them. The comment on
# `BrokerSettings.auth_shared_secret` justifies that default as correct
# "for the current single-host deployment ... there is no network boundary
# between herald and broker to defend against yet".
#
# That is a sound argument and nothing enforced it.
# `infra/systemd/kasukabe-broker.service` binds `0.0.0.0:8200` for the
# microVM bridge, and `start-genesis.sh` runs uvicorn with
# `--host 0.0.0.0`, so the premise was false on both of the deployments
# that exist.
#
# Enforced against the ACTUAL PEER rather than against a mirrored copy of
# the bind address: a setting that restates the truth without being the
# truth is its own defect, and there is nothing here to keep in sync.
LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def resolve_agent_id(
    headers: dict[str, str],
    auth_shared_secret: str,
    peer: "str | None" = None,
) -> str:
    """Extract and (if a secret is configured) verify the agent id.

    Raises AuthenticationError on a missing credential, an empty agent id,
    or (secret configured) a missing/invalid signature. Uses
    hmac.compare_digest for the signature check - a raw ``==`` on a MAC
    is a timing side-channel, and this is exactly the kind of comparison
    that check exists for.
    """
    token = extract_bearer_token(headers)

    if not auth_shared_secret:
        # INFRA-05: unsigned is a LOCAL convenience, not a deployment mode.
        # `peer=None` means the caller did not say, which is every existing
        # in-process caller and every test - those are local by
        # construction. A caller that knows the peer and reports a remote
        # one is refused, because on that path naming a resident is the
        # whole of the authentication.
        if peer is not None and peer not in LOOPBACK_PEERS:
            raise AuthenticationError(
                f"unsigned credentials are accepted from this host only, and "
                f"{peer} is not local. Set AUTH_SHARED_SECRET and sign the "
                "token as '<agent_id>.<hex-hmac>' - without it, naming a "
                "resident is the only thing standing between a caller and "
                "that resident's wallet"
            )
        agent_id = token.split(".", 1)[0] if "." in token else token
        if not agent_id:
            raise AuthenticationError("credential did not contain an agent id")
        return agent_id

    if "." not in token:
        raise AuthenticationError(
            "credential is unsigned but AUTH_SHARED_SECRET is configured - "
            "expected '<agent_id>.<hex-hmac>'"
        )
    agent_id, _, signature = token.partition(".")
    if not agent_id or not signature:
        raise AuthenticationError("credential did not contain both an agent id and a signature")

    expected = hmac.new(
        auth_shared_secret.encode("utf-8"), agent_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AuthenticationError("credential signature did not verify")
    return agent_id
