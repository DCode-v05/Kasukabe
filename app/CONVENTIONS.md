# app/ conventions

Rules for everything under `/Vat/kasukabe/app/`. The canonical spec is
`/Vat/kasukabe/DESIGN.md` - if this file and DESIGN.md ever disagree,
DESIGN.md wins and this file is wrong until fixed.

## Coding standards

- Python 3.12. Type hints on every function signature (parameters and
  return type); `from __future__ import annotations` at the top of every
  module.
- Async for anything that does I/O: HTTP calls, Bedrock calls, Supabase
  reads/writes, world-state mutation. Plain sync functions are fine for
  pure computation (ledger math, tool validation, tile geometry).
- No em dashes (`—`, U+2014) anywhere in `.py` files. Use a comma, a
  colon, or two sentences instead.
- No new leading-underscore module, function, class, or method names.
  Existing engine code (see `engine/src`) grandfathers some in; don't add
  more here. Private-by-convention is fine, just don't spell it with a
  leading underscore - use module-level placement or a clear name.
- Match engine idioms (`engine/src` is the reference): a module-level
  docstring that explains *why* the module is shaped the way it is, not
  just what it contains; comments earn their place by carrying
  information the code can't ("this fee is hardcoded because DESIGN.md
  section 3 hardcodes it," not "this sets the fee"); aggregate errors
  instead of failing on the first one where the caller benefits from
  seeing everything wrong at once (see `engine/src/config.py`'s
  `ConfigError`).
- Money is always an integer count of cents (USD) or an integer count of
  sim-currency units, never a float. `$1000` is `100_000` cents.
  Floating-point currency is a bug, not a style preference - it breaks
  the ledger invariant below by construction.
- Prefer `pydantic` models for anything that crosses a module boundary
  (HTTP request/response bodies, event payloads, tool call args/results).
  Internal-only data can be a plain dataclass.

## Module boundaries

These are enforced by `app/tests/test_module_boundaries.py`, not just
written down. A PR that breaks one fails CI.

- **`world/` owns ALL world state.** Map, ticks, tool registry, ledger,
  charter, health, crime, genesis. It is the single writer for every
  Supabase table that represents world state
  (`agents, transactions, businesses, organizations, petitions, cases,
  buildings, skills, tools_registry, relationships, world_snapshots`).
  No other package mutates world state directly, ever - not by writing
  to Supabase behind world's back, not by importing world internals and
  calling a private function. Go through world's async API (in-process)
  or its HTTP API (out-of-process).
- **`broker/` owns ALL Bedrock access.** It is the only package allowed
  to `import boto3`. Every model call from every other package - world's
  tool execution, research's dream probes, anything - goes through
  broker's HTTP API. Agents' herald `base_url` points at broker
  directly; broker is where the `{kimi-k2.5, deepseek-v3.2}` allowlist,
  the 100 RPM/model buckets, wallet metering, and parent-debit-for-minors
  live.
- **`game/` is presentation only.** It renders world state and lets the
  President/God operator issue commands. It never computes game logic
  locally - petitions, disasters, grants all go through world's HTTP
  API. It reads live state from Supabase Realtime, not by polling
  world's internals.
- **`research/` is read-only against the rest of the system.** It reads
  Supabase tables and world's read endpoints to compute metrics; it
  never writes world state. When a research instrument needs an LLM call
  (dream probes), it calls broker like any other caller - it does not
  get a side channel to Bedrock.
- **`supabase/`** is the trace store (DESIGN.md section 9, decided
  2026-07-30: SQLite in WAL mode is the production system of record,
  replicated to S3 by Litestream, superseding this bullet's original
  "schema migrations and realtime config only, no application logic"
  description from when this package fronted a hosted Supabase project).
  It is a real importable package now - the async client, the
  boto3-free S3 upload client the gzip payload archiver and `research/`'s
  Parquet export job both use, and the crash-safety connection setup -
  not just SQL files. See `supabase/README.md` for the full shape.
- **`infra/`** holds deploy and ops tooling. It drives the other
  packages from the outside (build images, start services, ship config);
  the other packages never import from it.

## Event naming scheme

Every event written to Supabase's `events` table, and every message
broadcast on a Realtime channel, has a `type` string of the form:

```
<domain>.<subject>.<past_tense_verb>
```

All lowercase, dot-separated, snake_case within each segment, verb
always past tense (the event records something that already happened -
tool calls are the only thing that can make something happen, so by the
time an event exists, it's history).

Domains match the DESIGN.md sections that own them:

| Domain | Owner | Example |
|---|---|---|
| `world` | world/ | `world.tick.advanced`, `world.land.expanded` |
| `agent` | world/ | `agent.moved`, `agent.spawned`, `agent.died` |
| `economy` | world/ | `economy.wallet.debited`, `economy.wallet.credited`, `economy.ledger.violated` |
| `business` | world/ | `business.registered`, `business.contract.won` |
| `crime` | world/ | `crime.theft.attempted`, `crime.warrant.issued` |
| `politics` | world/ | `politics.vote.cast`, `politics.charter.amended`, `politics.regime.changed` |
| `religion` | world/ | `religion.prayer.offered`, `religion.doctrine.emerged` |
| `health` | world/ | `health.ailment.onset`, `health.agent.died` |
| `broker` | broker/ | `broker.call.completed`, `broker.call.rejected`, `broker.rpm.throttled` |
| `research` | research/ | `research.probe.completed`, `research.metric.computed` |
| `system` | any | `system.checkpoint.written`, `system.sim.halted` |

Realtime channel names are `kasukabe:<domain>` (one channel per domain,
e.g. `kasukabe:economy`), so `game/` can subscribe to only what it needs
to render instead of the full firehose.

The `economy.ledger.violated` event is special: emitting it is the
tripwire action for the ledger invariant below, and any consumer that
sees it must treat the simulation as halted.

## How modules talk

- **world server** exposes two surfaces from the same running process:
  - an **internal async API** (plain async Python functions/classes) for
    anything running in-process with it (its own tool-registry
    execution, its own tick loop) - no HTTP round-trip for calls that
    never leave the process;
  - an **HTTP API** (FastAPI) for everything out-of-process: agents'
    tool calls arrive here, `game/`'s President/God console actions
    (approve petition, grant prayer, disaster picker) arrive here,
    `research/`'s read-only queries arrive here.
  - Default dev port: `8100` (`make run-world`).
- **broker** is a **separate FastAPI service**, its own process, not a
  library anyone imports. It is reached over HTTP only. Agents' herald
  `base_url` is broker's URL; broker never calls back into world, and
  world never calls broker on an agent's behalf - inference is always
  initiated by the agent (herald), metered and proxied by broker.
  - Default dev port: `8200` (`make run-broker`).
- **game** talks to world over HTTP for commands and to Supabase
  Realtime for live state. It never talks to broker (it has no reason to
  call Bedrock) and never talks to research directly.
- **research** talks to Supabase directly for historical data (nightly
  batch jobs) and to world's HTTP API for anything that needs current
  live state instead of a snapshot. When it needs an LLM call, it talks
  to broker over HTTP exactly like an agent would.
- No package imports another package's internals across this diagram.
  `from world.something import private_helper` from inside `research/`
  is a boundary violation even though Python's import system would allow
  it - the module boundary is a design rule, not a Python visibility
  rule, and `test_module_boundaries.py` only catches the boto3 case
  mechanically. The rest is enforced by code review.

## Test conventions

- Tests for a package live in that package's own `tests/` directory:
  `world/tests/`, `broker/tests/`, `research/tests/`. Keep each package
  self-contained since world, broker, and (eventually) game are separate
  deployable services - a test suite should be runnable without checking
  out a sibling package's internals.
- Cross-module invariant tests - things no single package owns, like the
  boto3 import boundary or (once the ledger exists) the $1000 invariant -
  live in the top-level `app/tests/`.
- Test file names are unique across the whole repo (`test_world_app.py`,
  not `test_app.py` repeated in every package) so pytest's default
  rootdir-based collection never hits a module-name collision.
- `pytest-asyncio` runs in `strict` mode (see `pyproject.toml`): mark
  every async test explicitly with `@pytest.mark.asyncio`, there is no
  implicit auto-detection.
- **Never call a real LLM API in a test.** No live Bedrock calls, no live
  broker calls that reach Bedrock. Stub or mock inference (an `httpx`
  mock transport, a fake broker client, a canned response fixture).
  Tests must be fast, free, and deterministic; they run in CI with no
  AWS credentials available.
- One behavior per test. Prefer several small, clearly-named tests over
  one test with many assertions - a failure should tell you what broke
  without reading the test body.
- Run the suite with `make test` (`uv run pytest` under the hood) before
  calling any change done.

## The ledger invariant (a law, not a guideline)

```
sum(agent wallets) + treasury + burned == $1000.00
```

This is DESIGN.md section 3's tripwire, and it is binding on every
module that touches money, not just `world/`'s ledger implementation:

- The system starts with exactly $1000: $500 genesis (250 x $2 agent
  wallets) + $500 government treasury. Every dollar that ever exists is
  one of those two pools until it is spent on inference, at which point
  it moves to `burned` and stays there - burned money is never
  un-burned, and no path in the system creates or destroys a dollar
  outside this equation.
- All three quantities are integer cents. `wallets + treasury + burned`
  must equal `100_000` cents at all times, checked after every
  transaction that moves money (a debit, a credit, a burn from an
  inference call, a tax, a fee, a fine, a pension).
- The check is a tripwire: on violation, emit `economy.ledger.violated`
  and halt the simulation. A silently-wrong ledger is worse than a
  stopped one - DESIGN.md section 3 is explicit that this halts the sim,
  not just logs a warning.
- Only `world/` (the ledger) and `broker/` (burning money on inference)
  ever mutate a wallet, the treasury, or the burned total. Everyone else
  - `game/`'s console, `research/`'s jobs - can read balances but never
  writes them.
- Once the ledger is implemented, its invariant check gets a test in
  `app/tests/` (see "Test conventions" above) exactly like the boto3
  boundary does today. Until then, this section is the law every module
  builder designs against.
