{{ config(materialized='table') }}

with hotel_prices as (
select
    source,
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
group by source, city_iata, hotel_id, checkin_date, checkout_date, observed_at::date, guests, currency_code
)

select
    hotel_prices.*,
    hotels.hotel_key,
    observed_dates.date_key as observed_date_key,
    checkin_dates.date_key as checkin_date_key,
    checkout_dates.date_key as checkout_date_key
from hotel_prices
left join {{ ref('dim_hotel') }} as hotels
  using (source, hotel_id)
left join {{ ref('dim_date') }} as observed_dates
  on observed_dates.date_key = hotel_prices.observed_date
left join {{ ref('dim_date') }} as checkin_dates
  on checkin_dates.date_key = hotel_prices.checkin_date
left join {{ ref('dim_date') }} as checkout_dates
  on checkout_dates.date_key = hotel_prices.checkout_date
