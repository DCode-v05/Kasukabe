# supabase/ - the trace store

DESIGN.md section 9 (decided 2026-07-30) replaces this package's original
design - a real Supabase/PostgREST project as the system of record, with
a local SQLite fallback for dev/test - with a single one:

> **SQLite (WAL mode) on the host is the production trace store.**
> Litestream streams the WAL continuously to S3. Raw LLM payloads
> gzipped to S3. Per-game-day Parquet export to S3; research analysis
> via DuckDB.

There is no PostgREST client anymore, and no "which backend" driver
detection to get wrong. Every caller - a test's throwaway `:memory:`
database, a local-dev run against a real file, the genesis host's one
production WAL file - gets the exact same `SqliteClient`, applying the
exact same schema, with the exact same crash-safety pragmas.

## Why still called `supabase/`

This package talked to a real hosted Supabase project until the decision
above. The directory (and the `SupabaseClient`/`SupabaseConfig` class
names, and the `import supabase` package name) kept that name rather than
being renamed as part of the same change, for two reasons: nothing in
this repo had wired against it yet (grep the tree - only this package's
own tests and `infra/env/*.env.example` mention it, both updated
alongside this change), so a rename was free of *behavioral* risk; but
`app/CONVENTIONS.md` and `DESIGN.md` section 12's repo layout both name
the directory `app/supabase/` as the shared cross-team interface every
other track's module-boundary rules are written against, and this
track's brief was explicit about staying in its own lane. Renaming the
directory is a legitimate future cleanup; it is not this change.

## What is here

```
supabase/
  sqlite_schema.sql    the trace store's actual, live schema - applied by
                        SqliteClient itself, not a migration runner. Its
                        llm_calls/events tables are GENERATED from broker/
                        trace.py's TraceRecord/BrokerEvent - see schema_gen.py
  schema_gen.py          generates llm_calls/events DDL straight from
                          broker/trace.py's dataclasses (the ONE SOURCE OF
                          TRUTH fix, 2026-07-31) - see its own docstring
  sqlite_client.py      SqliteClient: the only SupabaseClient implementation
  repair_live_schema.py  pure-stdlib, re-runnable repair tool for a live
                          trace-store file whose schema has drifted from
                          sqlite_schema.sql - never drops a non-empty table
  s3_client.py           S3Client: boto3-free SigV4 PUT, for archiver.py
                          and research/parquet_export.py
  client.py               SupabaseClient (the interface) + filter helpers
  config.py                 SupabaseConfig: sqlite path, crash-safety
                             pragmas, S3 archive target
  factory.py                  get_client() - the one way callers get one
  archiver.py                  PayloadArchiver: gzip + hash + upload
  migrations/          dormant - see "migrations/ (dormant)" below
  tests/                31 files' worth of tests, see "Tests" below
```

## The client

```python
from supabase import get_client, eq

client = get_client()  # SupabaseConfig.from_env(): :memory: unless
                        # KASUKABE_SQLITE_PATH is set
rows = await client.insert("agents", [{"slug": "agent-001", ...}, ...])
mine = await client.select("agents", filters={"slug": eq("agent-001")})
await client.update("agents", filters={"slug": eq("agent-001")}, values={"occupation": "baker"})
await client.close()
```

`insert()` is a genuine batch insert (one transaction for the whole
list, not one per row). Filters use the eq/neq/gt/.../in_ helpers from
`supabase.client` - a naming convention (`"<op>.<operand>"` strings) left
over from when this package talked to PostgREST directly, kept because
there is no reason to invent a second filter syntax now that SQLite is
the only backend.

### Configuration

| Env var | Effect |
|---|---|
| `KASUKABE_SQLITE_PATH` | `:memory:` (default, every test) or a real file path (the genesis host: `/opt/kasukabe/data/kasukabe.db`, per `infra/env/world.env.example`) |
| `KASUKABE_SQLITE_BUSY_TIMEOUT_MS` | How long a writer blocks on `SQLITE_BUSY` before raising. Default 5000. |
| `KASUKABE_S3_BUCKET` | Empty (default, every test) means "archive to a local directory next to the sqlite file". Set on the genesis host to the bucket `infra/scripts/01-setup-s3-and-iam.sh` provisions. |
| `KASUKABE_S3_PREFIX` | Optional extra prefix under the bucket (e.g. a per-run identifier). |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | Required if `KASUKABE_S3_BUCKET` is set - S3 requests must be signed for a specific region. |

## Crash safety: WAL + busy_timeout + synchronous=NORMAL

`sqlite_client.py`'s `connect_sqlite()` sets three pragmas on every
connection it opens against a real file (`:memory:` databases skip WAL -
SQLite has no write-ahead log for them):

- `journal_mode = WAL` - readers never block the writer and vice versa,
  which is what lets Litestream tail the WAL and `research/`'s Parquet
  export job read the database concurrently with world/ and broker/
  still writing to it.
- `busy_timeout` (configurable, default 5s) - a second connection
  contending for the one write lock blocks and retries instead of
  immediately raising "database is locked".
- `synchronous = NORMAL` - SQLite's own documentation describes this as
  safe from corruption in WAL mode, and durable across an application
  crash (a killed process); the only thing it does not guarantee is
  durability across an OS-level power loss, which this project accepts
  in exchange for not paying an fsync on every commit.

`tests/test_supabase_crash_safety.py` proves the application-crash half
of that claim empirically rather than citing the documentation: it
launches a real writer as a separate OS process
(`tests/crash_safety_writer.py`), lets it commit a few batches, SIGKILLs
it mid-transaction at a precisely-timed point (a deliberate one-second
sleep inside the transaction makes the timing deterministic, not a race),
then reopens the database and asserts `PRAGMA integrity_check` passes,
every previously-committed row is intact and correctly attributed, the
in-flight (killed) transaction leaked nothing, and the database is fully
writable again afterward - not just readable. A second test repeats the
whole cycle twice against the same file to rule out "recovered once by
luck".

## Litestream: WAL -> S3

`app/infra/litestream.yml` (owned by this same track) runs the
`litestream` binary alongside world/ and broker/ on the genesis host,
continuously replicating the WAL file this package writes to S3 under
the `litestream/` prefix `infra/scripts/01-setup-s3-and-iam.sh`
provisions. See that file's own header comment for the required env vars
and why it authenticates via the EC2 instance role rather than a static
credential (the same "static env vars, else IMDSv2" order
`s3_client.py` implements for the Python side).

## The archiver: gzip -> S3

```python
from supabase import get_client, PayloadArchiver, build_llm_call_archive_key

client = get_client()
archiver = PayloadArchiver(client)
result = await archiver.archive_payload(
    key=build_llm_call_archive_key(agent_id=agent_id, tick=tick, llm_call_id=call_id),
    payload={"request": raw_request, "response": raw_response},
)
# result.storage_path -> store this in llm_calls.payload_archive_path
```

Gzips, hashes (sha256 of the *uncompressed* bytes, for a later integrity
check), and uploads through whichever backend `client.upload_archive` is
configured for: **S3** (via `s3_client.S3Client`, no boto3 - see that
module's docstring for why) when `KASUKABE_S3_BUCKET` is set, or a local
`local_storage/<bucket>/<path>` directory next to the sqlite file
otherwise - every test in this repo, and any local-dev run with no S3
configured. `archiver.py`'s two logical bucket names map onto the fixed
S3 prefixes `infra/scripts/01-setup-s3-and-iam.sh` already carved out
(`s3_client.ARCHIVE_BUCKET_PREFIXES`):

| Logical bucket | S3 prefix |
|---|---|
| `llm-payloads` | `archives/payloads/` |
| `world-snapshots` | `archives/checkpoints/` |

`payload_archive_path` always stores the backend-portable
`"<logical-bucket>/<path>"` form regardless of which backend actually
wrote the bytes; the real S3 key is one dict lookup away
(`ARCHIVE_BUCKET_PREFIXES[bucket] + "/" + path`), documented in
`SqliteClient.upload_archive`'s own docstring.

## Per-game-day Parquet export

Lives in `research/parquet_export.py`, not here (`research/` is where
DESIGN.md section 9 puts it) - it reuses this package's `S3Client` for
its own upload, under the `exports/parquet/` prefix
(`s3_client.PARQUET_EXPORT_PREFIX`). See that module's docstring for a
real DuckDB gotcha worth knowing if you touch either file: DuckDB's
sqlite scanner extension does not expose SQLite's `GENERATED ALWAYS AS
... STORED` columns at all, so `*_game_day` columns are invisible to it -
the export job recomputes game-day partitioning from the underlying
`tick` column instead.

## `migrations/` (dormant)

19 Postgres/Supabase DDL files (`extensions` -> 16 tables ->
`realtime_publication` -> `storage_buckets`) from before the
2026-07-30 decision above. Nothing in this repo applies them today -
there is no PostgREST client left to talk to the project they would
create, and `sqlite_schema.sql` (not this directory) is the trace
store's real, live schema. They are kept, not deleted, only because
DESIGN.md section 9's last sentence keeps the door open: "Swap to native
Postgres only at ~1000 agents / multi-host - same client interface." If
that day comes, this is where that schema would come from; until then,
treat every file under `migrations/` as a historical reference, not a
deploy step, and do not point deploy tooling at it.

`sqlite_schema.sql` mirrors the same 16 tables (types translated per
that file's own header: `uuid` -> `TEXT`, `jsonb` -> JSON-encoded `TEXT`,
boolean -> `INTEGER` 0/1, ...) including every `CHECK` constraint and
generated `tick`/`game_day`/`game_minute_of_day` column - the ledger
invariant (`app/CONVENTIONS.md`) is a real `CHECK` constraint here too,
not just an application-level assumption.

## Tests

```
cd app && make test                              # whole app suite
docker compose run --rm test pytest supabase/ research/ -v   # this module + the export job
```

- `test_supabase_config.py` - `SupabaseConfig.from_env()` and
  `SupabaseConfigError` aggregation.
- `test_supabase_sqlite_client.py` - real I/O against a temp-file SQLite
  database: CRUD, the crash-safety pragmas, and S3-routing logic (mocked
  S3 client, real everything else).
- `test_supabase_s3_client.py` - SigV4 signing (cross-checked once
  against botocore's own reference signer while writing the test - see
  that file's docstring), credential resolution (static env vars, IMDSv2
  fallback), and `put_object` against `httpx.MockTransport` - no boto3,
  no real AWS call, anywhere.
- `test_supabase_crash_safety.py` - the real SIGKILL-a-subprocess proof,
  see "Crash safety" above.
- `test_supabase_archiver.py` - gzip + hash + upload against a real
  temp-file SQLite backend.
- `test_supabase_migrations_structure.py` (always runs) and
  `test_supabase_migrations_apply_live.py` (applies every `migrations/`
  file to a real, throwaway Postgres database via `psql`; skips cleanly
  when no local Postgres is reachable) - kept alongside `migrations/`
  itself for the same "dormant, not deleted" reason.
- `test_schema_gen.py` - the drift test: applies `schema_gen.py`'s
  generated `llm_calls`/`events` DDL and `sqlite_schema.sql`'s checked-in
  DDL to two real, separate SQLite connections and diffs `PRAGMA
  table_xinfo` between them (not source text - includes generated
  columns, which `PRAGMA table_info` silently omits on this SQLite
  build, see the test file's own note), plus two real-writer-shape
  insert tests: one row exactly as `broker/trace.py`'s own
  `trace_record_to_row`/`broker_event_to_row` produce it, and one row
  exactly as `world/wiring.py`'s `TraceStore.record_event` produces it,
  into the SAME table, proving the schema serves both real writers.

## The trace-store fix (2026-07-31)

`llm_calls`/`events` used to be hand-maintained here, independently of
`broker/trace.py`'s `TraceRecord`/`BrokerEvent` dataclasses that actually
populate them. They drifted (13 columns vs. 21 fields, different names)
and every write on the 2026-07-31 genesis run failed silently - see
`broker/trace.py`'s own "TRACE-STORE FIX" docstring paragraph for the
full incident. `schema_gen.py` now generates both tables' DDL directly
from those two dataclasses; `sqlite_schema.sql`'s checked-in text is
required to match it exactly, enforced by `test_schema_gen.py`, not by
convention. `repair_live_schema.py` is the tool that rebuilt the
already-damaged live database in place (empty tables only - it refuses
to drop a table that has real rows). `broker/app.py`'s `GET /admin/trace`
and its pre-dispatch halt check are the other half: a batch write
failing is now counted, and three consecutive failures on either writer
stops new dispatch before it happens, not after another silent loss.
