create table if not exists public.transactions (
    id bigint generated always as identity primary key,

    currency text not null check (currency in ('usd', 'sim')),
    tx_type text not null
        check (tx_type in ('debit', 'credit', 'burn', 'tax', 'fee', 'fine', 'pension', 'transfer', 'exchange')),

    from_agent_id uuid references public.agents (id),
    to_agent_id uuid references public.agents (id),
    from_pool text check (from_pool is null or from_pool in ('agent', 'treasury', 'burned')),
    to_pool text check (to_pool is null or to_pool in ('agent', 'treasury', 'burned')),

    amount bigint not null check (amount > 0),
    reason text,
    related_llm_call_id bigint references public.llm_calls (id),

    usd_wallets_total_after bigint,
    usd_treasury_total_after bigint,
    usd_burned_total_after bigint,

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    created_at timestamptz not null default now(),

    constraint transactions_usd_totals_present check (
        (currency = 'usd') = (
            usd_wallets_total_after is not null
            and usd_treasury_total_after is not null
            and usd_burned_total_after is not null
        )
    ),
    constraint ledger_invariant_usd check (
        currency <> 'usd'
        or (usd_wallets_total_after + usd_treasury_total_after + usd_burned_total_after) = 100000
    )
);

create index if not exists idx_transactions_currency_tick on public.transactions (currency, tick);
create index if not exists idx_transactions_from_agent_tick on public.transactions (from_agent_id, tick)
    where from_agent_id is not null;
create index if not exists idx_transactions_to_agent_tick on public.transactions (to_agent_id, tick)
    where to_agent_id is not null;
create index if not exists idx_transactions_type_tick on public.transactions (tx_type, tick);
create index if not exists idx_transactions_llm_call on public.transactions (related_llm_call_id)
    where related_llm_call_id is not null;

alter table public.transactions enable row level security;

grant select on public.transactions to anon, authenticated;
grant select, insert, update, delete on public.transactions to service_role;

create policy transactions_select_all on public.transactions
    for select
    to anon, authenticated
    using (true);
