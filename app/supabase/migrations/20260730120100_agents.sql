create table if not exists public.agents (
    id uuid primary key default extensions.gen_random_uuid(),
    slug text not null,
    display_name text not null,
    sex text not null check (sex in ('male', 'female')),

    birth_tick bigint not null check (birth_tick >= 0),
    birth_game_day integer generated always as (birth_tick / 144) stored,
    birth_game_minute_of_day smallint generated always as ((birth_tick % 144) * 10) stored,
    is_minor boolean not null default false,

    household_id uuid,
    parent_agent_id uuid references public.agents (id),

    usd_wallet_cents bigint not null default 200 check (usd_wallet_cents >= 0),
    sim_balance bigint not null default 0 check (sim_balance >= 0),

    is_dormant boolean not null default false,
    dormant_since_tick bigint check (dormant_since_tick >= 0),

    occupation text,
    employer_business_id uuid,

    health_status text not null default 'healthy'
        check (health_status in ('healthy', 'sick_short', 'sick_medium', 'sick_long', 'terminal', 'deceased')),
    is_alive boolean not null default true,
    died_tick bigint check (died_tick >= 0),
    death_cause text
        check (death_cause is null or death_cause in (
            'terminal_illness', 'untreated_chronic', 'elderly_hazard', 'cognitive_poverty'
        )),

    created_tick bigint not null check (created_tick >= 0),
    created_game_day integer generated always as (created_tick / 144) stored,
    created_game_minute_of_day smallint generated always as ((created_tick % 144) * 10) stored,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint agents_slug_unique unique (slug),
    constraint agents_death_fields_consistent check (
        (is_alive and died_tick is null and death_cause is null)
        or (not is_alive and died_tick is not null and death_cause is not null)
    )
);

create index if not exists idx_agents_household on public.agents (household_id);
create index if not exists idx_agents_parent on public.agents (parent_agent_id);
create index if not exists idx_agents_employer on public.agents (employer_business_id);
create index if not exists idx_agents_alive on public.agents (id) where is_alive;
create index if not exists idx_agents_dormant on public.agents (dormant_since_tick) where is_dormant;

alter table public.agents enable row level security;

grant select on public.agents to anon, authenticated;
grant select, insert, update, delete on public.agents to service_role;

create policy agents_select_all on public.agents
    for select
    to anon, authenticated
    using (true);
