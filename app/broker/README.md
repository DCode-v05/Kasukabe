# broker/

Standalone FastAPI service, the ONLY path to Bedrock (DESIGN.md section 9,
app/CONVENTIONS.md module boundaries). Exposes an Anthropic-Messages-
compatible `POST /v1/messages` so an agent's herald can point `base_url`
here unmodified - see `engine/src/herald/transport.py` for the wire shape
this implements.

## Status

Built and tested (`app/broker/tests/`). Runs standalone against
`MOCK_MODE=true` (the default - see `config.py`) with no AWS credentials,
no running `world/` process, and no Supabase project. `MOCK_MODE=false`
is the real deployment path infra/ wires up.

## Request pipeline (`service.py`)

auth -> allowlist -> spend-cap check -> six-lane routing (`lanes.py`:
Kimi preferred for cognition, DeepSeek for background work, overflow to
DeepSeek when Kimi saturates) -> translate to Bedrock converse ->
pre-dispatch wallet check (refuse if insufficient) -> the selected lane's
own 100 RPM bucket -> Bedrock call on that lane's own regional client ->
exact-cost metering -> ledger debit -> lane circuit-breaker feedback ->
spend governor -> buffered trace + event write. See `service.py`'s
module docstring for the full walkthrough and every DESIGN.md/
CONVENTIONS.md citation behind each step, and `lanes.py`/`spend.py` for
the routing policy and the process-wide USD cap.

`GET /admin/lanes` and `GET /admin/spend` expose the six lanes' live
circuit/bucket state and the spend governor's spent/burn-rate/projected-
exhaustion numbers.

## Open integration point: world's ledger API

`world/` does not implement a ledger HTTP API yet (this repo's build
order per DESIGN.md section 12 is world+tools before broker+ledger, but
broker is being built against the `world/` scaffold rather than a
finished ledger). `ledger.py`'s `HttpLedgerClient` codifies the contract
broker needs; whoever builds `world/`'s ledger should either implement
these two endpoints or the two of you should adjust `ledger.py` to match
whatever world actually exposes:

```
GET {world_base_url}/ledger/payer/{agent_id}
  200 -> {"agent_id": str, "payer_agent_id": str,
          "balance_cents": int, "is_minor": bool}
  404 -> unknown agent

POST {world_base_url}/ledger/debit
  body: {"agent_id": str, "amount_cents": int, "purpose": str,
         "metadata": dict}
  200 -> {"payer_agent_id": str, "balance_cents_after": int}
  404 -> unknown agent
```

`payer_agent_id` resolves DESIGN.md section 4's "minors debit parents"
server-side, since world owns the family graph broker doesn't have a copy
of. `debit` never rejects for insufficient funds - by the time it's
called the Bedrock spend already happened; see `ledger.py`'s module
docstring for why that's correct, not a missing safety check.

`InMemoryLedgerClient` in the same file implements this same contract
in-memory (including minor -> parent resolution) and is what
`MOCK_MODE=true` and every test use instead.

## Auth: agent id from the auth header

Each agent's engine microVM sets `CDT_URL` to broker's URL and `CDT_KEY`
to that agent's broker credential - herald needs zero code changes for
this (see `auth.py`'s module docstring: herald already sends
`Authorization: Bearer <CDT_KEY>` and `x-api-key: <CDT_KEY>` on every
call, unconditionally). Token format is `<agent_id>` (trusted as-is when
`AUTH_SHARED_SECRET` is unset - the current single-host deployment has no
network boundary between herald and broker to defend against yet) or
`<agent_id>.<hex-hmac>` once a secret is configured. Minting signed
tokens at agent-provisioning time is infra/'s job; `auth.py::sign_agent_token`
is the function to call.

## Purpose tagging (trace records)

Anthropic's Messages API has no native "why was this call made" field,
and herald doesn't set one today. Trace records default `purpose` to
`"unspecified"`; a caller (world's tick loop, research's dream probes,
the learning centre) that wants a specific call tagged can set the
optional `X-Kasukabe-Purpose` header via herald's existing
`extra_headers` kwarg (already threaded all the way to the wire by
`engine/src/herald/transport.py` - no herald changes needed there either,
just passing `extra_headers={"X-Kasukabe-Purpose": "..."}` at whatever
call site wants it tagged).

## Quickstart

```
make run-broker   # uvicorn broker.app:app on :8200, MOCK_MODE=true by default
make test         # broker/tests/ + app/tests/ module boundary check
```
