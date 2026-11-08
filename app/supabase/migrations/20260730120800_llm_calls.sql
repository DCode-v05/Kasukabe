create table if not exists public.llm_calls (
    id bigint generated always as identity primary key,
    agent_id uuid not null references public.agents (id),

    model text not null,
    call_type text not null check (call_type in ('agent_turn', 'dream_probe', 'learning_centre')),

    request_tokens integer not null check (request_tokens >= 0),
    response_tokens integer not null check (response_tokens >= 0),
    cost_usd_cents bigint not null check (cost_usd_cents >= 0),

    payload_archive_path text,
    payload_archived_at timestamptz,

    succeeded boolean not null default true,
    error_message text,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now()
);

create index if not exists idx_llm_calls_agent_tick on public.llm_calls (agent_id, tick);
create index if not exists idx_llm_calls_model_tick on public.llm_calls (model, tick);
create index if not exists idx_llm_calls_call_type_tick on public.llm_calls (call_type, tick);
create index if not exists idx_llm_calls_unarchived on public.llm_calls (id) where payload_archive_path is null;

alter table public.llm_calls enable row level security;

grant select on public.llm_calls to anon, authenticated;
grant select, insert, update, delete on public.llm_calls to service_role;

create policy llm_calls_select_all on public.llm_calls
    for select
    to anon, authenticated
    using (true);
