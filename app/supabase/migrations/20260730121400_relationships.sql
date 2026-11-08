-- relationships: LOG table of relationship-state changes, an append-only
-- edge log rather than a single current-state row per pair so the
-- sociology track's "network topology evolution" and the political
-- science track's "cult centralization" metrics (DESIGN.md section 10)
-- can be computed as a time series, not just read off the latest state.
--
-- The edge target is polymorphic on purpose: an agent's social ties
-- (family, friend, spouse, rival) and an agent's affiliations
-- (organization membership/followership) are the same shape of fact,
-- "this agent's standing with that other thing changed at this tick", and
-- DESIGN.md's table list has no separate membership table. other_agent_id
-- and organization_id are mutually exclusive; exactly one is set per row.
create table if not exists public.relationships (
    id bigint generated always as identity primary key,
    agent_id uuid not null references public.agents (id),
    other_agent_id uuid references public.agents (id),
    organization_id uuid references public.organizations (id),

    -- Free text, not an enum: relationship structure is meant to be
    -- emergent ("nobody tells them what to do", DESIGN.md intro), so this
    -- schema does not get to pre-declare the vocabulary agents converge on
    -- (family/friend/spouse/rival/membership/... are examples, not a
    -- closed list).
    relationship_type text not null,
    sentiment numeric(4, 3) check (sentiment is null or sentiment between -1 and 1),
    trust numeric(4, 3) check (trust is null or trust between 0 and 1),
    interaction_count integer not null default 1 check (interaction_count >= 0),
    notes text,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now(),

    constraint relationships_target_is_exclusive check (
        (other_agent_id is not null) <> (organization_id is not null)
    ),
    constraint relationships_not_self check (agent_id <> other_agent_id)
);

create index if not exists idx_relationships_agent_tick on public.relationships (agent_id, tick);
create index if not exists idx_relationships_other_agent_tick on public.relationships (other_agent_id, tick)
    where other_agent_id is not null;
create index if not exists idx_relationships_org_tick on public.relationships (organization_id, tick)
    where organization_id is not null;
create index if not exists idx_relationships_type_tick on public.relationships (relationship_type, tick);

alter table public.relationships enable row level security;

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.relationships to anon, authenticated;
grant select, insert, update, delete on public.relationships to service_role;

create policy relationships_select_all on public.relationships
    for select
    to anon, authenticated
    using (true);
