create table if not exists public.eval_metrics (
    id bigint generated always as identity primary key,

    track text not null
        check (track in (
            'political_science', 'economics', 'sociology', 'religion',
            'machine_psychology', 'business_readiness', 'mas_engineering'
        )),
    metric_name text not null,
    metric_value double precision,
    metric_payload jsonb,

    ablation_arm text not null default 'full' check (ablation_arm in ('full', 'no_reflection', 'no_learning')),
    agent_id uuid references public.agents (id),
    source text not null default 'batch_job' check (source in ('batch_job', 'dream_probe', 'live_query')),

    tick bigint not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day smallint generated always as ((tick % 144) * 10) stored,

    computed_at timestamptz not null default now()
);

create index if not exists idx_eval_metrics_track_name_tick on public.eval_metrics (track, metric_name, tick);
create index if not exists idx_eval_metrics_agent_name_tick on public.eval_metrics (agent_id, metric_name, tick)
    where agent_id is not null;
create index if not exists idx_eval_metrics_arm_track_tick on public.eval_metrics (ablation_arm, track, tick);

alter table public.eval_metrics enable row level security;

grant select on public.eval_metrics to anon, authenticated;
grant select, insert, update, delete on public.eval_metrics to service_role;

create policy eval_metrics_select_all on public.eval_metrics
    for select
    to anon, authenticated
    using (true);
