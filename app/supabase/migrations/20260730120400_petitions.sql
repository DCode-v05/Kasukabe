-- petitions: STATE table (one row per filed petition, status evolves in
-- place) for the `petition` world primitive (DESIGN.md section 2). The
-- flagship case is land expansion to the President (DESIGN.md section 1:
-- "government petitions the President ... app appends tiles to the Tiled
-- JSON"); petition_type is extensible to other petition-shaped decisions
-- such as charter amendments (DESIGN.md section 7) without a new table.
create table if not exists public.petitions (
    id uuid primary key default extensions.gen_random_uuid(),
    petition_type text not null check (petition_type in ('land_expansion', 'charter_amendment', 'other')),
    filed_by_agent_id uuid not null references public.agents (id),
    title text not null,
    description text,
    -- Type-specific structured content, e.g. {"tiles": [...]} for a land
    -- expansion petition's requested geometry. Kept generic so this table
    -- does not need a new column, or a new migration, per petition_type.
    payload jsonb not null default '{}'::jsonb,

    status text not null default 'pending' check (status in ('pending', 'approved', 'denied', 'withdrawn')),
    -- The President is the human operator wearing a hat (DESIGN.md section
    -- 8: "One console, two hats ... over the live game view"), not a
    -- simulated agent, so the decision-maker is not an agents(id) foreign
    -- key. Free text keeps this table honest about that: 'operator' today,
    -- room for a named title if the console ever supports more than one
    -- operator seat.
    decided_by text,
    decided_tick bigint check (decided_tick >= 0),
    decision_note text,

    filed_tick bigint not null check (filed_tick >= 0),
    filed_game_day integer generated always as (filed_tick / 144) stored,
    filed_game_minute_of_day smallint generated always as ((filed_tick % 144) * 10) stored,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint petitions_decision_fields_consistent check (
        (status = 'pending' and decided_tick is null)
        or (status <> 'pending' and decided_tick is not null)
    )
);

create index if not exists idx_petitions_status_tick on public.petitions (status, filed_tick);
create index if not exists idx_petitions_type_tick on public.petitions (petition_type, filed_tick);
create index if not exists idx_petitions_filed_by on public.petitions (filed_by_agent_id);
create index if not exists idx_petitions_pending on public.petitions (filed_tick) where status = 'pending';

alter table public.petitions enable row level security;

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.petitions to anon, authenticated;
grant select, insert, update, delete on public.petitions to service_role;

create policy petitions_select_all on public.petitions
    for select
    to anon, authenticated
    using (true);
