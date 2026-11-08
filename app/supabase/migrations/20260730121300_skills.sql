-- skills: LOG table of proficiency snapshots (DESIGN.md section 4:
-- "repetition -> proficiency per task type ... skill = cheaper cognition:
-- fewer tokens, better output, higher success"). Append-only rather than a
-- single current-state row per (agent, task_type) so the learning-driven
-- cost-of-living curve is itself a queryable time series, not just a
-- final number.
create table if not exists public.skills (
    id bigint generated always as identity primary key,
    agent_id uuid not null references public.agents (id),

    -- Free text, not an enum: task types are whatever the world's tool
    -- registry ends up defining (farming.harvest_wheat,
    -- crafting.bake_bread, ...), and skills must not need a schema change
    -- every time world/ adds a recipe.
    task_type text not null,
    proficiency_level numeric(5, 2) not null check (proficiency_level >= 0),
    repetitions_observed integer not null default 0 check (repetitions_observed >= 0),

    -- The learning centre authors distilled-procedure skill artifacts that
    -- load on matching tasks (DESIGN.md section 4); inlined here rather
    -- than in a separate artifacts table since a skill row already is
    -- "this agent, this task type, this level, this artifact" as one fact.
    skill_artifact_id uuid,
    skill_artifact_text text,
    -- Set when this proficiency came from the `teach` tool rather than
    -- repetition: co-located, consensual, transfers a copy at reduced
    -- proficiency (DESIGN.md section 4).
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

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.skills to anon, authenticated;
grant select, insert, update, delete on public.skills to service_role;

create policy skills_select_all on public.skills
    for select
    to anon, authenticated
    using (true);
