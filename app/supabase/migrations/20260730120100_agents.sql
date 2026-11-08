-- agents: one row per engine-backed resident of Kasukabe (DESIGN.md
-- section 4). This is a STATE table (current snapshot, updated in place),
-- not a log: the causal history of how an agent got here lives in
-- `events`; this row is where world/ (the single writer) keeps what is
-- true about the agent right now.
--
-- Every domain table in this schema carries its primary tick alongside two
-- generated columns, game_day and game_minute_of_day, derived from it
-- (DESIGN.md section 1: tick = 10 game-minutes, 144 ticks/game-day). They
-- are STORED generated columns, not app-computed, so tick and its
-- human-readable game-time projection can never drift apart.
create table if not exists public.agents (
    id uuid primary key default extensions.gen_random_uuid(),
    -- Human-readable, stable identifier used in logs, the game UI, and by
    -- researchers; separate from `id` so world/ can assign a readable slug
    -- ("agent-014") without it doubling as the join key.
    slug text not null,
    display_name text not null,
    sex text not null check (sex in ('male', 'female')),

    -- Spawn time. birth_tick is the age clock: minors age 1 year per
    -- game-hour (DESIGN.md section 4), so age is always derived from
    -- (current_tick - birth_tick), never stored, to avoid a second clock
    -- that can fall out of sync.
    birth_tick bigint not null check (birth_tick >= 0),
    birth_game_day integer generated always as (birth_tick / 144) stored,
    birth_game_minute_of_day smallint generated always as ((birth_tick % 144) * 10) stored,
    is_minor boolean not null default false,

    -- Grouping key for pre-linked households/families at genesis (DESIGN.md
    -- section 4). Deliberately not a foreign key to another table: this
    -- schema has no dedicated households table, a shared household_id is
    -- enough for the household to be queryable as a unit.
    household_id uuid,
    -- Primary financial guardian: broker's parent-debit rule for a minor's
    -- inference calls (DESIGN.md section 4) needs exactly one payer. Wider
    -- family structure (both parents, siblings, extended family) belongs in
    -- `relationships`, which supports many edges per agent; this column
    -- answers one narrow question broker asks on every call a minor makes.
    parent_agent_id uuid references public.agents (id),

    -- Dual currency (DESIGN.md section 3). usd_wallet_cents is the API
    -- budget wallet feeding the $1000 system invariant (app/CONVENTIONS.md:
    -- "Money is always an integer count of cents ... never a float").
    -- sim_balance is in-world trade currency and is NOT part of that
    -- invariant; it has its own emergent supply via the government credit
    -- store exchange rate.
    usd_wallet_cents bigint not null default 200 check (usd_wallet_cents >= 0),
    sim_balance bigint not null default 0 check (sim_balance >= 0),

    -- Cognitive poverty (DESIGN.md section 3): at $0 the agent goes
    -- dormant; unrevived for 24 game-hours (144 ticks) it dies.
    is_dormant boolean not null default false,
    dormant_since_tick bigint check (dormant_since_tick >= 0),

    occupation text,
    employer_business_id uuid,

    health_status text not null default 'healthy'
        check (health_status in ('healthy', 'sick_short', 'sick_medium', 'sick_long', 'terminal', 'deceased')),
    is_alive boolean not null default true,
    died_tick bigint check (died_tick >= 0),
    -- Death paths enumerated in DESIGN.md section 5. Null while alive.
    death_cause text
        check (death_cause is null or death_cause in (
            'terminal_illness', 'untreated_chronic', 'elderly_hazard', 'cognitive_poverty'
        )),

    created_tick bigint not null check (created_tick >= 0),
    created_game_day integer generated always as (created_tick / 144) stored,
    created_game_minute_of_day smallint generated always as ((created_tick % 144) * 10) stored,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint agents_slug_unique unique (slug),
    -- died_tick and death_cause are a pair: a row is either alive with both
    -- null, or dead with both set. This is the shape health/'s death path
    -- and research's mortality queries both depend on.
    constraint agents_death_fields_consistent check (
        (is_alive and died_tick is null and death_cause is null)
        or (not is_alive and died_tick is not null and death_cause is not null)
    )
);

-- employer_business_id -> businesses(id) is a deferred foreign key: agents
-- and businesses reference each other (an agent owns a business, a
-- business employs an agent), so one side must be created without the
-- constraint and have it added once the other table exists. See
-- 20260730120200_businesses.sql for the ALTER TABLE that closes this loop.

-- Per-agent timelines: "click any agent -> ... trajectory" (DESIGN.md
-- section 8) and every research track that studies one agent over time.
create index if not exists idx_agents_household on public.agents (household_id);
create index if not exists idx_agents_parent on public.agents (parent_agent_id);
create index if not exists idx_agents_employer on public.agents (employer_business_id);
-- Population scans (god-tab dial, pension sweeps, health hazard rolls) only
-- ever care about the living; a partial index keeps that scan cheap as the
-- dead accumulate over a multi-day run.
create index if not exists idx_agents_alive on public.agents (id) where is_alive;
create index if not exists idx_agents_dormant on public.agents (dormant_since_tick) where is_dormant;

alter table public.agents enable row level security;

-- Read-only public policy: game/'s live view and research/'s batch jobs
-- read through the anon/authenticated Supabase roles (app/CONVENTIONS.md:
-- "game ... reads live state from Supabase Realtime", "research ... reads
-- Supabase tables"). Writes are not granted to those roles at all - only
-- world/ and broker/ write, and they do so with the service role key,
-- which bypasses RLS entirely, so no write policy is declared here.
--
-- RLS policies only gate which ROWS a role can see once it already has
-- the underlying table privilege; a hosted Supabase project grants these
-- privileges as part of its own provisioning (SELECT to anon/authenticated,
-- full read/write to service_role, which also carries BYPASSRLS), but this
-- migration set is also meant to stand up a self-hosted Postgres (see
-- 20260730120000_extensions.sql), which has no such provisioning. Both
-- grants below were added after live-testing this migration against a bare
-- Postgres 18 and finding SELECT denied for anon and INSERT denied for
-- service_role despite RLS being configured correctly - they make the
-- privilege grants this schema's own responsibility instead of an
-- assumption about the platform.
grant select on public.agents to anon, authenticated;
grant select, insert, update, delete on public.agents to service_role;

create policy agents_select_all on public.agents
    for select
    to anon, authenticated
    using (true);
