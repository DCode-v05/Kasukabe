create table if not exists public.buildings (
    id uuid primary key default extensions.gen_random_uuid(),
    name text,
    building_type text not null check (building_type in ('genesis', 'freeform')),
    purpose text not null,

    owner_agent_id uuid references public.agents (id),
    owner_business_id uuid references public.businesses (id),

    footprint jsonb not null default '{}'::jsonb,
    rooms jsonb not null default '[]'::jsonb,
    bill_of_materials jsonb,

    construction_status text not null default 'complete'
        check (construction_status in ('permitted', 'under_construction', 'complete', 'condemned')),
    permit_petition_id uuid references public.petitions (id),
    construction_started_tick bigint check (construction_started_tick >= 0),
    construction_completed_tick bigint check (construction_completed_tick >= 0),

    created_tick bigint not null check (created_tick >= 0),
    created_game_day integer generated always as (created_tick / 144) stored,
    created_game_minute_of_day smallint generated always as ((created_tick % 144) * 10) stored,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint buildings_freeform_needs_permit check (
        building_type = 'genesis' or permit_petition_id is not null
    )
);

create index if not exists idx_buildings_type_purpose on public.buildings (building_type, purpose);
create index if not exists idx_buildings_owner_agent on public.buildings (owner_agent_id);
create index if not exists idx_buildings_owner_business on public.buildings (owner_business_id);
create index if not exists idx_buildings_status on public.buildings (construction_status);
create index if not exists idx_buildings_footprint_gin on public.buildings using gin (footprint);

alter table public.buildings enable row level security;

grant select on public.buildings to anon, authenticated;
grant select, insert, update, delete on public.buildings to service_role;

create policy buildings_select_all on public.buildings
    for select
    to anon, authenticated
    using (true);
