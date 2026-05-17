create table if not exists public.whatsapp_poll_vote_snapshot (
    id uuid primary key default gen_random_uuid(),
    group_jid text not null,
    poll_message_id text not null,
    poll_title text not null,
    voter_jid text not null,
    voter_phone text,
    selected_options jsonb not null default '[]'::jsonb,
    normalized_vote int check (normalized_vote in (0, 1) or normalized_vote is null),
    last_vote_timestamp timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (poll_message_id, voter_jid)
);

create index if not exists whatsapp_poll_vote_snapshot_group_idx
    on public.whatsapp_poll_vote_snapshot (group_jid);

create index if not exists whatsapp_poll_vote_snapshot_poll_idx
    on public.whatsapp_poll_vote_snapshot (poll_message_id);
