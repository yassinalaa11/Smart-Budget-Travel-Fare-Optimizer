{{ config(materialized='view') }}

select *
from {{ ref('int_flight_price_quality') }}
where quality_reason is not null
