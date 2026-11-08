create table if not exists public.memories (
    id bigint generated always as identity primary key,
    agent_id uuid not null references public.agents (id),

    memory_type text not null check (memory_type in ('observation', 'reflection', 'plan')),
    content text not null,
    importance smallint not null check (importance between 1 and 10),
    embedding extensions.vector(1536),
    related_memory_ids bigint[],

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now()
);

create index if not exists idx_memories_agent_tick on public.memories (agent_id, tick);
create index if not exists idx_memories_agent_importance on public.memories (agent_id, importance desc);
create index if not exists idx_memories_type_tick on public.memories (memory_type, tick);
create index if not exists idx_memories_embedding_ivfflat on public.memories
    using ivfflat (embedding extensions.vector_cosine_ops) with (lists = 100);

alter table public.memories enable row level security;

grant select on public.memories to anon, authenticated;
grant select, insert, update, delete on public.memories to service_role;

create policy memories_select_all on public.memories
    for select
    to anon, authenticated
    using (true);
