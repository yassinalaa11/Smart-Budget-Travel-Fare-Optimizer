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
), generated_dates as (
    select dates.date_value::date as date_key
    from date_bounds
    cross join lateral generate_series(
        date_bounds.first_date::timestamp,
        date_bounds.last_date::timestamp,
        interval '1 day'
    ) as dates(date_value)
    where date_bounds.first_date is not null
      and date_bounds.last_date is not null
), years as (
    select distinct extract(year from date_key)::integer as y
    from generated_dates
    union
    select extract(year from current_date)::integer
    union
    select extract(year from current_date)::integer + 1
), fixed_holidays as (
    select make_date(y, m, d) as holiday_date, scope, holiday_name
    from years
    cross join (
        values
            (1, 1, 'destination', 'New Year Day'),
            (1, 7, 'origin', 'Coptic Christmas'),
            (1, 25, 'origin', 'Revolution Day'),
            (4, 25, 'origin', 'Sinai Liberation Day'),
            (5, 1, 'both', 'Labour Day'),
            (6, 30, 'origin', 'June 30 Revolution'),
            (7, 4, 'destination', 'US Independence Day'),
            (7, 14, 'destination', 'Bastille Day'),
            (7, 23, 'origin', 'July 23 Revolution'),
            (10, 6, 'origin', 'Armed Forces Day'),
            (10, 29, 'destination', 'Turkish Republic Day'),
            (12, 2, 'destination', 'UAE National Day'),
            (12, 25, 'destination', 'Christmas Day'),
            (12, 26, 'destination', 'Boxing Day')
    ) as h(m, d, scope, holiday_name)
), movable_holidays as (
    select holiday_date::date as holiday_date, scope, holiday_name
    from (
        values
            ('2025-03-30', 'both', 'Eid al-Fitr'),
            ('2025-03-31', 'both', 'Eid al-Fitr'),
            ('2025-04-20', 'destination', 'Easter Sunday'),
            ('2025-04-21', 'origin', 'Sham El-Nessim'),
            ('2025-06-06', 'both', 'Eid al-Adha'),
            ('2025-06-07', 'both', 'Eid al-Adha'),
            ('2025-06-26', 'both', 'Islamic New Year'),
            ('2025-09-04', 'origin', 'Prophet Birthday'),
            ('2026-03-20', 'both', 'Eid al-Fitr'),
            ('2026-03-21', 'both', 'Eid al-Fitr'),
            ('2026-04-05', 'destination', 'Easter Sunday'),
            ('2026-04-13', 'origin', 'Sham El-Nessim'),
            ('2026-05-27', 'both', 'Eid al-Adha'),
            ('2026-05-28', 'both', 'Eid al-Adha'),
            ('2026-06-16', 'both', 'Islamic New Year'),
            ('2026-08-25', 'origin', 'Prophet Birthday'),
            ('2026-11-26', 'destination', 'US Thanksgiving'),
            ('2027-03-09', 'both', 'Eid al-Fitr'),
            ('2027-03-10', 'both', 'Eid al-Fitr'),
            ('2027-03-28', 'destination', 'Easter Sunday'),
            ('2027-05-03', 'origin', 'Sham El-Nessim'),
            ('2027-05-16', 'both', 'Eid al-Adha'),
            ('2027-05-17', 'both', 'Eid al-Adha'),
            ('2027-06-06', 'both', 'Islamic New Year'),
            ('2027-08-15', 'origin', 'Prophet Birthday'),
            ('2027-11-25', 'destination', 'US Thanksgiving')
    ) as mh(holiday_date, scope, holiday_name)
), all_holidays as (
    select holiday_date, scope, holiday_name from fixed_holidays
    union all
    select holiday_date, scope, holiday_name from movable_holidays
)

select
    gd.date_key,
    extract(isodow from gd.date_key)::smallint as day_of_week,
    extract(isodow from gd.date_key) in (6, 7) as is_weekend,
    extract(day from gd.date_key)::smallint as day_of_month,
    extract(doy from gd.date_key)::smallint as day_of_year,
    extract(week from gd.date_key)::smallint as week_of_year,
    extract(month from gd.date_key)::smallint as month_num,
    to_char(gd.date_key, 'Mon') as month_name,
    extract(quarter from gd.date_key)::smallint as quarter_num,
    extract(year from gd.date_key)::smallint as year_num,
    case
        when extract(month from gd.date_key) in (12, 1, 2) then 'winter'
        when extract(month from gd.date_key) in (3, 4, 5) then 'spring'
        when extract(month from gd.date_key) in (6, 7, 8) then 'summer'
        else 'autumn'
    end as season_name,
    exists (
        select 1
        from all_holidays as h
        where h.holiday_date = gd.date_key
          and h.scope in ('origin', 'both')
    ) as is_origin_public_holiday,
    exists (
        select 1
        from all_holidays as h
        where h.holiday_date = gd.date_key
          and h.scope in ('destination', 'both')
    ) as is_destination_public_holiday,
    (
        (extract(month from gd.date_key) = 12 and extract(day from gd.date_key) >= 20)
        or (extract(month from gd.date_key) = 1 and extract(day from gd.date_key) <= 10)
        or (extract(month from gd.date_key) = 1 and extract(day from gd.date_key) >= 24)
        or (extract(month from gd.date_key) = 2 and extract(day from gd.date_key) <= 7)
        or (extract(month from gd.date_key) = 6 and extract(day from gd.date_key) >= 15)
        or extract(month from gd.date_key) in (7, 8)
        or (extract(month from gd.date_key) = 9 and extract(day from gd.date_key) <= 15)
    ) as school_break_flag,
    nh.days_to_nearest_holiday
from generated_dates as gd
left join lateral (
    select min(abs(gd.date_key - h.holiday_date))::integer as days_to_nearest_holiday
    from all_holidays as h
) as nh on true
