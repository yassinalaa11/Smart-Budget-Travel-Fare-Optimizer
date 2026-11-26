{{ config(materialized='view') }}

with tracked as (
    select greatest(count(*), 15)::integer as tracked_route_count
    from {{ ref('dim_route') }}
), observed_daily as (
    select
        provider,
        observed_date as ingested_date,
        count(distinct route_id)::integer as routes_with_valid_offers,
        count(*)::integer as fact_rows,
        sum(offer_count)::integer as total_offers
    from {{ ref('fct_flight_price_observations') }}
    group by provider, observed_date
), request_audit as (
    select
        provider,
        started_at::date as ingested_date,
        count(*)::integer as total_requests,
        count(*) filter (where outcome = 'succeeded')::integer as succeeded_requests,
        count(*) filter (where outcome <> 'succeeded')::integer as failed_requests
    from {{ source('public', 'ingestion_request_log') }}
    group by provider, started_at::date
)

select
    o.provider,
    o.ingested_date,
    o.routes_with_valid_offers,
    t.tracked_route_count,
    round((o.routes_with_valid_offers::numeric / t.tracked_route_count::numeric), 4) as route_coverage_pct,
    (o.routes_with_valid_offers::numeric / t.tracked_route_count::numeric) >= 0.95 as meets_95pct_sla,
    o.fact_rows,
    o.total_offers,
    coalesce(r.total_requests, 0) as total_requests,
    coalesce(r.succeeded_requests, 0) as succeeded_requests,
    coalesce(r.failed_requests, 0) as failed_requests
from observed_daily as o
cross join tracked as t
left join request_audit as r
  on r.provider = o.provider
 and r.ingested_date = o.ingested_date
