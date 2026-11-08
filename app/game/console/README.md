# game/console/

The operator's President+God console (DESIGN.md section 8): a standalone
FastAPI service, its own process, port **8300** by default. Serves the
console page at `/` and a JSON API at `/api/*` that proxies to world's
admin HTTP API - see `__init__.py`'s module docstring for why this is a
separate process from `game/server.py`'s live town view rather than one
shared FastAPI app (different auth boundary: this console is
operator-only, the town view has none).

## Status

Built and tested (`game/console/tests/`). Runs standalone with **no**
environment configuration (`OPERATOR_TOKEN` unset falls back to a fresh
random per-process token, logged at boot - see `config.py`'s module
docstring), and with no running `world/` process (every route that
reaches world is only exercised at request time, not at boot). Talking
to a *real* `world/` deployment needs `WORLD_BASE_URL` pointed at it and
`OPERATOR_TOKEN` set to the same secret world's admin API is configured
with.

## Request pipeline (`app.py`)

browser -> `/api/<tab>/<action>` -> `require_operator_token` (auth.py,
verifies `Authorization: Bearer <token>` against `OPERATOR_TOKEN`) ->
one `WorldAdminClient` method (world_client.py) -> HTTP call to world's
admin API with the same token forwarded -> response validated against
this package's own pydantic view in `schemas.py` -> JSON back to the
browser. No step in this pipeline decides anything about the simulation;
every decision (does the petition get approved, does the map grow, is
this prayer silently denied) happens inside world/.

## Open integration point: world's admin API

`world/` does not implement an admin HTTP surface yet (`world/app.py` is
still the `/health`-only scaffold as of this writing, per its own module
docstring - this repo's several world/ submodules like `charter/`,
`life/`, and `map/` already implement the internal logic these routes
need, they are just not wired to HTTP yet). `world_client.py`'s
`WorldAdminClient` codifies the exact contract this console needs;
whoever wires world's admin HTTP API should implement these routes (or
the two of you should adjust `world_client.py`/`schemas.py` to match
whatever world actually exposes - keep both sides in sync by hand, there
is no shared codegen between the two packages, see console/__init__.py's
module docstring on why game/ and world/ do not share Python imports).

Every route below is under `{world_base_url}/admin` and expects
`Authorization: Bearer <operator_token>` - the SAME token this console's
own `/api/*` routes are gated behind (`OPERATOR_TOKEN`), so world
independently re-validates instead of trusting this proxy blindly.
Request/response bodies are exactly `schemas.py`'s pydantic models,
JSON-encoded.

```
President - land petitions (world.charter.land_petitions.LandPetitionQueue
+ world.map.service.MapService.expand_land):
  GET  /admin/president/petitions                      -> [LandPetitionView]
  POST /admin/president/petitions/{id}/approve          body ApprovePetitionRequest
                                                          -> PetitionDecisionView
                                                          (approve triggers the real
                                                          map expansion, DESIGN.md
                                                          section 8)
  POST /admin/president/petitions/{id}/deny             body DenyPetitionRequest
                                                          -> LandPetitionView

President - amendments (world.charter.amendments.AmendmentEngine):
  GET  /admin/president/amendments                      -> [AmendmentProposalView]
  POST /admin/president/amendments/{id}/sign             -> AmendmentProposalView
  POST /admin/president/amendments/{id}/veto             body VetoAmendmentRequest
                                                          -> AmendmentProposalView

President - address to the nation (new world state, no owning module yet):
  POST /admin/president/broadcast                        body BroadcastRequest
                                                          -> BroadcastView
  GET  /admin/president/broadcasts                       -> [BroadcastView]

President - honors (new world state, no owning module yet):
  POST /admin/president/honors                           body HonorRequest
                                                          -> HonorView
  GET  /admin/president/honors                           -> [HonorView]

God - prayer feed (new world state, no owning module yet - see
schemas.py's module docstring for why grant/silent-deny are two routes,
not one route with a flag):
  GET  /admin/god/prayers                                -> [PrayerView]
  POST /admin/god/prayers/{id}/grant                      body GrantPrayerRequest
                                                          -> PrayerDecisionView
  POST /admin/god/prayers/{id}/silent-deny                -> PrayerDecisionView
                                                          (must NOT emit anything on
                                                          a religion.* / agent-visible
                                                          channel - DESIGN.md section 8:
                                                          "silent-deny leaves NO trace
                                                          visible to agents")

God - baby blessing queue (world.life.birth: submit_birth_request /
grant_birth_request / deny_birth_request already implement this logic,
just not wired to HTTP):
  GET  /admin/god/blessings                              -> [BirthRequestView]
  POST /admin/god/blessings/{id}/grant                    -> BirthDecisionView
  POST /admin/god/blessings/{id}/deny                     body DenyBlessingRequest
                                                          -> BirthDecisionView

God - disasters, miracles, misfortunes (new world state, no owning
module yet):
  POST /admin/god/disasters                               body DisasterRequest
                                                          -> DisasterView
  GET  /admin/god/disasters                               -> [DisasterView]
  POST /admin/god/miracles                                body MiracleRequest
                                                          -> MiracleView
  GET  /admin/god/miracles                                -> [MiracleView]
  POST /admin/god/misfortunes                             body MisfortuneRequest
                                                          -> MisfortuneView
  GET  /admin/god/misfortunes                             -> [MisfortuneView]

God - population dials (world.life.population.PopulationDials, already
a real model - just needs one mutable store + these two routes):
  GET  /admin/god/population-dials                       -> PopulationDialsView
  PUT  /admin/god/population-dials                        body PopulationDialsUpdate
                                                          -> PopulationDialsView

Spend - live burn tracking (task spec: "show live spend + burn rate
prominently"; world.ledger.core.Ledger already tracks all three pools,
this is one read route over Ledger.accounts):
  GET  /admin/spend/status                                -> SpendStatusView
                                                          (burned/treasury/wallets USD
                                                          cents + budget_total_usd_cents,
                                                          must sum per app/CONVENTIONS.md's
                                                          $1000 invariant - schemas.py
                                                          re-checks this on receipt. Burn
                                                          RATE is not a field: the console
                                                          polls this route on a timer and
                                                          derives $/hour from two samples
                                                          client-side, see static/
                                                          console.js's renderSpend())

Alert feed (crime spike, cult centralization, amendment tabled, regime
change - reads world.core.events.EventBus.recent() filtered to these
categories once the emitting systems exist):
  GET  /admin/alerts?limit=50                             -> [AlertView]

Top-level dials (world.core.pacing.PacingController +
world.core.checkpoint.CheckpointManager, both already real - just need
these four routes):
  GET  /admin/control/status                              -> SimControlStatus
  POST /admin/control/pause                                -> SimControlStatus
  POST /admin/control/resume                               -> SimControlStatus
  PUT  /admin/control/tick-speed                           body TickSpeedRequest
                                                          -> SimControlStatus
  POST /admin/control/checkpoint-now                       -> CheckpointSummary
```

`tests/fake_world_admin.py` implements this exact contract in-memory
behind an `httpx.MockTransport`, and every route above has at least one
test in `tests/` exercising the full path: `TestClient` -> `app.py`'s
route -> `WorldAdminClient` -> the fake transport -> back. That is the
closest thing to a real integration test this package can have before
world/'s HTTP API exists (app/CONVENTIONS.md: "an httpx mock transport"
is the sanctioned stand-in for a real dependency in tests).

## Auth: the operator token

One shared secret (`OPERATOR_TOKEN`), not a per-agent credential -
DESIGN.md section 8 describes one operator wearing two hats, not 250
agents each needing their own login. Send it as either
`Authorization: Bearer <token>` or `X-Operator-Token: <token>` (auth.py
accepts both, matching the two header conventions broker/auth.py already
established for agent tokens). Compared with `hmac.compare_digest`, same
timing-safety reasoning as broker/auth.py's HMAC check.

If `OPERATOR_TOKEN` is not set, the console still boots (so `make test`
and a first `make run-console` never require any configuration) with a
random per-process token logged at startup - see `config.py`'s module
docstring for the full reasoning and `app.py`'s boot-time log line.

## Quickstart

```
make run-console   # uvicorn game.console.app:app on :8300
make test           # game/console/tests/ + app/tests/ module boundary check
```

The console page itself needs no build step - `static/index.html` loads
`static/console.css` and `static/console.js` directly, vanilla DOM +
`fetch()`, same "minimal tooling" posture as `game/server.py`'s town
view.
