-- organizations: STATE table for parties, cults, societies, and religions
-- (DESIGN.md section 7: "Organizations first-class ... registered charters
-- or underground"). This table holds every organization regardless of
-- in-world legality: unregistered coordination is itself research data
-- ("researchers see all", DESIGN.md section 2), so is_registered records
-- the in-world legal status, it does not gate whether the row exists.
create table if not exists public.organizations (
    id uuid primary key default extensions.gen_random_uuid(),
    name text not null,
    org_type text not null check (org_type in ('party', 'cult', 'society', 'religion', 'guild', 'other')),
    is_registered boolean not null default true,
    founder_agent_id uuid not null references public.agents (id),
    charter_text text,

    status text not null default 'active' check (status in ('active', 'dissolved')),
    dissolved_tick bigint check (dissolved_tick >= 0),

    founded_tick bigint not null check (founded_tick >= 0),
    founded_game_day integer generated always as (founded_tick / 144) stored,
    founded_game_minute_of_day smallint generated always as ((founded_tick % 144) * 10) stored,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint organizations_name_unique unique (name),
    constraint organizations_dissolved_fields_consistent check (
        (status = 'active' and dissolved_tick is null)
        or (status = 'dissolved' and dissolved_tick is not null)
    )
);

create index if not exists idx_organizations_type_tick on public.organizations (org_type, founded_tick);
create index if not exists idx_organizations_founder on public.organizations (founder_agent_id);
create index if not exists idx_organizations_active on public.organizations (id) where status = 'active';

alter table public.organizations enable row level security;

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.organizations to anon, authenticated;
grant select, insert, update, delete on public.organizations to service_role;

create policy organizations_select_all on public.organizations
    for select
    to anon, authenticated
    using (true);
