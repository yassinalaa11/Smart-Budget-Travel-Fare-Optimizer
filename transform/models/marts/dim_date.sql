{{ config(materialized='table') }}

with relevant_dates as (
    select observed_at::date as date_value from {{ ref('stg_flight_responses') }}
    union all
    select departure_date from {{ ref('stg_flight_responses') }}
    union all
    select return_date from {{ ref('stg_flight_responses') }} where return_date is not null
    union all
    select observed_at::date from {{ ref('stg_hotel_price_observations') }}
    union all
    select checkin_date from {{ ref('stg_hotel_price_observations') }}
    union all
    select checkout_date from {{ ref('stg_hotel_price_observations') }}
), date_bounds as (
    select min(date_value) as first_date, max(date_value) as last_date
    from relevant_dates
)

select
    dates.date_value::date as date_key,
    extract(isodow from dates.date_value)::smallint as day_of_week,
    extract(isodow from dates.date_value) in (6, 7) as is_weekend,
    extract(day from dates.date_value)::smallint as day_of_month,
    extract(week from dates.date_value)::smallint as week_of_year,
    extract(month from dates.date_value)::smallint as month_num,
    to_char(dates.date_value, 'Mon') as month_name,
    extract(quarter from dates.date_value)::smallint as quarter_num,
    extract(year from dates.date_value)::smallint as year_num,
    case
        when extract(month from dates.date_value) in (12, 1, 2) then 'winter'
        when extract(month from dates.date_value) in (3, 4, 5) then 'spring'
        when extract(month from dates.date_value) in (6, 7, 8) then 'summer'
        else 'autumn'
    end as season_name
from date_bounds
cross join lateral generate_series(
    date_bounds.first_date::timestamp,
    date_bounds.last_date::timestamp,
    interval '1 day'
) as dates(date_value)
