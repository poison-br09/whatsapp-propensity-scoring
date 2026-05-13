alter table public.whatsapp_poll_vote_event
    add column if not exists normalized_vote int;

alter table public.whatsapp_poll_vote_event
    drop constraint if exists whatsapp_poll_vote_event_normalized_vote_check;

alter table public.whatsapp_poll_vote_event
    add constraint whatsapp_poll_vote_event_normalized_vote_check
    check (normalized_vote in (0, 1) or normalized_vote is null);
