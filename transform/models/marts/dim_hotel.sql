{{ config(materialized='table') }}

with hotel_attributes as (
    select
        source,
        hotel_id,
        max(hotel_name) as hotel_name,
        max(city_iata) as city_iata,
        max(city_name) as city_name,
        max(room_type) as room_type,
        max(hotel_class) as hotel_class,
        max(rating) as rating
    from {{ ref('stg_hotel_price_observations') }}
    where hotel_id is not null
    group by source, hotel_id
)

select
    md5(source || ':' || hotel_id) as hotel_key,
    source,
    hotel_id,
    hotel_name,
    city_iata,
    city_name,
    room_type,
    hotel_class,
    rating
from hotel_attributes
