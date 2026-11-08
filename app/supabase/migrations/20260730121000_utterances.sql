create table if not exists public.utterances (
    id bigint generated always as identity primary key,

    speaker_agent_id uuid not null references public.agents (id),
    listener_agent_id uuid references public.agents (id),
    conversation_id uuid not null default extensions.gen_random_uuid(),

    location text,
    content text not null,
    is_public boolean not null default true,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    content_tsv tsvector generated always as (to_tsvector('english', content)) stored,

    created_at timestamptz not null default now()
);

create index if not exists idx_utterances_speaker_tick on public.utterances (speaker_agent_id, tick);
create index if not exists idx_utterances_listener_tick on public.utterances (listener_agent_id, tick)
    where listener_agent_id is not null;
create index if not exists idx_utterances_conversation_tick on public.utterances (conversation_id, tick);
create index if not exists idx_utterances_tick on public.utterances (tick);
create index if not exists idx_utterances_content_search on public.utterances using gin (content_tsv);

alter table public.utterances enable row level security;

grant select on public.utterances to anon, authenticated;
grant select, insert, update, delete on public.utterances to service_role;

create policy utterances_select_all on public.utterances
    for select
    to anon, authenticated
    using (true);
