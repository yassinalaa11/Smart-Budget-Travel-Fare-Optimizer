{{ config(materialized='table') }}

with observed_routes as (
    select
        origin_iata,
        destination_iata,
        max(destination_city) as destination_city
    from {{ ref('stg_flight_responses') }}
    where origin_iata is not null and destination_iata is not null
    group by origin_iata, destination_iata
)

select
    md5(origin_iata || '-' || destination_iata) as route_key,
    origin_iata || '-' || destination_iata as route_id,
    origin_iata,
    destination_iata,
    destination_city,
    true as is_active
from observed_routes
