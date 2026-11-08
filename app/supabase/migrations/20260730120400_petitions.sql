create table if not exists public.petitions (
    id uuid primary key default extensions.gen_random_uuid(),
    petition_type text not null check (petition_type in ('land_expansion', 'charter_amendment', 'other')),
    filed_by_agent_id uuid not null references public.agents (id),
    title text not null,
    description text,
    payload jsonb not null default '{}'::jsonb,

    status text not null default 'pending' check (status in ('pending', 'approved', 'denied', 'withdrawn')),
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

grant select on public.petitions to anon, authenticated;
grant select, insert, update, delete on public.petitions to service_role;

create policy petitions_select_all on public.petitions
    for select
    to anon, authenticated
    using (true);
