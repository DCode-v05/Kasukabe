"""Documentation a new engineer would follow into the wrong behaviour.

`UI-21`. `GameSettings.mock_mode` defaults to **False** and `MOCK_MODE` is
no longer read from the environment - the game serves a real world or
nothing. Four docstrings still said it defaults to True, which is what the
module tells you it does when you open it:

  * `game/config.py` - "MOCK_MODE defaults to True"
  * `game/server.py` - "boots with ... (mock_mode=True)"
  * `game/mock_world.py` x2 - "by default", "`make run-game`'s default
    mock_mode=True"

A reader following any of them expects `make run-game` to serve a fake
town with no world running, and spends time working out why it does not.

This test is deliberately narrow: it checks the DEFAULT against what the
prose claims, because that is the specific drift. It is not a general
"docstrings must be true" check, which is not testable.
"""
from __future__ import annotations

import dataclasses
import inspect

from game import config as config_module
from game import mock_world as mock_world_module
from game import server as server_module
from game.config import GameSettings


def test_the_default_really_is_false() -> None:
    """The fact the prose has to match."""
    default = {
        f.name: f.default for f in dataclasses.fields(GameSettings)
    }["mock_mode"]
    assert default is False


def test_no_module_claims_the_default_is_true() -> None:
    """The four places that said otherwise. Matched on the CLAIM, not on
    any mention of mock_mode - tests constructing GameSettings(mock_mode=
    True) are legitimate and must not trip this."""
    claims = (
        "MOCK_MODE defaults to True",
        "mock_mode defaults to True",
        "default (mock_mode=True)",
        "default mock_mode=True",
    )
    for module in (config_module, server_module, mock_world_module):
        source = inspect.getsource(module)
        for claim in claims:
            assert claim not in source, (
                f"UI-21: {module.__name__} still tells a reader "
                f"{claim!r}, and the default is False"
            )
