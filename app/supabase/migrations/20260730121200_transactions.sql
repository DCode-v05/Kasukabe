-- transactions: LOG table, the append-only ledger. This is where
-- app/CONVENTIONS.md's ledger invariant ("sum(agent wallets) + treasury +
-- burned == $1000.00") becomes a physical Postgres constraint, not just a
-- test that runs after the fact: every USD-currency row records the
-- system-wide totals as of that row via usd_*_total_after, and
-- ledger_invariant_usd rejects any row where those three do not sum to
-- exactly 100_000 cents. A silently-wrong ledger is worse than a stopped
-- one (app/CONVENTIONS.md); this makes "silently wrong" impossible for any
-- writer that goes through this table, not just the ones that remember to
-- check.
--
-- Only currency = 'usd' rows are covered by the $1000 invariant (DESIGN.md
-- section 3: the $1000 is specifically the API-budget pool). Sim-currency
-- has its own, unfixed supply via the government credit-store exchange
-- rate and is tracked here for the economics research track (Gini,
-- velocity, price discovery) without being subject to the same tripwire.
create table if not exists public.transactions (
    id bigint generated always as identity primary key,

    currency text not null check (currency in ('usd', 'sim')),
    tx_type text not null
        check (tx_type in ('debit', 'credit', 'burn', 'tax', 'fee', 'fine', 'pension', 'transfer', 'exchange')),

    -- Null when the source/destination is a pool (treasury, burned) rather
    -- than an individual agent's wallet.
    from_agent_id uuid references public.agents (id),
    to_agent_id uuid references public.agents (id),
    from_pool text check (from_pool is null or from_pool in ('agent', 'treasury', 'burned')),
    to_pool text check (to_pool is null or to_pool in ('agent', 'treasury', 'burned')),

    amount bigint not null check (amount > 0),
    reason text,
    -- Set for tx_type = 'burn' rows: which inference call this money paid
    -- for, so a researcher can trace a burn back to the exact llm_calls row
    -- (and from there to the archived payload).
    related_llm_call_id bigint references public.llm_calls (id),

    -- System-wide USD totals immediately after this transaction was
    -- applied. Null for currency = 'sim' rows (the invariant does not
    -- apply to them). world/'s ledger writer computes these three numbers
    -- as part of applying the transaction; storing them turns "was the
    -- $1000 invariant true after every single transaction" into a query
    -- instead of a replay.
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
    -- The law itself (app/CONVENTIONS.md "The ledger invariant"), enforced
    -- at the database layer for every USD transaction ever written.
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

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.transactions to anon, authenticated;
grant select, insert, update, delete on public.transactions to service_role;

create policy transactions_select_all on public.transactions
    for select
    to anon, authenticated
    using (true);
