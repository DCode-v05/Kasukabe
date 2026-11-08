-- Realtime publication: game/ subscribes to `events` and `utterances` so
-- the live town view and the agent inspector's "live conversation" feed
-- (DESIGN.md section 8) update without polling world's HTTP API. Per
-- app/CONVENTIONS.md, game/ "reads live state from Supabase Realtime, not
-- by polling world's internals" - this publication is what makes that
-- true at the database layer.
--
-- Deliberately just these two tables. Every other table in this schema is
-- either low-frequency enough for a normal read (agents, businesses,
-- petitions, ...) or research-only (llm_calls, eval_metrics); adding a
-- table to this publication is a live decision for whichever module needs
-- it, made in its own migration, not a default applied here.
do $$
begin
    -- A fresh self-hosted Postgres may not have the supabase_realtime
    -- publication yet; Supabase-hosted projects provision it automatically.
    -- Guarding with an existence check keeps this migration idempotent
    -- across both.
    if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        create publication supabase_realtime;
    end if;
end $$;

alter publication supabase_realtime add table public.events;
alter publication supabase_realtime add table public.utterances;

-- Both tables are insert-only from every writer in this schema (world/ and
-- broker/ never UPDATE or DELETE an event or utterance row), so the
-- default replica identity (primary key) is sufficient: Realtime's INSERT
-- payload only ever needs the new row, which it always has. REPLICA
-- IDENTITY FULL (needed to see old values on UPDATE/DELETE) is not set
-- because there is no old value these tables ever produce.
