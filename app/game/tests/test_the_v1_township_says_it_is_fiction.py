"""The v1 township is served during live runs and shows none of them.

`UI-28`. `township.html` + `township.js` + `village.js` are the pure-fiction
v1 viewer, kept "for comparison" - and served at `/township.html` by the
live game server. So during a real run it is one URL away, its residents
walk a schedule the browser invents, its money and government and
conversations are computed client-side, and nothing in the rendering tells
a viewer that none of it came from the run.

Deleting it is operator decision 12 (keep the file-only showcase, or delete
the browser simulation), which is open. Labelling it is not: a page that
cannot be mistaken for the live town costs nothing and pre-empts nothing.

`archive/village3.js` was deleted outright - 1,255 lines referenced by no
page, no script and no test.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from game.config import GameSettings
from game.server import create_app

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_the_page_declares_itself_simulated() -> None:
    html = (STATIC / "township.html").read_text(encoding="utf-8")
    assert "SIMULATED TOWNSHIP" in html, (
        "UI-28: the v1 township is reachable during a live run with nothing "
        "on it saying that what it shows is invented"
    )


def test_the_notice_is_visible_not_just_a_comment() -> None:
    """A warning only in the HTML source is a warning nobody reads. It has
    to be in the body, above the map."""
    html = (STATIC / "township.html").read_text(encoding="utf-8")
    body = html[html.index("<body>"):] if "<body>" in html else ""
    assert "SIMULATED TOWNSHIP" in body


def test_it_points_at_the_live_page() -> None:
    html = (STATIC / "township.html").read_text(encoding="utf-8")
    assert "township-v2.html" in html


def test_the_dead_archive_is_gone() -> None:
    """Referenced by no page, script or test."""
    assert not (STATIC / "js" / "township" / "archive" / "village3.js").exists()


def test_the_live_page_carries_no_such_warning() -> None:
    """The guard on the guard: township-v2 binds to the world and must not
    be labelled fiction."""
    html = (STATIC / "township-v2.html").read_text(encoding="utf-8")
    assert "SIMULATED TOWNSHIP" not in html


def test_the_page_is_still_served() -> None:
    """Labelled, not removed - decision 12 has not been made."""
    with TestClient(create_app(settings=GameSettings(mock_mode=True))) as client:
        assert client.get("/township.html").status_code == 200
