{{ config(materialized='view') }}

with ranked_offers as (
    select
        *,
        row_number() over (
            partition by
                provider,
                origin_iata,
                destination_iata,
                departure_date,
                return_date,
                observed_at::date,
                cabin_class,
                currency_code,
                payload_hash,
                offer_hash
            order by observed_at, raw_response_id
        ) as duplicate_rank
    from {{ ref('stg_flight_responses') }}
)

select
    raw_response_id,
    provider,
    endpoint,
    origin_iata,
    destination_iata,
    departure_date,
    return_date,
    total_price,
    currency_code,
    cabin_class,
    offer_id,
    airline,
    flight_number,
    departure_time,
    arrival_time,
    duration_min,
    stops,
    destination_city,
    destination_country,
    price_level,
    co2_g,
    airplane,
    offer_hash,
    is_demo,
    payload_hash,
    observed_at,
    ingested_date,
    dag_run_id
from ranked_offers
where duplicate_rank = 1
