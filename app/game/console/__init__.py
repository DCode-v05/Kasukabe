"""The President+God operator console (DESIGN.md section 8).

One console, two hats, over the live game view:

- President tab: land petition queue (approve -> triggers map expansion),
  amendment sign/veto, address-to-the-nation broadcast, honors.
- God tab: live prayer feed with grant / silent-deny (silent-deny leaves
  NO trace visible to agents), baby blessing queue, disaster picker (crop
  blight, storm, quake, plague) with a scope selector, miracle and
  misfortune actions targeting an agent, population dials.
- An alert feed (crime spike, cult centralization, amendment tabled,
  regime change) and top-level dials (pause/resume, tick speed,
  checkpoint-now).

Architecture (app/CONVENTIONS.md, "game/ is presentation only" +
"How modules talk"): this package computes NO game logic locally. Every
button click ends up as one HTTP call from this package's own small
FastAPI app to world's admin HTTP API (see world_client.py's module
docstring and README.md's "Open integration point" for the exact
contract) - approvals, grants, disasters, dials, all of it happen inside
world/, never here. This package is a separate FastAPI service (its own
process, own port - see config.py) so "served by the same FastAPI
server" (the TASK spec's wording) means the console page and its own
`/api/*` proxy routes share one process, not that this package reaches
into world.app's ASGI object: game/README.md is explicit that nothing
under app/ imports from game/, and the reverse (game/ importing world/
internals) breaks the same boundary from the other side.

Every mutating route is auth-gated behind a single shared operator
token (auth.py) - DESIGN.md section 8's console is a operator-only tool,
and the same token is forwarded to world's admin API so world
independently re-validates instead of trusting this proxy blindly.
"""

from __future__ import annotations
