"""Console error hierarchy - one exception per HTTP outcome, one renderer.

Mirrors broker/errors.py's shape: every error this service's request
pipeline can raise carries its own status_code, and app.py registers one
exception handler that renders any ConsoleError the same way, so route
handlers just raise and never format a JSONResponse by hand.
"""

from __future__ import annotations


class ConsoleError(Exception):
    """Base for every error the console's request pipeline can raise."""

    status_code: int = 500

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def to_body(self) -> dict[str, object]:
        return {"detail": self.message}


class OperatorAuthError(ConsoleError):
    """Missing or wrong operator token on a gated route (auth.py)."""

    status_code = 401


class WorldAdminUnavailableError(ConsoleError):
    """world's admin API could not be reached at all (connection refused,
    DNS failure, timeout) - distinct from WorldAdminError, which means
    world *was* reached and returned an error status. The operator UI
    shows these differently: "world server is down" vs "world rejected
    that action"."""

    status_code = 503


class WorldAdminError(ConsoleError):
    """world's admin API was reached and returned a non-2xx response.
    status_code is whatever world responded with, passed straight
    through so a 404 (unknown petition id) or a 409 (already decided)
    reaches the operator as the same status world gave it, not a
    generic 500."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)
