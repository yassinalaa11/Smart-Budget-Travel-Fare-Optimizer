{{ config(materialized='table') }}

with base as (
    select
        f.provider,
        f.route_id,
        f.origin_iata,
        f.destination_iata,
        f.departure_date,
        f.return_date,
        f.observed_date,
        f.cabin_class,
        f.currency_code,
        f.min_total_price,
        f.median_total_price,
        f.offer_count,
        f.all_data_is_demo,
        (f.departure_date - f.observed_date)::integer as days_to_departure,
        case
            when (f.departure_date - f.observed_date) <= 7 then '0-7d'
            when (f.departure_date - f.observed_date) <= 14 then '8-14d'
            when (f.departure_date - f.observed_date) <= 21 then '15-21d'
            when (f.departure_date - f.observed_date) <= 45 then '22-45d'
            when (f.departure_date - f.observed_date) <= 90 then '46-90d'
            else '90d+'
        end as lead_time_bucket,
        d.day_of_week as departure_day_of_week,
        d.is_weekend as departure_is_weekend,
        d.month_num as departure_month,
        d.season_name as departure_season
    from {{ ref('fct_flight_price_observations') }} as f
    left join {{ ref('dim_date') }} as d
      on d.date_key = f.departure_date
    where f.departure_date >= f.observed_date
), windowed as (
    select
        *,
        avg(min_total_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between 6 preceding and current row
        )::numeric(12, 2) as rolling_mean_7d,
        avg(min_total_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between 13 preceding and current row
        )::numeric(12, 2) as rolling_mean_14d,
        stddev_samp(min_total_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between 6 preceding and current row
        )::numeric(12, 2) as rolling_std_7d,
        lag(min_total_price, 3) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
        ) as price_3d_ago,
        lag(min_total_price, 7) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
        ) as price_7d_ago,
        min(min_total_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between unbounded preceding and current row
        ) as historical_min_price
    from base
)

select
    provider,
    route_id,
    origin_iata,
    destination_iata,
    departure_date,
    return_date,
    observed_date,
    cabin_class,
    currency_code,
    min_total_price,
    median_total_price,
    offer_count,
    all_data_is_demo,
    days_to_departure,
    lead_time_bucket,
    departure_day_of_week,
    departure_is_weekend,
    departure_month,
    departure_season,
    rolling_mean_7d,
    rolling_mean_14d,
    rolling_std_7d,
    case
        when price_3d_ago > 0 then round((min_total_price - price_3d_ago) / price_3d_ago, 4)
        else null
    end as price_velocity_3d,
    case
        when price_7d_ago > 0 then round((min_total_price - price_7d_ago) / price_7d_ago, 4)
        else null
    end as price_velocity_7d,
    case
        when historical_min_price > 0 then round(min_total_price / historical_min_price, 4)
        else null
    end as markup_over_historical_min
from windowed
