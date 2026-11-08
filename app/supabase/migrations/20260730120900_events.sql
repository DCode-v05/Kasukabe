-- events: the canonical causal record (DESIGN.md section 2: "the Supabase
-- trace is a complete causal record of the world"). Every mutation any
-- module makes goes through world's tool registry, and by the time a tool
-- call has happened it is history, hence the past-tense verb rule in
-- app/CONVENTIONS.md's event naming scheme.
create table if not exists public.events (
    id bigint generated always as identity primary key,
    -- '<domain>.<subject>.<past_tense_verb>', all lowercase, dot-separated,
    -- snake_case within each segment (app/CONVENTIONS.md "Event naming
    -- scheme"). Enforced here, not just documented, so a malformed type
    -- fails at insert instead of silently breaking per-domain Realtime
    -- routing later.
    --
    -- The scheme's own example table uses a 2-segment shorthand
    -- (domain.verb) for four events - agent.moved, agent.spawned,
    -- agent.died, business.registered - where the subject would otherwise
    -- just repeat the domain, so this constraint accepts 2 or 3 segments.
    -- Verified against every example in that table while live-testing this
    -- migration; a strict 3-segment-only regex rejects real events from
    -- CONVENTIONS.md's own reference list.
    type text not null check (type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$'),
    -- Derived, not app-supplied: the Realtime channel name
    -- ("kasukabe:<domain>", app/CONVENTIONS.md) is always type's first dot
    -- segment, so deriving it in the database means a channel router can
    -- filter on `domain` directly instead of parsing `type` on every event.
    domain text generated always as (split_part(type, '.', 1)) stored,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    -- Nullable: not every event is agent-scoped (world.tick.advanced,
    -- system.checkpoint.written have no single agent to attach to).
    agent_id uuid references public.agents (id),
    payload jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now()
);

-- Per-type event scans and per-agent timelines are both named explicitly
-- in the task brief; these two indexes are the direct answer to each.
create index if not exists idx_events_type_tick on public.events (type, tick);
create index if not exists idx_events_agent_tick on public.events (agent_id, tick) where agent_id is not null;
-- Backs the Realtime channel-per-domain model and any backfill/replay a
-- new game/ client does on (re)connect.
create index if not exists idx_events_domain_tick on public.events (domain, tick);
create index if not exists idx_events_payload_gin on public.events using gin (payload);

alter table public.events enable row level security;

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.events to anon, authenticated;
grant select, insert, update, delete on public.events to service_role;

create policy events_select_all on public.events
    for select
    to anon, authenticated
    using (true);

-- Realtime publication membership for this table is set up in
-- 20260730121700_realtime_publication.sql, once utterances (the other
-- table game/ subscribes to) also exists.
