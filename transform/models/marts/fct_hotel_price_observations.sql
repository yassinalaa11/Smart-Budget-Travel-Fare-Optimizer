{{ config(materialized='view') }}

select
    city_iata,
    hotel_id,
    checkin_date,
    checkout_date,
    observed_at::date as observed_date,
    guests,
    currency_code,
    min(total_price)::numeric(12,2) as min_total_price,
    percentile_cont(0.5) within group (order by total_price)::numeric(12,2) as median_total_price,
    count(*)::integer as observation_count
from {{ ref('stg_hotel_price_observations') }}
where hotel_id is not null and checkin_date < checkout_date and total_price > 0
group by city_iata, hotel_id, checkin_date, checkout_date, observed_at::date, guests, currency_code
