-- Which link in the chain broke, on a failed llm_call.
--
-- bedrock | ledger | world | engine | broker-internal, null on success.
--
-- Added because the HTTP status and "where did this actually break" are not
-- the same question, and the last run answered the second one wrongly for its
-- whole duration: a `502 UpstreamError` really meant the broker could not
-- reach world's ledger - a downstream failure labelled upstream. Every one of
-- the 5,724 failures in that run was indistinguishable from a Bedrock outage
-- when read from this table.
alter table llm_calls add column failed_at text;

create index if not exists idx_llm_calls_failed_at
    on llm_calls (failed_at) where failed_at is not null;
