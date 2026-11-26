{{ config(
    materialized='incremental',
    unique_key='flight_observation_key',
    incremental_strategy='delete+insert',
    on_schema_change='sync_all_columns'
) }}

with filtered_offers as (
    select
        *,
        min(total_price) over (
            partition by
                provider,
                origin_iata,
                destination_iata,
                departure_date,
                return_date,
                observed_at::date,
                cabin_class,
                currency_code
        ) as group_min_price
    from {{ ref('int_flight_price_quality') }}
    where origin_iata is not null
      and destination_iata is not null
      and departure_date is not null
      and total_price is not null
      and currency_code is not null
      and quality_reason is null
    {% if is_incremental() %}
      and observed_at::date >= (
          select coalesce(max(observed_date) - 3, date '1900-01-01')
          from {{ this }}
      )
    {% endif %}
), daily_prices as (
    select
        md5(concat_ws(
            '|',
            provider,
            origin_iata || '-' || destination_iata,
            departure_date::text,
            coalesce(return_date::text, 'ONEWAY'),
            (observed_at::date)::text,
            cabin_class,
            currency_code
        )) as flight_observation_key,
        provider,
        origin_iata || '-' || destination_iata as route_id,
        origin_iata,
        destination_iata,
        departure_date,
        return_date,
        observed_at::date as observed_date,
        cabin_class,
        currency_code,
        min(total_price)::numeric(12, 2) as min_total_price,
        percentile_cont(0.5) within group (order by total_price)::numeric(12, 2)
            as median_total_price,
        min(total_price) filter (where stops = 0)::numeric(12, 2)
            as cheapest_direct_price,
        min(total_price) filter (where stops = 1)::numeric(12, 2)
            as cheapest_1stop_price,
        min(duration_min)::integer as min_duration_minutes,
        count(distinct airline) filter (
            where airline is not null
              and total_price <= group_min_price * 1.15
        )::integer as active_carrier_count,
        count(*)::integer as offer_count,
        count(distinct raw_response_id)::integer as response_count,
        bool_or(is_demo) as contains_demo_data,
        bool_and(is_demo) as all_data_is_demo
    from filtered_offers
    group by
        provider,
        origin_iata,
        destination_iata,
        departure_date,
        return_date,
        observed_at::date,
        cabin_class,
        currency_code
)

select
    daily_prices.*,
    fx.rate as fx_rate_to_usd,
    case
        when daily_prices.currency_code = 'USD' then daily_prices.min_total_price
        when fx.rate is not null and fx.rate > 0
            then round((daily_prices.min_total_price / fx.rate)::numeric, 2)
        else null
    end as min_total_price_usd,
    case
        when daily_prices.currency_code = 'USD' then daily_prices.median_total_price
        when fx.rate is not null and fx.rate > 0
            then round((daily_prices.median_total_price / fx.rate)::numeric, 2)
        else null
    end as median_total_price_usd,
    case
        when daily_prices.cheapest_direct_price is null then null
        when daily_prices.currency_code = 'USD' then daily_prices.cheapest_direct_price
        when fx.rate is not null and fx.rate > 0
            then round((daily_prices.cheapest_direct_price / fx.rate)::numeric, 2)
        else null
    end as cheapest_direct_price_usd,
    case
        when daily_prices.cheapest_1stop_price is null then null
        when daily_prices.currency_code = 'USD' then daily_prices.cheapest_1stop_price
        when fx.rate is not null and fx.rate > 0
            then round((daily_prices.cheapest_1stop_price / fx.rate)::numeric, 2)
        else null
    end as cheapest_1stop_price_usd,
    routes.route_key,
    observed_dates.date_key as observed_date_key,
    departure_dates.date_key as departure_date_key,
    return_dates.date_key as return_date_key
from daily_prices
left join lateral (
    select r.rate
    from {{ ref('stg_fx_rates') }} as r
    where r.quote_currency = daily_prices.currency_code
    order by
        case when r.rate_date <= daily_prices.observed_date then 0 else 1 end,
        abs(r.rate_date - daily_prices.observed_date)
    limit 1
) as fx on true
left join {{ ref('dim_route') }} as routes using (route_id)
left join {{ ref('dim_date') }} as observed_dates
  on observed_dates.date_key = daily_prices.observed_date
left join {{ ref('dim_date') }} as departure_dates
  on departure_dates.date_key = daily_prices.departure_date
left join {{ ref('dim_date') }} as return_dates
  on return_dates.date_key = daily_prices.return_date
