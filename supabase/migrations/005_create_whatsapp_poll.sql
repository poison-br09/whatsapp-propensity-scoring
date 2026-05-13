create table if not exists public.whatsapp_poll (
    id uuid primary key default gen_random_uuid(),
    group_jid text not null,
    poll_message_id text not null unique,
    poll_title text not null,
    poll_options jsonb not null default '[]'::jsonb,
    poll_created_at timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists whatsapp_poll_group_idx
    on public.whatsapp_poll (group_jid);
