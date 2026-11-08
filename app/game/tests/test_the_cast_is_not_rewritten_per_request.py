"""The 1.7 MB cast must not be decoded and re-encoded on every request.

`UI-19`. `version_module_imports` buffers a response, decodes it to `str`,
regex-scans it for relative ES-module specifiers, re-encodes and re-sends.
That is right for `index.html` and the small modules - a browser keys its
module cache by URL, so a rebuilt image is never re-fetched without a
version query - and it is wrong for `agents.js`, which:

  * is served from a DYNAMIC route, so it cannot be short-circuited by
    static-file handling;
  * is about 1.7 MB, and this ran per request, per tab, on the hot path of
    the page a person is watching;
  * contains no imports at all. It is one `const AGENTS = [...]`, so every
    byte of that work produced a byte-identical result.
"""
from __future__ import annotations

from pathlib import Path

CAST = Path(__file__).resolve().parents[1] / "static" / "js" / "township" / "agents.js"


def test_the_cast_has_no_relative_imports_to_version() -> None:
    """The premise of the exemption. If the cast ever gains an import, this
    fails and the exemption has to be reconsidered rather than silently
    serving a stale module."""
    from game.server import MODULE_IMPORT_RE

    text = CAST.read_text(encoding="utf-8", errors="replace")
    assert not MODULE_IMPORT_RE.search(text), (
        "agents.js now contains a relative import, so exempting it from "
        "version_module_imports would serve a stale module (UI-19)"
    )


def test_the_rewrite_never_runs_for_the_cast(monkeypatch) -> None:
    """The exemption, observed rather than grepped.

    The saving is invisible in the RESPONSE - `agents.js` has no imports,
    so the rewrite was a no-op on its contents and the bytes came back the
    same either way. What changed is that 1.7 MB is no longer decoded,
    scanned and re-encoded per request. The only way to see that is to
    watch whether the substitution is attempted at all.

    A source grep would have been the easier test and the wrong one -
    CORE-23 and EVAL-21 exist because this repository was full of tests
    that matched source text and passed on a comment.
    """
    from fastapi.testclient import TestClient

    from game import server
    from game.config import GameSettings

    calls: list[int] = []
    real = server.MODULE_IMPORT_RE

    class CountingPattern:
        def sub(self, *args, **kwargs):
            calls.append(1)
            return real.sub(*args, **kwargs)

        def search(self, *args, **kwargs):
            return real.search(*args, **kwargs)

    monkeypatch.setattr(server, "MODULE_IMPORT_RE", CountingPattern())
    client = TestClient(server.create_app(settings=GameSettings(mock_mode=True)))

    assert client.get("/js/township/agents.js").status_code == 200
    assert not calls, (
        "UI-19: the 1.7 MB cast is still being scanned and re-encoded on "
        "every request for it"
    )

    client.get("/")
    assert calls, "the module-versioning rewrite stopped running entirely"


def test_a_module_with_imports_is_still_versioned() -> None:
    """The guard on the guard: exempting one path must not disable the
    rewrite everywhere. `index.html` still has to come back versioned, or
    a rebuilt image is never re-fetched."""
    from fastapi.testclient import TestClient

    from game.config import GameSettings
    from game.server import create_app

    client = TestClient(create_app(settings=GameSettings(mock_mode=True)))
    body = client.get("/").text
    assert "?v=" in body, "the module-versioning rewrite stopped working"
