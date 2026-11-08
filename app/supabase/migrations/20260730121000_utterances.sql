-- utterances: LOG table for agent speech. Conversation never moves matter
-- on its own (DESIGN.md section 2: "world state mutates ONLY through tool
-- calls"), but it is the substrate negotiation, gossip, promise-keeping,
-- and doctrine formation happen in (DESIGN.md sections 3, 7, 10), so it is
-- traced with the same rigor as any tool call.
create table if not exists public.utterances (
    id bigint generated always as identity primary key,

    speaker_agent_id uuid not null references public.agents (id),
    -- Null for a broadcast, a soliloquy, or an unaddressed thought; not
    -- every utterance has exactly one listener.
    listener_agent_id uuid references public.agents (id),
    -- Groups a back-and-forth exchange into one thread, e.g. for the agent
    -- inspector's "live conversation" view (DESIGN.md section 8).
    conversation_id uuid not null default extensions.gen_random_uuid(),

    -- Free text tile/building identifier: tools are location-gated
    -- (DESIGN.md section 2), so where an utterance happened determines who
    -- could plausibly have overheard it.
    location text,
    content text not null,
    -- Whether nearby agents could witness this utterance, relevant to
    -- theft witnessing (DESIGN.md section 6: "~30% witness memory
    -- generated") and gossip diffusion (DESIGN.md section 10).
    is_public boolean not null default true,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    -- Full-text search over dialogue content backs the sociology track's
    -- promise-keeping and norm-emergence queries ("did anyone say X") and
    -- the religion track's doctrine-genesis search, without a separate
    -- search index table.
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

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.utterances to anon, authenticated;
grant select, insert, update, delete on public.utterances to service_role;

create policy utterances_select_all on public.utterances
    for select
    to anon, authenticated
    using (true);

-- Realtime publication membership: 20260730121700_realtime_publication.sql.
