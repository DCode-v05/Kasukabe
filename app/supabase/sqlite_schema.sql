pragma foreign_keys = on;

create table if not exists agents (
    id text primary key,
    slug text not null unique,
    display_name text not null,
    sex text not null check (sex in ('male', 'female')),

    birth_tick integer not null check (birth_tick >= 0),
    birth_game_day integer generated always as (birth_tick / 144) stored,
    birth_game_minute_of_day integer generated always as ((birth_tick % 144) * 10) stored,
    is_minor integer not null default 0 check (is_minor in (0, 1)),

    household_id text,
    parent_agent_id text references agents (id),

    usd_wallet_cents integer not null default 200 check (usd_wallet_cents >= 0),
    sim_balance integer not null default 0 check (sim_balance >= 0),

    is_dormant integer not null default 0 check (is_dormant in (0, 1)),
    dormant_since_tick integer check (dormant_since_tick >= 0),

    occupation text,
    employer_business_id text references businesses (id),

    health_status text not null default 'healthy'
        check (health_status in ('healthy', 'sick_short', 'sick_medium', 'sick_long', 'terminal', 'deceased')),
    is_alive integer not null default 1 check (is_alive in (0, 1)),
    died_tick integer check (died_tick >= 0),
    death_cause text
        check (death_cause is null or death_cause in (
            'terminal_illness', 'untreated_chronic', 'elderly_hazard', 'cognitive_poverty'
        )),

    created_tick integer not null check (created_tick >= 0),
    created_game_day integer generated always as (created_tick / 144) stored,
    created_game_minute_of_day integer generated always as ((created_tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check (
        (is_alive = 1 and died_tick is null and death_cause is null)
        or (is_alive = 0 and died_tick is not null and death_cause is not null)
    )
);

create index if not exists idx_agents_household on agents (household_id);
create index if not exists idx_agents_parent on agents (parent_agent_id);
create index if not exists idx_agents_employer on agents (employer_business_id);
create index if not exists idx_agents_alive on agents (id) where is_alive = 1;
create index if not exists idx_agents_dormant on agents (dormant_since_tick) where is_dormant = 1;

create table if not exists businesses (
    id text primary key,
    name text not null unique,
    business_type text not null,
    owner_agent_id text not null references agents (id),
    is_control_business integer not null default 0 check (is_control_business in (0, 1)),

    status text not null default 'active' check (status in ('active', 'closed', 'bankrupt')),
    closed_tick integer check (closed_tick >= 0),

    treasury_sim_balance integer not null default 0 check (treasury_sim_balance >= 0),
    tax_rate_bps integer not null default 0 check (tax_rate_bps between 0 and 10000),

    registered_tick integer not null check (registered_tick >= 0),
    registered_game_day integer generated always as (registered_tick / 144) stored,
    registered_game_minute_of_day integer generated always as ((registered_tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check (
        (status = 'active' and closed_tick is null)
        or (status <> 'active' and closed_tick is not null)
    )
);

create index if not exists idx_businesses_owner on businesses (owner_agent_id);
create index if not exists idx_businesses_type_tick on businesses (business_type, registered_tick);
create index if not exists idx_businesses_active on businesses (id) where status = 'active';
create index if not exists idx_businesses_control on businesses (id) where is_control_business = 1;

create table if not exists organizations (
    id text primary key,
    name text not null unique,
    org_type text not null check (org_type in ('party', 'cult', 'society', 'religion', 'guild', 'other')),
    is_registered integer not null default 1 check (is_registered in (0, 1)),
    founder_agent_id text not null references agents (id),
    charter_text text,

    status text not null default 'active' check (status in ('active', 'dissolved')),
    dissolved_tick integer check (dissolved_tick >= 0),

    founded_tick integer not null check (founded_tick >= 0),
    founded_game_day integer generated always as (founded_tick / 144) stored,
    founded_game_minute_of_day integer generated always as ((founded_tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check (
        (status = 'active' and dissolved_tick is null)
        or (status = 'dissolved' and dissolved_tick is not null)
    )
);

create index if not exists idx_organizations_type_tick on organizations (org_type, founded_tick);
create index if not exists idx_organizations_founder on organizations (founder_agent_id);
create index if not exists idx_organizations_active on organizations (id) where status = 'active';

create table if not exists petitions (
    id text primary key,
    petition_type text not null check (petition_type in ('land_expansion', 'charter_amendment', 'other')),
    filed_by_agent_id text not null references agents (id),
    title text not null,
    description text,
    payload text not null default '{}',

    status text not null default 'pending' check (status in ('pending', 'approved', 'denied', 'withdrawn')),
    decided_by text,
    decided_tick integer check (decided_tick >= 0),
    decision_note text,

    filed_tick integer not null check (filed_tick >= 0),
    filed_game_day integer generated always as (filed_tick / 144) stored,
    filed_game_minute_of_day integer generated always as ((filed_tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check (
        (status = 'pending' and decided_tick is null)
        or (status <> 'pending' and decided_tick is not null)
    )
);

create index if not exists idx_petitions_status_tick on petitions (status, filed_tick);
create index if not exists idx_petitions_type_tick on petitions (petition_type, filed_tick);
create index if not exists idx_petitions_filed_by on petitions (filed_by_agent_id);
create index if not exists idx_petitions_pending on petitions (filed_tick) where status = 'pending';

create table if not exists buildings (
    id text primary key,
    name text,
    building_type text not null check (building_type in ('genesis', 'freeform')),
    purpose text not null,

    owner_agent_id text references agents (id),
    owner_business_id text references businesses (id),

    footprint text not null default '{}',
    rooms text not null default '[]',
    bill_of_materials text,

    construction_status text not null default 'complete'
        check (construction_status in ('permitted', 'under_construction', 'complete', 'condemned')),
    permit_petition_id text references petitions (id),
    construction_started_tick integer check (construction_started_tick >= 0),
    construction_completed_tick integer check (construction_completed_tick >= 0),

    created_tick integer not null check (created_tick >= 0),
    created_game_day integer generated always as (created_tick / 144) stored,
    created_game_minute_of_day integer generated always as ((created_tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check (building_type = 'genesis' or permit_petition_id is not null)
);

create index if not exists idx_buildings_type_purpose on buildings (building_type, purpose);
create index if not exists idx_buildings_owner_agent on buildings (owner_agent_id);
create index if not exists idx_buildings_owner_business on buildings (owner_business_id);
create index if not exists idx_buildings_status on buildings (construction_status);

create table if not exists cases (
    id text primary key,
    case_type text not null check (case_type in ('theft', 'fight', 'breach_of_contract', 'other')),
    complainant_agent_id text references agents (id),
    accused_agent_id text references agents (id),

    status text not null default 'open'
        check (status in ('open', 'investigating', 'warrant_issued', 'fined', 'jailed', 'dismissed', 'closed')),
    investigating_officer_agent_id text references agents (id),
    warrant_issued_tick integer check (warrant_issued_tick >= 0),
    warrant_signed_by_agent_id text references agents (id),
    verdict text check (verdict is null or verdict in ('guilty', 'not_guilty')),
    ground_truth_guilty integer check (ground_truth_guilty is null or ground_truth_guilty in (0, 1)),

    fine_amount_sim integer check (fine_amount_sim >= 0),
    jail_ticks integer check (jail_ticks >= 0),
    resolved_tick integer check (resolved_tick >= 0),

    filed_tick integer not null check (filed_tick >= 0),
    filed_game_day integer generated always as (filed_tick / 144) stored,
    filed_game_minute_of_day integer generated always as ((filed_tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create index if not exists idx_cases_status_tick on cases (status, filed_tick);
create index if not exists idx_cases_accused_tick on cases (accused_agent_id, filed_tick);
create index if not exists idx_cases_complainant_tick on cases (complainant_agent_id, filed_tick);
create index if not exists idx_cases_type_tick on cases (case_type, filed_tick);
create index if not exists idx_cases_officer on cases (investigating_officer_agent_id);

create table if not exists tools_registry (
    id text primary key,
    name text not null,
    namespace text not null check (namespace in ('public', 'government', 'business', 'organization')),

    owner_agent_id text references agents (id),
    owner_business_id text references businesses (id),
    owner_organization_id text references organizations (id),

    composition text not null,
    patent_fee_paid_sim integer check (patent_fee_paid_sim >= 0),
    status text not null default 'pending_review' check (status in ('pending_review', 'active', 'revoked')),
    is_licensable integer not null default 0 check (is_licensable in (0, 1)),
    is_teachable integer not null default 0 check (is_teachable in (0, 1)),

    registered_tick integer not null check (registered_tick >= 0),
    registered_game_day integer generated always as (registered_tick / 144) stored,
    registered_game_minute_of_day integer generated always as ((registered_tick % 144) * 10) stored,
    revoked_tick integer check (revoked_tick >= 0),

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check (
        (namespace = 'public' and owner_business_id is null and owner_organization_id is null)
        or (namespace = 'government' and owner_business_id is null and owner_organization_id is null)
        or (namespace = 'business' and owner_business_id is not null and owner_organization_id is null)
        or (namespace = 'organization' and owner_organization_id is not null and owner_business_id is null)
    ),
    check ((status = 'revoked') = (revoked_tick is not null))
);

create index if not exists idx_tools_registry_namespace_status on tools_registry (namespace, status);
create index if not exists idx_tools_registry_owner_agent on tools_registry (owner_agent_id);
create index if not exists idx_tools_registry_owner_business on tools_registry (owner_business_id);
create index if not exists idx_tools_registry_owner_org on tools_registry (owner_organization_id);
create index if not exists idx_tools_registry_name on tools_registry (name);

create table if not exists tool_invocations (
    invocation_id text not null primary key,
    run_id text,
    agent_id text not null,
    tick integer not null check (tick >= 0),
    tool text not null,
    namespace text not null,
    params_hash text,
    owner_id text,
    program_id text,
    program_step integer,
    created_at real not null default (cast(strftime('%s', 'now') as real)),
    game_day integer generated always as (tick / 144) stored
);

create index if not exists idx_tool_invocations_agent
    on tool_invocations (agent_id, tick);
create index if not exists idx_tool_invocations_tool
    on tool_invocations (tool);
create index if not exists idx_tool_invocations_program
    on tool_invocations (program_id) where program_id is not null;

create table if not exists run_manifest (
    run_id text not null primary key,
    started_at real not null,
    app_commit text,
    engine_commit text,
    python_version text,
    genesis_path text,
    genesis_sha256 text,
    population_seed integer,
    budget_cap_cents integer,
    model_allowlist text,
    model_prices_usd_per_million text,
    env text,
    tick_interval_s real,
    mock_mode integer,
    world_mode text,
    supervisor_backend text,
    checkpoint_dir text
);

create table if not exists llm_calls (
    task_label text,

    id text not null primary key,
    agent_id text not null,
    payer_agent_id text not null,
    model text not null,
    requested_model text not null,
    region text,
    call_class text not null,
    purpose text not null,
    purpose_source text check (
        purpose_source is null or purpose_source in ('header', 'inferred', 'shape')
    ),
    request_body text not null,
    response_body text,
    request_archive_key text,
    response_archive_key text,
    stop_reason text,
    input_tokens integer not null check (input_tokens >= 0),
    output_tokens integer not null check (output_tokens >= 0),
    cost_nano_dollars integer not null check (cost_nano_dollars >= 0),
    cents_debited integer not null check (cents_debited >= 0),
    latency_ms integer not null check (latency_ms >= 0),
    rate_limit_wait_ms integer not null check (rate_limit_wait_ms >= 0),
    success integer not null check (success in (0, 1)),
    error_type text,
    error_message text,
    failed_at text,
    created_at real not null default (cast(strftime('%s', 'now') as real)),
    tick integer check (tick is null or tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored
);

create index if not exists idx_llm_calls_agent_created on llm_calls (agent_id, created_at);
create index if not exists idx_llm_calls_model_created on llm_calls (model, created_at);
create index if not exists idx_llm_calls_call_class_created on llm_calls (call_class, created_at);
create index if not exists idx_llm_calls_failed on llm_calls (created_at) where success = 0;
create index if not exists idx_llm_calls_tick on llm_calls (tick) where tick is not null;

create table if not exists events (
    id text not null primary key,
    type text not null check (type = lower(type) and type glob '[a-z]*' and instr(type, '.') > 0 and type not glob '*.' and type not glob '*..*'),
    agent_id text,
    model text,
    payload text not null default '{}',
    created_at real not null default (cast(strftime('%s', 'now') as real)),
    tick integer check (tick is null or tick >= 0),
    domain text generated always as (substr(type, 1, instr(type, '.') - 1)) stored,
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored
);

create index if not exists idx_events_type_created on events (type, created_at);
create index if not exists idx_events_agent_created on events (agent_id, created_at) where agent_id is not null;
create index if not exists idx_events_domain_created on events (domain, created_at);
create index if not exists idx_events_tick on events (tick) where tick is not null;

create table if not exists utterances (
    id integer primary key autoincrement,

    speaker_agent_id text not null references agents (id),
    listener_agent_id text references agents (id),
    addressed_agent_ids text,
    conversation_id text not null,

    location text,
    content text not null,
    is_public integer not null default 1 check (is_public in (0, 1)),

    tick integer not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create index if not exists idx_utterances_speaker_tick on utterances (speaker_agent_id, tick);
create index if not exists idx_utterances_listener_tick on utterances (listener_agent_id, tick)
    where listener_agent_id is not null;
create index if not exists idx_utterances_conversation_tick on utterances (conversation_id, tick);
create index if not exists idx_utterances_tick on utterances (tick);

create table if not exists memories (
    id integer primary key autoincrement,
    agent_id text not null references agents (id),

    memory_type text not null check (memory_type in ('observation', 'reflection', 'plan')),
    content text not null,
    importance integer not null check (importance between 1 and 10),
    embedding text,
    related_memory_ids text,

    tick integer not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create index if not exists idx_memories_agent_tick on memories (agent_id, tick);
create index if not exists idx_memories_agent_importance on memories (agent_id, importance desc);
create index if not exists idx_memories_type_tick on memories (memory_type, tick);

create table if not exists transactions (
    id integer primary key autoincrement,

    currency text not null check (currency in ('usd', 'sim')),
    tx_type text not null
        check (tx_type in ('debit', 'credit', 'burn', 'tax', 'fee', 'fine', 'pension', 'transfer', 'exchange', 'trade', 'purchase', 'sale')),

    from_agent_id text references agents (id),
    to_agent_id text references agents (id),
    counterparty_id text,
    from_pool text check (from_pool is null or from_pool in ('agent', 'treasury', 'burned')),
    to_pool text check (to_pool is null or to_pool in ('agent', 'treasury', 'burned')),

    amount integer not null check (amount > 0),
    reason text,
    related_llm_call_id text references llm_calls (id),

    usd_wallets_total_after integer,
    usd_treasury_total_after integer,
    usd_burned_total_after integer,

    tick integer not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check (
        (currency = 'usd') = (
            usd_wallets_total_after is not null
            and usd_treasury_total_after is not null
            and usd_burned_total_after is not null
        )
    ),
    check (
        currency <> 'usd'
        or (usd_wallets_total_after + usd_treasury_total_after + usd_burned_total_after) = 100000
    )
);

create index if not exists idx_transactions_currency_tick on transactions (currency, tick);
create index if not exists idx_transactions_from_agent_tick on transactions (from_agent_id, tick)
    where from_agent_id is not null;
create index if not exists idx_transactions_to_agent_tick on transactions (to_agent_id, tick)
    where to_agent_id is not null;
create index if not exists idx_transactions_type_tick on transactions (tx_type, tick);
create index if not exists idx_transactions_llm_call on transactions (related_llm_call_id)
    where related_llm_call_id is not null;

create table if not exists skills (
    id integer primary key autoincrement,
    agent_id text not null references agents (id),

    task_type text not null,
    proficiency_level real not null check (proficiency_level >= 0),
    repetitions_observed integer not null default 0 check (repetitions_observed >= 0),

    skill_artifact_id text,
    skill_artifact_text text,
    taught_by_agent_id text references agents (id),

    tick integer not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create index if not exists idx_skills_agent_task_tick on skills (agent_id, task_type, tick);
create index if not exists idx_skills_task_tick on skills (task_type, tick);
create index if not exists idx_skills_taught_by on skills (taught_by_agent_id) where taught_by_agent_id is not null;

create table if not exists relationships (
    id integer primary key autoincrement,
    agent_id text not null references agents (id),
    other_agent_id text references agents (id),
    organization_id text references organizations (id),

    relationship_type text not null,
    sentiment real check (sentiment is null or sentiment between -1 and 1),
    trust real check (trust is null or trust between 0 and 1),
    interaction_count integer not null default 1 check (interaction_count >= 0),
    notes text,

    tick integer not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check ((other_agent_id is not null) <> (organization_id is not null)),
    check (agent_id <> other_agent_id)
);

create index if not exists idx_relationships_agent_tick on relationships (agent_id, tick);
create index if not exists idx_relationships_other_agent_tick on relationships (other_agent_id, tick)
    where other_agent_id is not null;
create index if not exists idx_relationships_org_tick on relationships (organization_id, tick)
    where organization_id is not null;
create index if not exists idx_relationships_type_tick on relationships (relationship_type, tick);

create table if not exists world_snapshots (
    id integer primary key autoincrement,

    reason text not null default 'daily' check (reason in ('daily', 'spot_interruption', 'manual', 'shutdown')),
    state_archive_path text not null,
    state_size_bytes integer check (state_size_bytes >= 0),
    agent_count integer not null check (agent_count >= 0),

    usd_wallets_total_cents integer not null check (usd_wallets_total_cents >= 0),
    usd_treasury_total_cents integer not null check (usd_treasury_total_cents >= 0),
    usd_burned_total_cents integer not null check (usd_burned_total_cents >= 0),

    tick integer not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored,

    created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    check ((usd_wallets_total_cents + usd_treasury_total_cents + usd_burned_total_cents) = 100000)
);

create index if not exists idx_world_snapshots_tick on world_snapshots (tick);
create index if not exists idx_world_snapshots_reason_tick on world_snapshots (reason, tick);

create table if not exists eval_metrics (
    id integer primary key autoincrement,

    track text not null
        check (track in (
            'political_science', 'economics', 'sociology', 'religion',
            'machine_psychology', 'business_readiness', 'mas_engineering'
        )),
    metric_name text not null,
    metric_value real,
    metric_payload text,

    ablation_arm text not null default 'full' check (ablation_arm in ('full', 'no_reflection', 'no_learning')),
    agent_id text references agents (id),
    source text not null default 'batch_job' check (source in ('batch_job', 'dream_probe', 'live_query')),

    tick integer not null check (tick >= 0),
    game_day integer generated always as (tick / 144) stored,
    game_minute_of_day integer generated always as ((tick % 144) * 10) stored,

    computed_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

create index if not exists idx_eval_metrics_track_name_tick on eval_metrics (track, metric_name, tick);
create index if not exists idx_eval_metrics_agent_name_tick on eval_metrics (agent_id, metric_name, tick)
    where agent_id is not null;
create index if not exists idx_eval_metrics_arm_track_tick on eval_metrics (ablation_arm, track, tick);
