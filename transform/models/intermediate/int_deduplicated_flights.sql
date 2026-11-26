{{ config(materialized='view') }}

with exact_dedup as (
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
        ) as exact_rank
    from {{ ref('stg_flight_responses') }}
), codeshare_dedup as (
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
                case
                    when departure_time is not null and arrival_time is not null
                        then concat_ws('|', departure_time, arrival_time, coalesce(duration_min::text, ''), coalesce(stops::text, ''))
                    else offer_hash
                end
            order by
                total_price asc nulls last,
                observed_at asc,
                raw_response_id asc
        ) as itinerary_rank
    from exact_dedup
    where exact_rank = 1
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
from codeshare_dedup
where itinerary_rank = 1
