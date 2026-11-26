{{ config(materialized='view') }}

with offers_with_fx as (
    select
        offer.*,
        fx.rate as fx_rate_to_usd,
        case
            when offer.currency_code = 'USD' then offer.total_price::numeric(12, 2)
            when fx.rate is not null and fx.rate > 0
                then round((offer.total_price / fx.rate)::numeric, 2)
            else null
        end as total_price_usd
    from {{ ref('int_deduplicated_flights') }} as offer
    left join lateral (
        select r.rate
        from {{ ref('stg_fx_rates') }} as r
        where r.quote_currency = offer.currency_code
        order by
            case when r.rate_date <= offer.observed_at::date then 0 else 1 end,
            abs(r.rate_date - offer.observed_at::date)
        limit 1
    ) as fx on true
), daily_route_medians as (
    select
        provider,
        origin_iata,
        destination_iata,
        departure_date,
        return_date,
        observed_at::date as observed_date,
        cabin_class,
        currency_code,
        percentile_cont(0.5) within group (order by total_price)::numeric
            as median_price
    from offers_with_fx
    where total_price is not null and total_price > 0
    group by
        provider,
        origin_iata,
        destination_iata,
        departure_date,
        return_date,
        observed_at::date,
        cabin_class,
        currency_code
), median_history as (
    select
        *,
        lag(median_price, 1) over (
            partition by provider, origin_iata, destination_iata, departure_date,
                         return_date, cabin_class, currency_code
            order by observed_date
        ) as previous_median,
        lag(median_price, 2) over (
            partition by provider, origin_iata, destination_iata, departure_date,
                         return_date, cabin_class, currency_code
            order by observed_date
        ) as prior_median
    from daily_route_medians
)

select
    offer.*,
    history.median_price as observed_day_median,
    history.previous_median,
    history.prior_median,
    case
        when offer.total_price is null or offer.total_price <= 0
            then 'non_positive_price'
        when offer.return_date is not null and offer.return_date < offer.departure_date
            then 'return_before_departure'
        when (offer.total_price_usd is not null and offer.total_price_usd < 15)
          or (offer.currency_code = 'USD' and offer.total_price < 15)
            then 'price_usd_below_15'
        when history.previous_median is not null
         and history.prior_median is not null
         and history.previous_median between history.prior_median * 0.75
                                         and history.prior_median * 1.25
         and offer.total_price > history.previous_median * 4
            then 'price_spike_over_4x_stable_baseline'
        else null
    end as quality_reason
from offers_with_fx as offer
left join median_history as history
  on history.provider = offer.provider
 and history.origin_iata = offer.origin_iata
 and history.destination_iata = offer.destination_iata
 and history.departure_date = offer.departure_date
 and history.return_date is not distinct from offer.return_date
 and history.observed_date = offer.observed_at::date
 and history.cabin_class = offer.cabin_class
 and history.currency_code = offer.currency_code
