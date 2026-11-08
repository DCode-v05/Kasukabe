"""Single operator token auth (DESIGN.md section 8: "Admin API must be
auth-gated (single operator token)").

Deliberately not imported from broker/auth.py even though the bearer-
token extraction logic looks similar: console/__init__.py's module
docstring explains why game/ keeps zero Python import coupling to any
other app/ package, wire-only. This file is the console's own copy, sized
for its one job (one shared secret, not per-agent HMAC-signed tokens -
there is exactly one operator, not 250 agents each needing their own
credential).

extract_operator_token mirrors the two header conventions this repo
already uses in broker/auth.py (Authorization: Bearer <token>, and a
second header as a plain fallback) so an operator can use whichever is
easier from curl or the console's own JS.
"""

from __future__ import annotations

import hmac

from fastapi import Header, Request

from game.console.errors import OperatorAuthError


def extract_operator_token(
    authorization: str | None, x_operator_token: str | None
) -> str:
    """Pulls the raw token out of either header. Authorization takes
    precedence, matching broker/auth.py's own precedence order."""
    if authorization:
        stripped = authorization.strip()
        if stripped.lower().startswith("bearer "):
            token = stripped[7:].strip()
        else:
            token = stripped
        if token:
            return token
    if x_operator_token and x_operator_token.strip():
        return x_operator_token.strip()
    raise OperatorAuthError(
        "no operator token found - send 'Authorization: Bearer <token>' or "
        "'X-Operator-Token: <token>'"
    )


def tokens_match(provided: str, expected: str) -> bool:
    """Timing-safe comparison (hmac.compare_digest) - a raw `==` on a
    shared secret is a timing side-channel, same reasoning as
    broker/auth.py's signature check."""
    if not expected:
        return False
    return hmac.compare_digest(provided, expected)


def require_operator_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> str:
    """FastAPI dependency: every mutating and read route under /api/*
    depends on this. Returns the verified token (app.py's routes pass it
    straight through to WorldAdminClient, so world independently
    re-validates the same secret rather than trusting this proxy)."""
    settings = request.app.state.settings
    token = extract_operator_token(authorization, x_operator_token)
    if not tokens_match(token, settings.operator_token):
        raise OperatorAuthError("operator token did not match")
    return token
