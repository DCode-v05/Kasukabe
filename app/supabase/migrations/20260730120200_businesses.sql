create table if not exists public.businesses (
    id uuid primary key default extensions.gen_random_uuid(),
    name text not null,
    business_type text not null,
    owner_agent_id uuid not null references public.agents (id),
    is_control_business boolean not null default false,

    status text not null default 'active' check (status in ('active', 'closed', 'bankrupt')),
    closed_tick bigint check (closed_tick >= 0),

    treasury_sim_balance bigint not null default 0 check (treasury_sim_balance >= 0),
    tax_rate_bps integer not null default 0 check (tax_rate_bps between 0 and 10000),

    registered_tick bigint not null check (registered_tick >= 0),
    registered_game_day integer generated always as (registered_tick / 144) stored,
    registered_game_minute_of_day smallint generated always as ((registered_tick % 144) * 10) stored,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint businesses_name_unique unique (name),
    constraint businesses_closed_fields_consistent check (
        (status = 'active' and closed_tick is null)
        or (status <> 'active' and closed_tick is not null)
    )
);

alter table public.agents
    add constraint agents_employer_business_id_fkey
    foreign key (employer_business_id) references public.businesses (id);

create index if not exists idx_businesses_owner on public.businesses (owner_agent_id);
create index if not exists idx_businesses_type_tick on public.businesses (business_type, registered_tick);
create index if not exists idx_businesses_active on public.businesses (id) where status = 'active';
create index if not exists idx_businesses_control on public.businesses (id) where is_control_business;

alter table public.businesses enable row level security;

grant select on public.businesses to anon, authenticated;
grant select, insert, update, delete on public.businesses to service_role;

create policy businesses_select_all on public.businesses
    for select
    to anon, authenticated
    using (true);
