{{ config(materialized='view') }}

with daily_route_medians as (
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
    from {{ ref('int_deduplicated_flights') }}
    where total_price is not null
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
        when offer.currency_code = 'USD' and offer.total_price < 15
            then 'usd_price_below_15'
        when history.previous_median is not null
         and history.prior_median is not null
         and history.previous_median between history.prior_median * 0.75
                                         and history.prior_median * 1.25
         and offer.total_price > history.previous_median * 4
            then 'price_spike_over_4x_stable_baseline'
        else null
    end as quality_reason
from {{ ref('int_deduplicated_flights') }} as offer
join median_history as history
  on history.provider = offer.provider
 and history.origin_iata = offer.origin_iata
 and history.destination_iata = offer.destination_iata
 and history.departure_date = offer.departure_date
 and history.return_date is not distinct from offer.return_date
 and history.observed_date = offer.observed_at::date
 and history.cabin_class = offer.cabin_class
 and history.currency_code = offer.currency_code
