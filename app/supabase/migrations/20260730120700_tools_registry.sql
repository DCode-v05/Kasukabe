create table if not exists public.tools_registry (
    id uuid primary key default extensions.gen_random_uuid(),
    name text not null,
    namespace text not null check (namespace in ('public', 'government', 'business', 'organization')),

    owner_agent_id uuid references public.agents (id),
    owner_business_id uuid references public.businesses (id),
    owner_organization_id uuid references public.organizations (id),

    composition jsonb not null,
    patent_fee_paid_sim bigint check (patent_fee_paid_sim >= 0),
    status text not null default 'pending_review' check (status in ('pending_review', 'active', 'revoked')),
    is_licensable boolean not null default false,
    is_teachable boolean not null default false,

    registered_tick bigint not null check (registered_tick >= 0),
    registered_game_day integer generated always as (registered_tick / 144) stored,
    registered_game_minute_of_day smallint generated always as ((registered_tick % 144) * 10) stored,
    revoked_tick bigint check (revoked_tick >= 0),

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint tools_registry_namespace_owner_shape check (
        (namespace = 'public' and owner_business_id is null and owner_organization_id is null)
        or (namespace = 'government' and owner_business_id is null and owner_organization_id is null)
        or (namespace = 'business' and owner_business_id is not null and owner_organization_id is null)
        or (namespace = 'organization' and owner_organization_id is not null and owner_business_id is null)
    ),
    constraint tools_registry_revoked_fields_consistent check (
        (status = 'revoked') = (revoked_tick is not null)
    )
);

create index if not exists idx_tools_registry_namespace_status on public.tools_registry (namespace, status);
create index if not exists idx_tools_registry_owner_agent on public.tools_registry (owner_agent_id);
create index if not exists idx_tools_registry_owner_business on public.tools_registry (owner_business_id);
create index if not exists idx_tools_registry_owner_org on public.tools_registry (owner_organization_id);
create index if not exists idx_tools_registry_name on public.tools_registry (name);

alter table public.tools_registry enable row level security;

grant select on public.tools_registry to anon, authenticated;
grant select, insert, update, delete on public.tools_registry to service_role;

create policy tools_registry_select_all on public.tools_registry
    for select
    to anon, authenticated
    using (true);
