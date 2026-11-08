"""The console asks twice before doing something it cannot undo.

`UI-31`. Approve grows the map, Deny closes a petition, a disaster lands
on real residents and the population dials change who lives and dies -
every one of them fired on a single click, sitting a few pixels from the
button that does the opposite, with no confirmation anywhere in
`console.js`.

Second half of the same finding: `arena_names` has been in
`ApprovePetitionRequest` (`console/schemas.py:118`), in world's admin API
(`admin_api.py:146`) and in `expansion.py`'s `single_arena_rect_bands`
for a long time, and the console - its only caller - had no field for it.
DESIGN section 1's expansion "regenerates collision", but every approval
carved the new strip into one undivided arena because nobody could name
any.

The clicking is done by `console_petition_harness.js`, which runs the
REAL `renderPetition` from `console.js` in a Node VM against a small DOM.
Grepping the source for "confirmable(" would pass forever while the
button still fired on one click - this project has shipped exactly that
kind of test twice (CORE-23, EVAL-21).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "console_petition_harness.js"
CONSOLE_JS = Path(__file__).parent.parent / "static" / "console.js"
CONSOLE_CSS = Path(__file__).parent.parent / "static" / "console.css"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH on this host"
)


def click(times: int, arenas: str | None = None) -> dict:
    plan: dict = {"clicks": times}
    if arenas is not None:
        plan["arenas"] = arenas
    proc = subprocess.run(
        ["node", str(HARNESS), json.dumps(plan)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_one_click_sends_nothing() -> None:
    """The finding, stated as behaviour."""
    result = click(1)
    assert result["sent"] == [], (
        "UI-31: one click approved a petition and grew the map, with no "
        "confirmation between the operator and an irreversible change"
    )


def test_one_click_says_what_it_is_armed_to_do() -> None:
    """Armed silently is worse than not armed: the operator would think the
    click failed and go looking for the reason."""
    result = click(1)
    assert result["label"].startswith("Confirm:")
    assert "Approve" in result["label"]
    assert result["armed"] is True, "the style hook is missing, so nothing looks armed"


def test_the_second_click_does_it() -> None:
    result = click(2)
    assert len(result["sent"]) == 1
    assert result["sent"][0]["url"] == "/api/president/petitions/pet-1/approve"


def test_the_button_disarms_itself_after_firing() -> None:
    """Otherwise the next click - on a list that refreshes every 8 seconds -
    approves again."""
    result = click(2)
    assert result["armed"] is False
    assert not result["label"].startswith("Confirm:")


def test_a_third_click_does_not_send_twice() -> None:
    result = click(3)
    assert len(result["sent"]) == 1, (
        f"UI-31: {len(result['sent'])} approvals sent; the button re-armed "
        "instead of firing again, or did not disarm at all"
    )


def test_arena_names_reach_the_world() -> None:
    result = click(2, arenas="north field, orchard")
    assert result["sent"][0]["body"]["arena_names"] == ["north field", "orchard"], (
        "UI-31: arena_names is in the schema and in world's admin API and "
        "the console never sent it"
    )


def test_no_arenas_typed_means_null_not_empty_list() -> None:
    """`expand_land` reads an empty list as "carve it into no bands", which
    is not what an operator who typed nothing meant."""
    assert click(2, arenas="   ")["sent"][0]["body"]["arena_names"] is None
    assert click(2)["sent"][0]["body"]["arena_names"] is None


def test_every_irreversible_button_is_wrapped() -> None:
    """The reversible ones are deliberately left alone - a confirmation on
    a harmless button teaches the operator to click through
    confirmations."""
    source = CONSOLE_JS.read_text(encoding="utf-8")
    for action in ("approve", "deny", "sign", "veto", "grant", "silent-deny"):
        assert f'confirmable(item.querySelector(\'[data-action="{action}"]\')' in source, (
            f"UI-31: the {action} button still fires on one click"
        )
    for button in ("btn-disaster", "btn-miracle", "btn-misfortune", "btn-update-dials"):
        assert f'confirmable(document.getElementById("{button}")' in source, (
            f"UI-31: {button} still fires on one click"
        )
    for reversible in ("btn-pause", "btn-resume", "btn-checkpoint", "btn-apply-pacing"):
        assert f'confirmable(document.getElementById("{reversible}")' not in source, (
            f"{reversible} is reversible and does not need arming"
        )


def test_the_armed_state_is_visible() -> None:
    assert ".btn.armed" in CONSOLE_CSS.read_text(encoding="utf-8"), (
        "the button adds an `armed` class nothing styles, so an armed "
        "irreversible action looks exactly like a resting one"
    )
