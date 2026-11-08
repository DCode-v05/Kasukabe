"""An unsigned credential is a local convenience, not a deployment mode.

`INFRA-05`. With no `AUTH_SHARED_SECRET` the broker trusts the bearer
token's `agent_id` field as it stands, so any process that can open a
socket to it can bill any resident's wallet by naming them. The comment
on `BrokerSettings.auth_shared_secret` defends that default as correct
"for the current single-host deployment ... there is no network boundary
between herald and broker to defend against yet".

That is a sound argument, and nothing enforced it.
`infra/systemd/kasukabe-broker.service` binds `0.0.0.0:8200` for the
microVM bridge and `start-genesis.sh` runs uvicorn with `--host 0.0.0.0`,
so the premise was false on both of the deployments that exist.

Second half: `AUTH_SHARED_SECRET` and `OPERATOR_TOKEN` were fixed
literals in a committed launcher, and therefore in git history for good.
The operator token is the whole of the console's authentication - grant a
blessing, unleash a disaster, move the population dials.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from broker.auth import AuthenticationError, resolve_agent_id

APP = Path(__file__).resolve().parents[2]
SIGNED = ""
HEADERS = {"authorization": "Bearer a0042"}


def test_a_local_caller_may_still_name_itself() -> None:
    """Every existing in-process caller and every test runs this way, and
    must keep working."""
    assert resolve_agent_id(HEADERS, SIGNED) == "a0042"
    assert resolve_agent_id(HEADERS, SIGNED, peer="127.0.0.1") == "a0042"
    assert resolve_agent_id(HEADERS, SIGNED, peer="::1") == "a0042"


def test_a_remote_caller_may_not() -> None:
    """The finding: naming a resident IS the authentication on this path."""
    with pytest.raises(AuthenticationError) as caught:
        resolve_agent_id(HEADERS, SIGNED, peer="10.0.4.7")
    assert "AUTH_SHARED_SECRET" in str(caught.value), (
        "the refusal must say what to do about it, or the next operator "
        "turns the check off"
    )


def test_a_signed_credential_works_from_anywhere() -> None:
    """The restriction is on the UNSIGNED path only - a deployment that
    configures a secret is unaffected."""
    import hmac
    import hashlib

    secret = "s3cret"
    mac = hmac.new(secret.encode(), b"a0042", hashlib.sha256).hexdigest()
    headers = {"authorization": f"Bearer a0042.{mac}"}
    assert resolve_agent_id(headers, secret, peer="10.0.4.7") == "a0042"


def test_the_peer_comes_from_the_connection_not_the_request() -> None:
    """A header the caller could set would be an authentication check the
    caller performs on itself."""
    source = (APP / "broker" / "app.py").read_text(encoding="utf-8")
    stamp = 'headers["x-kasukabe-peer"] = request.client.host if request.client else ""'
    assert stamp in source, (
        "INFRA-05: the peer is not taken from the connection, so a client "
        "can claim to be local"
    )
    assert source.index("headers = dict(request.headers)") < source.index(stamp)


def test_the_launcher_holds_no_secret_of_its_own() -> None:
    source = (APP / "start-genesis.sh").read_text(encoding="utf-8")
    for name in ("AUTH_SHARED_SECRET", "OPERATOR_TOKEN"):
        literals = re.findall(rf"-e {name}=([^\s\\]+)", source)
        for value in literals:
            assert value.startswith('"$') or value.startswith("$"), (
                f"INFRA-05: {name} is a literal in a committed launcher "
                f"({value!r}), and therefore in git history for good"
            )


def test_an_unset_secret_becomes_a_fresh_one_not_a_known_one() -> None:
    """A default is worse than nothing here: it is a password everybody
    has. Generated means an operator who sets neither gets a run nobody
    else can drive, rather than a run everybody can."""
    source = (APP / "start-genesis.sh").read_text(encoding="utf-8")
    for name in ("AUTH_SHARED_SECRET", "OPERATOR_TOKEN"):
        assign = re.search(rf"^{name}=\$\{{{name}:-(.+)\}}$", source, re.M)
        assert assign, f"INFRA-05: {name} is not taken from the environment"
        assert "secrets.token_hex" in assign.group(1), (
            f"INFRA-05: {name} falls back to a fixed value, which is a "
            "shared password rather than a secret"
        )


def test_an_internal_error_does_not_echo_itself_to_the_caller() -> None:
    """`f"internal error: {exc}"` sent raw exception text - file paths,
    SQL, connection strings, whatever the bug happened to carry - to
    whoever asked. The log already has the full traceback."""
    source = (APP / "broker" / "app.py").read_text(encoding="utf-8")
    assert 'BrokerError(f"internal error: {exc}")' not in source, (
        "INFRA-05: the broker echoes raw exception text to its callers"
    )
    assert "logger.exception" in source, "and the traceback must still be logged"
