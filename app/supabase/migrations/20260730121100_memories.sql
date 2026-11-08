-- memories: LOG table for each agent's memory stream (observations,
-- reflections, plans), the mechanism this project is inspired by from
-- Generative Agents (arXiv 2304.03442, DESIGN.md intro).
--
-- Dream probes are deliberately excluded from memory_type: DESIGN.md
-- section 10 is explicit that dream probes are "never written to agent
-- memory" (the whole point is measuring the agent without it remembering
-- being asked). Their output belongs in eval_metrics / llm_calls, not
-- here; a dream_probe row in this table would be a research-instrument
-- bug, so the CHECK constraint below makes it impossible to insert one.
create table if not exists public.memories (
    id bigint generated always as identity primary key,
    agent_id uuid not null references public.agents (id),

    memory_type text not null check (memory_type in ('observation', 'reflection', 'plan')),
    content text not null,
    -- Generative-agents-style importance score driving retrieval salience.
    importance smallint not null check (importance between 1 and 10),
    -- Similarity-search embedding for retrieval; nullable because a memory
    -- is written before the embedding job (a separate, batched step) has
    -- necessarily run over it.
    embedding extensions.vector(1536),
    -- Reflections cite the observations/memories they were synthesized
    -- from (generative-agents-style memory chains).
    related_memory_ids bigint[],

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now()
);

create index if not exists idx_memories_agent_tick on public.memories (agent_id, tick);
create index if not exists idx_memories_agent_importance on public.memories (agent_id, importance desc);
create index if not exists idx_memories_type_tick on public.memories (memory_type, tick);
-- ivfflat with a modest starting `lists`; retune (and consider hnsw) once
-- real memory volume per agent is known - this is a correct starting
-- index, not a tuned one, and rebuilding it later is a cheap migration.
create index if not exists idx_memories_embedding_ivfflat on public.memories
    using ivfflat (embedding extensions.vector_cosine_ops) with (lists = 100);

alter table public.memories enable row level security;

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.memories to anon, authenticated;
grant select, insert, update, delete on public.memories to service_role;

create policy memories_select_all on public.memories
    for select
    to anon, authenticated
    using (true);
