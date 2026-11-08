create table if not exists public.cases (
    id uuid primary key default extensions.gen_random_uuid(),
    case_type text not null check (case_type in ('theft', 'fight', 'breach_of_contract', 'other')),
    complainant_agent_id uuid references public.agents (id),
    accused_agent_id uuid references public.agents (id),

    status text not null default 'open'
        check (status in ('open', 'investigating', 'warrant_issued', 'fined', 'jailed', 'dismissed', 'closed')),
    investigating_officer_agent_id uuid references public.agents (id),
    warrant_issued_tick bigint check (warrant_issued_tick >= 0),
    warrant_signed_by_agent_id uuid references public.agents (id),
    verdict text check (verdict is null or verdict in ('guilty', 'not_guilty')),
    ground_truth_guilty boolean,

    fine_amount_sim bigint check (fine_amount_sim >= 0),
    jail_ticks bigint check (jail_ticks >= 0),
    resolved_tick bigint check (resolved_tick >= 0),

    filed_tick bigint not null check (filed_tick >= 0),
    filed_game_day integer generated always as (filed_tick / 144) stored,
    filed_game_minute_of_day smallint generated always as ((filed_tick % 144) * 10) stored,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_cases_status_tick on public.cases (status, filed_tick);
create index if not exists idx_cases_accused_tick on public.cases (accused_agent_id, filed_tick);
create index if not exists idx_cases_complainant_tick on public.cases (complainant_agent_id, filed_tick);
create index if not exists idx_cases_type_tick on public.cases (case_type, filed_tick);
create index if not exists idx_cases_officer on public.cases (investigating_officer_agent_id);

alter table public.cases enable row level security;

grant select on public.cases to anon, authenticated;
grant select, insert, update, delete on public.cases to service_role;

create policy cases_select_all on public.cases
    for select
    to anon, authenticated
    using (true);
