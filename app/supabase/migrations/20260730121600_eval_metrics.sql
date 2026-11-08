-- eval_metrics: LOG table for research/'s computed output (DESIGN.md
-- section 10: 7 research tracks, dream probes, ablation arms). A metric
-- can be scalar (metric_value) or structured (metric_payload, e.g. a
-- network snapshot for topology metrics) or both.
create table if not exists public.eval_metrics (
    id bigint generated always as identity primary key,

    track text not null
        check (track in (
            'political_science', 'economics', 'sociology', 'religion',
            'machine_psychology', 'business_readiness', 'mas_engineering'
        )),
    -- Free text, not an enum: DESIGN.md section 10 names flagship metrics
    -- per track as examples ("Authoritarianism Index", "Gini + mobility",
    -- ...) but the research program is meant to grow beyond that starting
    -- list without a migration per new metric.
    metric_name text not null,
    metric_value double precision,
    metric_payload jsonb,

    -- DESIGN.md section 10 instruments: ~210 full-arm agents, 20
    -- no-reflection, 20 no-learning. Every metric row states which arm it
    -- was computed over so ablation comparisons are a straight WHERE
    -- clause instead of a join against a separate cohort table.
    ablation_arm text not null default 'full' check (ablation_arm in ('full', 'no_reflection', 'no_learning')),
    -- Null for population-level metrics (Gini, turnout); set for
    -- per-agent metrics (soul drift, model-choice metacognition).
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

-- See 20260730120100_agents.sql for why this grant is explicit rather than
-- assumed from Supabase's own provisioning.
grant select on public.eval_metrics to anon, authenticated;
grant select, insert, update, delete on public.eval_metrics to service_role;

create policy eval_metrics_select_all on public.eval_metrics
    for select
    to anon, authenticated
    using (true);
