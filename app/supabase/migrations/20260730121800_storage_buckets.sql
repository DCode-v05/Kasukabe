insert into storage.buckets (id, name, public)
values ('llm-payloads', 'llm-payloads', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('world-snapshots', 'world-snapshots', false)
on conflict (id) do nothing;
