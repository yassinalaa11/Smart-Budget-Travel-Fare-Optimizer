{{ config(materialized='view') }}

with responses as (
    select id, payload, payload_hash, ingested_at
    from {{ source('raw', 'api_responses') }}
    where provider = 'historical_csv_hotels' and response_status between 200 and 299
), observations as (
    select responses.*, item.value as hotel
    from responses
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(payload->'data') = 'array' then payload->'data' else '[]'::jsonb end
    ) as item(value)
)
select
    hotel->>'source' as source,
    hotel->>'hotel_id' as hotel_id,
    hotel->>'hotel_name' as hotel_name,
    upper(hotel->>'city_iata') as city_iata,
    (hotel->>'checkin_date')::date as checkin_date,
    (hotel->>'checkout_date')::date as checkout_date,
    (hotel->>'guests')::integer as guests,
    hotel->>'room_type' as room_type,
    (hotel->>'total_price')::numeric(12,2) as total_price,
    upper(hotel->>'currency') as currency_code,
    (payload->>'observed_at')::timestamptz as observed_at,
    id as raw_response_id,
    payload_hash
from observations
