{{ config(materialized='view') }}

with raw_responses as (
    select
        id as raw_response_id,
        provider,
        endpoint,
        request_params,
        response_status,
        payload,
        payload_hash,
        ingested_at as observed_at,
        ingested_date,
        dag_run_id
    from {{ source('raw', 'api_responses') }}
    where response_status between 200 and 299
      and provider in ('mock', 'amadeus', 'travelpayouts', 'serpapi_snapshot')
), expanded_offers as (
    select
        raw_responses.*,
        offer.value as offer
    from raw_responses
    cross join lateral jsonb_array_elements(
        case
            when jsonb_typeof(raw_responses.payload->'data') = 'array'
                then raw_responses.payload->'data'
            else '[]'::jsonb
        end
    ) as offer(value)
)

select
    raw_response_id,
    provider,
    endpoint,
    coalesce(request_params->>'origin_iata', request_params->>'origin') as origin_iata,
    coalesce(request_params->>'destination_iata', request_params->>'destination') as destination_iata,
    coalesce(
        offer->>'departure_date',
        offer->>'depart_date',
        request_params->>'departure_date'
    )::date
        as departure_date,
    coalesce(
        nullif(offer->>'return_date', ''),
        request_params->>'return_date'
    )::date
        as return_date,
    coalesce(
        nullif(offer->'price'->>'total', '')::numeric,
        nullif(offer->'price'->>'grandTotal', '')::numeric,
        nullif(offer->>'value', '')::numeric
    ) as total_price,
    upper(coalesce(
        offer->'price'->>'currency',
        request_params->>'currency'
    )) as currency_code,
    upper(coalesce(request_params->>'travel_class', 'ECONOMY')) as cabin_class,
    coalesce(offer->>'id', offer->>'found_at') as offer_id,
    offer->>'airline' as airline,
    offer->>'flight_number' as flight_number,
    offer->>'departure_time' as departure_time,
    offer->>'arrival_time' as arrival_time,
    nullif(offer->>'duration_min', '')::integer as duration_min,
    nullif(offer->>'stops', '')::integer as stops,
    offer->>'destination_city' as destination_city,
    offer->>'destination_country' as destination_country,
    offer->>'price_level' as price_level,
    nullif(offer->>'co2_g', '')::numeric as co2_g,
    offer->>'airplane' as airplane,
    md5(offer::text) as offer_hash,
    coalesce((payload->>'demo_data')::boolean, provider = 'mock') as is_demo,
    payload_hash,
    observed_at,
    ingested_date,
    dag_run_id
from expanded_offers
