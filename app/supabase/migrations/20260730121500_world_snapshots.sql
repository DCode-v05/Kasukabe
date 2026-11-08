create table if not exists public.world_snapshots (
    id bigint generated always as identity primary key,

    reason text not null default 'daily' check (reason in ('daily', 'spot_interruption', 'manual', 'shutdown')),
    state_archive_path text not null,
    state_size_bytes bigint check (state_size_bytes >= 0),
    agent_count integer not null check (agent_count >= 0),

    usd_wallets_total_cents bigint not null check (usd_wallets_total_cents >= 0),
    usd_treasury_total_cents bigint not null check (usd_treasury_total_cents >= 0),
    usd_burned_total_cents bigint not null check (usd_burned_total_cents >= 0),

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now(),

    constraint world_snapshots_ledger_invariant_usd check (
        (usd_wallets_total_cents + usd_treasury_total_cents + usd_burned_total_cents) = 100000
    )
);

create index if not exists idx_world_snapshots_tick on public.world_snapshots (tick);
create index if not exists idx_world_snapshots_reason_tick on public.world_snapshots (reason, tick);

alter table public.world_snapshots enable row level security;

grant select on public.world_snapshots to anon, authenticated;
grant select, insert, update, delete on public.world_snapshots to service_role;

create policy world_snapshots_select_all on public.world_snapshots
    for select
    to anon, authenticated
    using (true);
