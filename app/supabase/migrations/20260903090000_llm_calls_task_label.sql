-- What a turn was FOR, so C1 has something to group by.
--
-- Evaluation spec section 4: "C1 cannot be measured without one, and no
-- turn carries one today." C1 is median tokens per turn, per task, per
-- resident, day five against day one. Without this column the roadmap's
-- first compounding quantity is unmeasurable rather than unmeasured.
--
-- Deterministic, from the tools the turn called - never a model's opinion,
-- because a label a judge assigned would make C1 a measurement of the
-- judge.
alter table llm_calls add column task_label text;

create index if not exists idx_llm_calls_task_label
    on llm_calls (task_label, agent_id);
