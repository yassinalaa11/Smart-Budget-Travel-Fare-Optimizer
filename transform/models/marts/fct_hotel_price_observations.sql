{{ config(
    materialized='incremental',
    unique_key='hotel_observation_key',
    incremental_strategy='delete+insert',
    on_schema_change='sync_all_columns'
) }}

with hotel_prices as (
    select
        md5(concat_ws(
            '|',
            source,
            city_iata,
            hotel_id,
            checkin_date::text,
            checkout_date::text,
            (observed_at::date)::text,
            coalesce(guests::text, 'ANY'),
            currency_code
        )) as hotel_observation_key,
        source,
        city_iata,
        hotel_id,
        checkin_date,
        checkout_date,
        observed_at::date as observed_date,
        guests,
        currency_code,
        min(total_price)::numeric(12, 2) as min_total_price,
        percentile_cont(0.5) within group (order by total_price)::numeric(12, 2) as median_total_price,
        count(*)::integer as observation_count
    from {{ ref('stg_hotel_price_observations') }}
    where hotel_id is not null
      and checkin_date < checkout_date
      and total_price > 0
    {% if is_incremental() %}
      and observed_at::date >= (
          select coalesce(max(observed_date) - 3, date '1900-01-01')
          from {{ this }}
      )
    {% endif %}
    group by
        source,
        city_iata,
        hotel_id,
        checkin_date,
        checkout_date,
        observed_at::date,
        guests,
        currency_code
)

select
    hotel_prices.*,
    fx.rate as fx_rate_to_usd,
    case
        when hotel_prices.currency_code = 'USD' then hotel_prices.min_total_price
        when fx.rate is not null and fx.rate > 0
            then round((hotel_prices.min_total_price / fx.rate)::numeric, 2)
        else null
    end as min_total_price_usd,
    case
        when hotel_prices.currency_code = 'USD' then hotel_prices.median_total_price
        when fx.rate is not null and fx.rate > 0
            then round((hotel_prices.median_total_price / fx.rate)::numeric, 2)
        else null
    end as median_total_price_usd,
    hotels.hotel_key,
    observed_dates.date_key as observed_date_key,
    checkin_dates.date_key as checkin_date_key,
    checkout_dates.date_key as checkout_date_key
from hotel_prices
left join lateral (
    select r.rate
    from {{ ref('stg_fx_rates') }} as r
    where r.quote_currency = hotel_prices.currency_code
    order by
        case when r.rate_date <= hotel_prices.observed_date then 0 else 1 end,
        abs(r.rate_date - hotel_prices.observed_date)
    limit 1
) as fx on true
left join {{ ref('dim_hotel') }} as hotels
  using (source, hotel_id)
left join {{ ref('dim_date') }} as observed_dates
  on observed_dates.date_key = hotel_prices.observed_date
left join {{ ref('dim_date') }} as checkin_dates
  on checkin_dates.date_key = hotel_prices.checkin_date
left join {{ ref('dim_date') }} as checkout_dates
  on checkout_dates.date_key = hotel_prices.checkout_date
