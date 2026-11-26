{{ config(materialized='view') }}

with source_rates as (
    select
        rate_date,
        upper(trim(base_currency)) as base_currency,
        upper(trim(quote_currency)) as quote_currency,
        rate::numeric(18, 6) as rate,
        source,
        fetched_at
    from {{ source('public', 'fx_rates') }}
    where upper(trim(base_currency)) = 'USD'
      and rate > 0
), default_usd as (
    select
        date '2000-01-01' as rate_date,
        'USD'::text as base_currency,
        'USD'::text as quote_currency,
        1.000000::numeric(18, 6) as rate,
        'system_default'::text as source,
        timestamp with time zone '2000-01-01 00:00:00+00' as fetched_at
    where not exists (
        select 1
        from source_rates
        where quote_currency = 'USD'
    )
)

select * from source_rates
union all
select * from default_usd
