create table if not exists public.poll_prediction (
    id uuid primary key default gen_random_uuid(),
    mobile text not null,
    product_name text not null,
    poll_date date not null,
    vote int not null check (vote in (0, 1)),
    prediction_score real check (
        prediction_score is null
        or (prediction_score >= 0 and prediction_score <= 100)
    ),
    source_filename text not null,
    created_at timestamptz not null default now()
);

create index if not exists poll_prediction_mobile_idx
    on public.poll_prediction (mobile);

create index if not exists poll_prediction_poll_date_idx
    on public.poll_prediction (poll_date);

