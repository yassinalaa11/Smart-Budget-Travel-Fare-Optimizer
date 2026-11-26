{{ config(materialized='table') }}

with base as (
    select
        f.flight_observation_key,
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
        f.min_total_price_usd,
        f.median_total_price_usd,
        coalesce(f.min_total_price_usd, f.min_total_price) as normalized_price,
        f.cheapest_direct_price,
        f.cheapest_1stop_price,
        f.cheapest_direct_price_usd,
        f.cheapest_1stop_price_usd,
        f.active_carrier_count,
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
        coalesce(d.day_of_week, extract(isodow from f.departure_date)::smallint) as departure_day_of_week,
        coalesce(d.day_of_year, extract(doy from f.departure_date)::smallint) as departure_day_of_year,
        coalesce(d.is_weekend, extract(isodow from f.departure_date) in (6, 7)) as departure_is_weekend,
        coalesce(d.month_num, extract(month from f.departure_date)::smallint) as departure_month,
        d.season_name as departure_season,
        coalesce(d.is_origin_public_holiday, false) as is_origin_public_holiday,
        coalesce(d.is_destination_public_holiday, false) as is_destination_public_holiday,
        coalesce(d.school_break_flag, false) as school_break_flag,
        d.days_to_nearest_holiday,
        coalesce(d.day_of_week = 5 and rd.day_of_week = 7, false) as is_friday_sunday_pair,
        round(sin(2 * pi() * coalesce(d.day_of_year, extract(doy from f.departure_date)) / 365.25)::numeric, 6) as day_of_year_sin,
        round(cos(2 * pi() * coalesce(d.day_of_year, extract(doy from f.departure_date)) / 365.25)::numeric, 6) as day_of_year_cos,
        round(sin(2 * pi() * coalesce(d.day_of_week, extract(isodow from f.departure_date)) / 7.0)::numeric, 6) as day_of_week_sin,
        round(cos(2 * pi() * coalesce(d.day_of_week, extract(isodow from f.departure_date)) / 7.0)::numeric, 6) as day_of_week_cos
    from {{ ref('fct_flight_price_observations') }} as f
    left join {{ ref('dim_date') }} as d
      on d.date_key = f.departure_date
    left join {{ ref('dim_date') }} as rd
      on rd.date_key = f.return_date
    where f.departure_date >= f.observed_date
), windowed as (
    select
        *,
        avg(normalized_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between 6 preceding and current row
        )::numeric(12, 2) as rolling_mean_7d,
        avg(normalized_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between 13 preceding and current row
        )::numeric(12, 2) as rolling_mean_14d,
        stddev_samp(normalized_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between 6 preceding and current row
        )::numeric(12, 2) as rolling_std_7d,
        lag(normalized_price, 3) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
        ) as price_3d_ago,
        lag(normalized_price, 7) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
        ) as price_7d_ago,
        min(normalized_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between unbounded preceding and current row
        ) as historical_min_price,
        max(normalized_price) over (
            partition by provider, route_id, departure_date, return_date, cabin_class, currency_code
            order by observed_date
            rows between unbounded preceding and current row
        ) as historical_max_price,
        avg(normalized_price) over (
            partition by route_id, departure_month, lead_time_bucket
        ) as route_horizon_mean,
        stddev_samp(normalized_price) over (
            partition by route_id, departure_month, lead_time_bucket
        ) as route_horizon_std
    from base
)

select
    flight_observation_key,
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
    min_total_price_usd,
    median_total_price_usd,
    cheapest_direct_price_usd,
    cheapest_1stop_price_usd,
    case
        when cheapest_direct_price is not null and cheapest_1stop_price is not null and cheapest_1stop_price > 0
            then round(((cheapest_direct_price - cheapest_1stop_price) / cheapest_1stop_price)::numeric, 4)
        else null
    end as direct_vs_1stop_spread_pct,
    active_carrier_count,
    offer_count,
    all_data_is_demo,
    days_to_departure,
    lead_time_bucket,
    departure_day_of_week,
    departure_day_of_year,
    departure_is_weekend,
    departure_month,
    departure_season,
    is_friday_sunday_pair,
    is_origin_public_holiday,
    is_destination_public_holiday,
    school_break_flag,
    days_to_nearest_holiday,
    day_of_year_sin,
    day_of_year_cos,
    day_of_week_sin,
    day_of_week_cos,
    rolling_mean_7d,
    rolling_mean_14d,
    rolling_std_7d,
    case
        when price_3d_ago > 0 then round(((normalized_price - price_3d_ago) / price_3d_ago)::numeric, 4)
        else null
    end as price_velocity_3d,
    case
        when price_7d_ago > 0 then round(((normalized_price - price_7d_ago) / price_7d_ago)::numeric, 4)
        else null
    end as price_velocity_7d,
    case
        when route_horizon_std is not null and route_horizon_std > 0
            then round(((normalized_price - route_horizon_mean) / route_horizon_std)::numeric, 4)
        else 0::numeric(10, 4)
    end as z_score_vs_route_horizon,
    case
        when historical_max_price > 0
            then round(((historical_max_price - normalized_price) / historical_max_price)::numeric, 4)
        else null
    end as drawdown_from_peak,
    case
        when historical_min_price > 0
            then round((normalized_price / historical_min_price)::numeric, 4)
        else null
    end as markup_over_historical_min
from windowed
