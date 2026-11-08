alter table utterances add column addressed_agent_ids text;

create index if not exists idx_utterances_addressed
    on utterances (conversation_id, tick);
