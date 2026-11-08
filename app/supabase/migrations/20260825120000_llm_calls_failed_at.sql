alter table llm_calls add column failed_at text;

create index if not exists idx_llm_calls_failed_at
    on llm_calls (failed_at) where failed_at is not null;
