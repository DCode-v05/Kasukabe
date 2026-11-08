create table if not exists public.events (
    id bigint generated always as identity primary key,
    type text not null check (type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$'),
    domain text generated always as (split_part(type, '.', 1)) stored,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    agent_id uuid references public.agents (id),
    payload jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now()
);

create index if not exists idx_events_type_tick on public.events (type, tick);
create index if not exists idx_events_agent_tick on public.events (agent_id, tick) where agent_id is not null;
create index if not exists idx_events_domain_tick on public.events (domain, tick);
create index if not exists idx_events_payload_gin on public.events using gin (payload);

alter table public.events enable row level security;

grant select on public.events to anon, authenticated;
grant select, insert, update, delete on public.events to service_role;

create policy events_select_all on public.events
    for select
    to anon, authenticated
    using (true);
