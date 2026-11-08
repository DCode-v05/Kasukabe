-- buildings: STATE table covering both genesis buildings (placed at world
-- generation, DESIGN.md section 1) and freeform builder-constructed
-- buildings (DESIGN.md section 2: "arbitrary footprint on the tile grid,
-- rooms, purpose ... bill of materials auto-computed from geometry").
create table if not exists public.buildings (
    id uuid primary key default extensions.gen_random_uuid(),
    name text,
    building_type text not null check (building_type in ('genesis', 'freeform')),
    -- Free text, not an enum: genesis purposes are a known fixed set
    -- (house, cafe, grocery, pharmacy, park, college, office, government,
    -- temple - DESIGN.md section 1) but freeform builders choose arbitrary
    -- purposes by design (section 2), so constraining this column would
    -- contradict the feature it is meant to support.
    purpose text not null,

    owner_agent_id uuid references public.agents (id),
    owner_business_id uuid references public.businesses (id),

    -- Tile-grid geometry and room layout for freeform builds; genesis
    -- buildings populate the same columns from the Tiled map at import
    -- time so both kinds are queryable uniformly.
    footprint jsonb not null default '{}'::jsonb,
    rooms jsonb not null default '[]'::jsonb,
    -- Timber/metal per tile, auto-computed from footprint geometry
    -- (DESIGN.md section 2). Null for genesis buildings, which did not go
    -- through the construction pipeline.
    bill_of_materials jsonb,

    construction_status text not null default 'complete'
        check (construction_status in ('permitted', 'under_construction', 'complete', 'condemned')),
    -- Freeform building requires plot rights and a government permit
    -- (DESIGN.md section 2). Permits are filed as petitions; nullable
    -- because genesis buildings never went through a permit process.
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
-- Freeform footprints are the geometry research and the game renderer both
-- query by containment (e.g. "which building owns this tile"); a GIN index
-- on the jsonb makes that a cheap query instead of a full scan.
create index if not exists idx_buildings_footprint_gin on public.buildings using gin (footprint);

alter table public.buildings enable row level security;

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.buildings to anon, authenticated;
grant select, insert, update, delete on public.buildings to service_role;

create policy buildings_select_all on public.buildings
    for select
    to anon, authenticated
    using (true);
