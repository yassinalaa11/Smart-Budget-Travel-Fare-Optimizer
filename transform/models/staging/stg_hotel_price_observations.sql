{{ config(materialized='view') }}

with responses as (
    select id, payload, payload_hash, ingested_at
    from {{ source('raw', 'api_responses') }}
    where provider in ('historical_csv_hotels', 'serpapi_snapshot')
      and response_status between 200 and 299
      and endpoint in ('/historical/hotels', '/google_hotels/csv_snapshot')
), observations as (
    select responses.*, item.value as hotel
    from responses
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(payload->'data') = 'array' then payload->'data' else '[]'::jsonb end
    ) as item(value)
)
select
    coalesce(hotel->>'source', 'historical_csv_hotels') as source,
    hotel->>'hotel_id' as hotel_id,
    hotel->>'hotel_name' as hotel_name,
    upper(hotel->>'city_iata') as city_iata,
    (hotel->>'checkin_date')::date as checkin_date,
    (hotel->>'checkout_date')::date as checkout_date,
    (hotel->>'guests')::integer as guests,
    hotel->>'room_type' as room_type,
    hotel->>'city' as city_name,
    nullif(hotel->>'hotel_class', '')::numeric(4,1) as hotel_class,
    nullif(hotel->>'rating', '')::numeric(4,2) as rating,
    nullif(hotel->>'reviews', '')::integer as review_count,
    nullif(hotel->>'nightly_price', '')::numeric(12,2) as nightly_price,
    hotel->>'free_cancellation' as free_cancellation,
    hotel->>'amenities' as amenities,
    (hotel->>'total_price')::numeric(12,2) as total_price,
    upper(hotel->>'currency') as currency_code,
    (payload->>'observed_at')::timestamptz as observed_at,
    id as raw_response_id,
    payload_hash
from observations
