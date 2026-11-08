"""game/ package marker.

game/README.md's older claim that game/ is "not a Python package - this
is a JS/TS frontend project" predates two real server-side Python
subsystems now living under this package:

- game/ itself (this file's own directory - server.py, relay.py,
  world_client.py, mock_world.py, config.py, contracts.py, position.py):
  the live town view's FastAPI static server + WebSocket relay (DESIGN.md
  section 9, this module's own task spec). static/ under here is the
  actual JS/TS frontend project the old docstring meant - vanilla JS +
  Phaser 3, no build system.
- game/console (see console/README.md): the operator's President+God
  console, a separate FastAPI app with its own auth (OPERATOR_TOKEN).

Both import as regular packages (`game.server`, `game.console.app`, ...)
under pytest's `pythonpath = ["."]` (app/pyproject.toml) the same way
world/ and broker/ do, which is why this marker file exists at all - a
namespace package would work too, but an explicit docstring here is
where a reader who lands on `game/__init__.py` first finds out both
subsystems exist and how they relate.

Nothing under app/ imports from this package (app/CONVENTIONS.md, "How
modules talk": "nothing under app/ should import from game/"), and
game/server.py and game/console/app.py do not import each other either -
they are two independent FastAPI processes that both happen to live
under the same top-level package because DESIGN.md section 9's
architecture diagram draws them as one "GAME APP (Phaser)" box even
though this build splits them into two deployable services (the live
town view has no auth and is meant for anyone to watch; the console is
auth-gated and meant for the operator only - sharing one process would
mean sharing one auth boundary, which the console's OPERATOR_TOKEN model
is built specifically to avoid).
"""

from __future__ import annotations
