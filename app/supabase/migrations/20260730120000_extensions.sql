-- Extensions used across the Kasukabe schema (DESIGN.md section 9: Supabase
-- ap-south-1 is the system of record). Kept in their own migration, applied
-- first, so every later migration can assume these functions exist instead
-- of each re-declaring "create extension if not exists" defensively.
--
-- A hosted Supabase project pre-creates the `extensions` schema; a bare
-- self-hosted Postgres (including the one this migration set is
-- smoke-tested against) does not, so this migration creates it itself
-- instead of assuming the platform already did.
create schema if not exists extensions;

-- pgcrypto: gen_random_uuid() for every entity table's primary key. Postgres
-- 13+ ships a core gen_random_uuid() too, but Supabase's own project
-- template still provisions pgcrypto, and declaring it here keeps this
-- schema portable to a self-hosted Postgres that predates the core function.
create extension if not exists pgcrypto with schema extensions;

-- vector (pgvector): similarity search over memories.embedding, the
-- retrieval mechanism the Generative Agents design this project is
-- inspired by (DESIGN.md intro) depends on for reflection and recall.
-- Supabase ships pgvector as a first-class supported extension.
create extension if not exists vector with schema extensions;
