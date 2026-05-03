-- RPC: get_poll_user_history
--
-- Purpose:
--   Returns one history row per requested mobile number so the FastAPI app can
--   calculate the WhatsApp poll propensity score without running many separate
--   table queries from Python.
--
-- What it calculates:
--   1. Valid lifetime purchases and last purchase date.
--   2. Purchase counts in the last 30 and 60 days.
--   3. Previous Yes-vote behavior from public.poll_prediction.
--   4. Whether the last two Yes votes converted into a valid order.
--
-- Conversion rule:
--   A past Yes vote is considered converted when the user has any valid order
--   on/after that poll date and before their next Yes poll date.
--
-- Expected production tables/columns:
--   "Auth".id, "Auth".mobile
--   "User".id, "User"."authId"
--   "LPoolOrder".id, "LPoolOrder"."userId", "LPoolOrder"."createdAt"
--   "LPoolOrder"."paymentStatus", "LPoolOrder"."settlementStatus",
--   "LPoolOrder"."exceptionType"
--   public.poll_prediction.mobile, public.poll_prediction.poll_date,
--   public.poll_prediction.vote, public.poll_prediction.created_at

create or replace function public.get_poll_user_history(phone_numbers text[])
returns table (
    mobile text,
    total_purchases bigint,
    last_purchase_date timestamptz,
    purchases_last_30_days bigint,
    purchases_last_60_days bigint,
    total_yes_votes bigint,
    last_vote_converted boolean,
    n_2_vote_converted boolean
)
language sql
stable
as $$
with valid_orders as (
    select
        a.mobile,
        lpo.id as order_id,
        lpo."createdAt" as order_created_at
    from "Auth" a
    join "User" u on u."authId" = a.id
    join "LPoolOrder" lpo on lpo."userId" = u.id
    where a.mobile = any(phone_numbers)
      and (
        lpo."paymentStatus" = 'Yes'
        or lpo."settlementStatus" = 'Yes'
        or lpo."exceptionType" = 'deltarefund_done'
      )
),
order_summary as (
    select
        vo.mobile,
        count(*) as total_purchases,
        max(vo.order_created_at) as last_purchase_date,
        count(*) filter (
            where vo.order_created_at >= now() - interval '30 days'
        ) as purchases_last_30_days,
        count(*) filter (
            where vo.order_created_at >= now() - interval '60 days'
        ) as purchases_last_60_days
    from valid_orders vo
    group by vo.mobile
),
past_yes_votes as (
    select
        pp.mobile,
        pp.poll_date,
        pp.created_at,
        lead(pp.poll_date) over (
            partition by pp.mobile
            order by pp.poll_date, pp.created_at
        ) as next_poll_date
    from public.poll_prediction pp
    where pp.mobile = any(phone_numbers)
      and pp.vote = 1
),
converted_votes as (
    select
        pyv.mobile,
        pyv.poll_date,
        pyv.created_at,
        exists (
            select 1
            from valid_orders vo
            where vo.mobile = pyv.mobile
              and vo.order_created_at::date >= pyv.poll_date
              and (
                pyv.next_poll_date is null
                or vo.order_created_at::date < pyv.next_poll_date
              )
        ) as converted
    from past_yes_votes pyv
),
ranked_votes as (
    select
        cv.*,
        row_number() over (
            partition by cv.mobile
            order by cv.poll_date desc, cv.created_at desc
        ) as vote_rank,
        count(*) over (
            partition by cv.mobile
        ) as total_yes_votes
    from converted_votes cv
)
select
    input_mobile.mobile,
    coalesce(os.total_purchases, 0) as total_purchases,
    os.last_purchase_date,
    coalesce(os.purchases_last_30_days, 0) as purchases_last_30_days,
    coalesce(os.purchases_last_60_days, 0) as purchases_last_60_days,
    coalesce(max(rv.total_yes_votes), 0) as total_yes_votes,
    max(rv.converted) filter (where rv.vote_rank = 1) as last_vote_converted,
    max(rv.converted) filter (where rv.vote_rank = 2) as n_2_vote_converted
from unnest(phone_numbers) as input_mobile(mobile)
left join order_summary os on os.mobile = input_mobile.mobile
left join ranked_votes rv on rv.mobile = input_mobile.mobile
group by
    input_mobile.mobile,
    os.total_purchases,
    os.last_purchase_date,
    os.purchases_last_30_days,
    os.purchases_last_60_days;
$$;

