# app/

The Kasukabe application: everything the 250-agent town runs on. Read
`/Vat/kasukabe/DESIGN.md` first - it's the source of truth for what each
module becomes. Read `app/CONVENTIONS.md` second, before writing code
here: coding standards, module boundaries, and the ledger law live there.

## Layout

- `world/` - world server: map, ticks, tools, ledger, charter, health,
  crime, genesis. Owns all world state (DESIGN.md section 9).
- `broker/` - Bedrock broker: allowlist, RPM buckets, wallet metering,
  traces. Only package allowed to talk to Bedrock.
- `game/` - Phaser town view, agent inspector, President/God console.
- `research/` - metric jobs, dream probes, control businesses (7 tracks,
  DESIGN.md section 10).
- `supabase/` - schema migrations (16 tables), realtime config, and a thin
  async client + payload archiver. See `supabase/README.md`.
- `infra/` - Firecracker rootfs build, metal-host bootstrap, deploy.

## Status

`supabase/` is built: 19 migrations (extensions, 16 tables, realtime
publication, storage buckets), a SQLite mirror for local dev, and an
async `SupabaseClient` (PostgREST + SQLite) with a payload archiver - see
`supabase/README.md`. `game/` and `infra/` hold a pointer README until
their owners start. `world/` and `broker/` status: check each package's
own tests for current coverage, this line is not kept in sync with their
in-progress work.

## Quickstart

```
make install    # uv sync
make test       # uv run pytest
make run-world  # uvicorn world.app:app on :8100
make run-broker # uvicorn broker.app:app on :8200
```
