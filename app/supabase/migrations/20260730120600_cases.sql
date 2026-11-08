-- cases: STATE table for the justice pipeline (DESIGN.md section 6):
-- complaints desk -> case file -> police investigate -> minister-signed
-- warrant -> fine or jail.
--
-- ground_truth_guilty is the load-bearing column here: "we know ground
-- truth on every crime; the police don't" (DESIGN.md section 6), and the
-- false-conviction rate of the in-world justice system is a flagship
-- machine-psychology metric (DESIGN.md section 10). world/'s tool
-- execution layer must never expose this column to an in-world agent
-- (police, minister, judge equivalent) - it is populated from the actual
-- theft/fight tool-call outcome and read only by research/. The RLS policy
-- below intentionally still exposes it to anon/authenticated: those roles
-- are for game/ and research/, both operator- or research-side consumers,
-- never the agents themselves (agents act through world/'s tool API on the
-- service role, not through direct table reads).
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

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.cases to anon, authenticated;
grant select, insert, update, delete on public.cases to service_role;

create policy cases_select_all on public.cases
    for select
    to anon, authenticated
    using (true);
