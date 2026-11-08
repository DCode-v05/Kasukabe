alter table llm_calls add column task_label text;

create index if not exists idx_llm_calls_task_label
    on llm_calls (task_label, agent_id);
