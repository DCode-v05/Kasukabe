-- Everyone a remark was addressed to, not only the one the parameter named.
--
-- `utterances.listener_agent_id` holds exactly one id, which is Goffman's
-- dyadic model in a town whose rooms hold five people. Run 19 has Ezra
-- Okonjo answering Father Anselm and Tova Okonjo in one utterance recorded
-- against Anselm alone; Tova had asked the question he was answering, and
-- the world went on telling her she was unanswered.
--
-- The world has known the full list since `social.everyone_addressed` and
-- carries it on the event and in the in-memory tail, and it stopped at the
-- table: the trace had no column for it, so the fix was invisible to every
-- measurement read from the store, and baseline B2 - "a resident named in
-- a remark is told they were addressed" - could not be computed at all.
--
-- JSON text rather than a join table: it is read whole, never joined on,
-- and a second table for a list that is almost always one or two ids would
-- cost a join on every conversation query for nothing.
alter table utterances add column addressed_agent_ids text;

create index if not exists idx_utterances_addressed
    on utterances (conversation_id, tick);
