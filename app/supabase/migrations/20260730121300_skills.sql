create table if not exists public.skills (
    id bigint generated always as identity primary key,
    agent_id uuid not null references public.agents (id),

    task_type text not null,
    proficiency_level numeric(5, 2) not null check (proficiency_level >= 0),
    repetitions_observed integer not null default 0 check (repetitions_observed >= 0),

    skill_artifact_id uuid,
    skill_artifact_text text,
    taught_by_agent_id uuid references public.agents (id),

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now()
);

create index if not exists idx_skills_agent_task_tick on public.skills (agent_id, task_type, tick);
create index if not exists idx_skills_task_tick on public.skills (task_type, tick);
create index if not exists idx_skills_taught_by on public.skills (taught_by_agent_id)
    where taught_by_agent_id is not null;

alter table public.skills enable row level security;

grant select on public.skills to anon, authenticated;
grant select, insert, update, delete on public.skills to service_role;

create policy skills_select_all on public.skills
    for select
    to anon, authenticated
    using (true);
