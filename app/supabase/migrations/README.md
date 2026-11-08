# Dormant — nothing applies these

**INFRA-22.** These are Postgres/Supabase migrations for a hosted database
this project does not use. `DESIGN.md` section 9 makes the trace store
**SQLite**, `broker/app.py` builds a `SqliteClient` on both paths, and the
schema those clients use lives in `supabase/sqlite_schema.sql` and is
applied additively by `SqliteClient.ensure_schema()` when the file is
opened.

So: no code path reads this directory, and
`supabase/tests/test_supabase_migrations_apply_live.py` — the only thing
that touches it — needs a live Postgres and `createdb` on PATH, so it
errors or skips everywhere including CI.

**Do not add migrations here for a schema change.** Change
`sqlite_schema.sql` and let `ensure_schema` apply it. Three migrations were
written here after the project had already moved, for a database nothing
would run them against.

Kept rather than deleted because they are the record of the hosted schema
if that path is ever revived, and because the `supabase/` package's own
client contract is still tested against them.
