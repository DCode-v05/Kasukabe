-- tools_registry: STATE table for the meta-tool system (DESIGN.md section
-- 2): agents may define new tools as compositions of existing primitives
-- only, registered at the government patent office, and namespaced into
-- public / government / business / organization with different ACLs.
--
-- The government namespace is deliberately left without an owner column
-- here: "confidential by default, visible/callable ONLY by sitting
-- incumbents, access transfers atomically on election". Who currently
-- holds an office is a political fact that changes on a 3-game-day
-- election cycle (DESIGN.md section 7) and belongs to whatever table
-- world/'s charter module uses to track officeholders, not to a static
-- owner column on the tool itself; resolving "is this caller a sitting
-- incumbent" is world/'s job at call time, not this schema's.
create table if not exists public.tools_registry (
    id uuid primary key default extensions.gen_random_uuid(),
    name text not null,
    namespace text not null check (namespace in ('public', 'government', 'business', 'organization')),

    owner_agent_id uuid references public.agents (id),
    owner_business_id uuid references public.businesses (id),
    owner_organization_id uuid references public.organizations (id),

    -- Ordered list of primitive tool calls this tool composes. "No new
    -- physics" (DESIGN.md section 2): every step here must resolve to an
    -- existing primitive, enforced by world/'s patent office review, not
    -- by this table.
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

    -- A business-namespace tool has a business owner and no organization
    -- owner, and vice versa; public and government namespace tools carry
    -- neither (see the comment above for why government has no owner
    -- column). This mirrors the namespace/ACL rules in DESIGN.md section 2
    -- as a physical constraint instead of leaving it to application code
    -- to get right on every insert.
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

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.tools_registry to anon, authenticated;
grant select, insert, update, delete on public.tools_registry to service_role;

-- Namespace confidentiality is an in-world rule enforced by world/'s tool
-- execution layer (non-members cannot see a confidential tool exists,
-- DESIGN.md section 2), not by Postgres RLS: research and the operator
-- console need to see every tool, confidential or not, to audit and to
-- render the President/God console. Restricting this table's visibility
-- at the database layer would break that audit guarantee.
create policy tools_registry_select_all on public.tools_registry
    for select
    to anon, authenticated
    using (true);
