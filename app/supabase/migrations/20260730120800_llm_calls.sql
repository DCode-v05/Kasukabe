-- llm_calls: LOG table, one row per broker-metered inference call
-- (DESIGN.md section 9). Structured metadata lives here; the raw request
-- and response payload is gzipped and uploaded to Storage by the
-- payload-archiver (app/supabase/archiver.py), with payload_archive_path
-- pointing at it - keeping multi-KB prompt/response bodies out of Postgres
-- while the metering data researchers actually query stays fast to scan.
create table if not exists public.llm_calls (
    id bigint generated always as identity primary key,
    agent_id uuid not null references public.agents (id),

    -- Not constrained to an enum: broker/ owns the allowlist
    -- (app/CONVENTIONS.md: "broker owns ALL Bedrock access ... the
    -- {kimi-k2.5, deepseek-v3.2} allowlist ... live [there]"). Duplicating
    -- that list as a CHECK constraint here would make every model catalog
    -- change a schema migration; broker rejects out-of-allowlist calls
    -- before they ever reach this table, so this column just needs to be
    -- present and indexable, not authoritative.
    model text not null,
    -- What the call was for: an in-tick agent turn, a sleep-tick dream
    -- probe (never written to `memories`, DESIGN.md section 10), or a
    -- learning-centre batch run (DESIGN.md section 4).
    call_type text not null check (call_type in ('agent_turn', 'dream_probe', 'learning_centre')),

    request_tokens integer not null check (request_tokens >= 0),
    response_tokens integer not null check (response_tokens >= 0),
    -- Integer cents actually burned for this call (app/CONVENTIONS.md's
    -- money rule). At $0.36-$1.80 per 1M tokens a single small call can be
    -- a fraction of a cent; broker's ledger is responsible for accumulating
    -- that sub-cent remainder so the cents it commits to `transactions`
    -- stay exact against the $1000 invariant. request_tokens/response_tokens
    -- above let research recompute the un-rounded theoretical cost from the
    -- model's published rate when a call-level fractional figure matters.
    cost_usd_cents bigint not null check (cost_usd_cents >= 0),

    payload_archive_path text,
    payload_archived_at timestamptz,

    succeeded boolean not null default true,
    error_message text,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now()
);

-- Per-agent timelines (explicitly required: "indexes for the research
-- queries: per-agent timelines, per-type event scans").
create index if not exists idx_llm_calls_agent_tick on public.llm_calls (agent_id, tick);
create index if not exists idx_llm_calls_model_tick on public.llm_calls (model, tick);
create index if not exists idx_llm_calls_call_type_tick on public.llm_calls (call_type, tick);
-- The payload-archiver's own working set: "which calls still need
-- gzip+upload". A partial index keeps that scan proportional to the
-- backlog, not the full table, once most rows are archived.
create index if not exists idx_llm_calls_unarchived on public.llm_calls (id) where payload_archive_path is null;

alter table public.llm_calls enable row level security;

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.llm_calls to anon, authenticated;
grant select, insert, update, delete on public.llm_calls to service_role;

create policy llm_calls_select_all on public.llm_calls
    for select
    to anon, authenticated
    using (true);
