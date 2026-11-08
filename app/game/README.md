# game/

Phaser town view + agent inspector + President/God console (DESIGN.md
section 8, architecture diagram in section 9), split into two
independent FastAPI services that share this top-level package:

- **This directory** (`server.py`, `relay.py`, `world_client.py`,
  `mock_world.py`, `config.py`, `contracts.py`, `position.py`,
  `static/`): the live town view. A small FastAPI process that serves a
  vanilla-JS + Phaser 3 frontend (no build system, lifted from
  `smallville/environment/frontend_server/templates/home` and
  `templates/demo`) and relays world/'s event bus to every connected
  browser tab over a WebSocket. No auth - meant for anyone to watch.
  `make run-game` (default port **8400**).
- **`console/`**: the operator's auth-gated President+God admin API and
  its own frontend. See `console/README.md`. `make run-console` (default
  port 8300).

Base map and character sprite assets come from
`smallville/environment/frontend_server/static_dirs/assets/`
(Apache-2.0, attribution preserved at `game/static/assets/LICENSE` and
in `game/static/index.html`'s footer).

Both services talk to the rest of the system only over world/'s HTTP API
(and, for this directory, a world-owned WebSocket relay - DESIGN.md
section 9: "The game app is fed by the world server's own WebSocket
relay (no external realtime)"), never by importing world/'s internals or
reading Supabase directly (app/CONVENTIONS.md, "How modules talk"; see
`contracts.py`'s module docstring for the exact HTTP/WS contract this
directory expects from world/, which world/'s own HTTP API does not
implement yet as of this writing - `game/mock_world.py`'s
`MockWorldClient` stands in until it does). Nothing under `app/` imports
from `game/` either direction.
