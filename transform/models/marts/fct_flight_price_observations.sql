{{ config(materialized='view') }}

select
    origin_iata || '-' || destination_iata as route_id,
    origin_iata,
    destination_iata,
    departure_date,
    return_date,
    observed_at::date as observed_date,
    cabin_class,
    currency_code,
    min(total_price)::numeric(12, 2) as min_total_price,
    percentile_cont(0.5) within group (order by total_price)::numeric(12, 2)
        as median_total_price,
    count(*)::integer as offer_count,
    count(distinct raw_response_id)::integer as response_count,
    bool_or(is_demo) as contains_demo_data,
    bool_and(is_demo) as all_data_is_demo
from {{ ref('int_flight_price_quality') }}
where origin_iata is not null
  and destination_iata is not null
  and departure_date is not null
  and return_date is not null
  and total_price is not null
  and currency_code is not null
  and quality_reason is null
group by
    origin_iata,
    destination_iata,
    departure_date,
    return_date,
    observed_at::date,
    cabin_class,
    currency_code
