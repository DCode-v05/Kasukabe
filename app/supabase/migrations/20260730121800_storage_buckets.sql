-- Storage buckets for the two kinds of gzipped blob this schema references
-- by path: raw LLM payloads (llm_calls.payload_archive_path,
-- app/supabase/archiver.py) and full world checkpoints
-- (world_snapshots.state_archive_path). Both are private: nothing in
-- DESIGN.md calls for public, anonymous access to raw prompts/responses or
-- full world state, and the game/research read path is the Postgres
-- tables and Realtime, not Storage.
insert into storage.buckets (id, name, public)
values ('llm-payloads', 'llm-payloads', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('world-snapshots', 'world-snapshots', false)
on conflict (id) do nothing;

-- Both buckets are written by broker/ and world/ (service role, bypasses
-- storage RLS same as it bypasses table RLS) and read by research/ for
-- deep-dive analysis of specific calls or checkpoints. No anon/authenticated
-- policy is created on storage.objects for these buckets: unlike the
-- Postgres tables above, raw payloads and full state dumps are not part of
-- the read surface app/CONVENTIONS.md grants to game/ or a broad
-- "research reads Supabase tables" - a researcher fetching one archived
-- payload does so with a service-role-backed job, not an anon key.
