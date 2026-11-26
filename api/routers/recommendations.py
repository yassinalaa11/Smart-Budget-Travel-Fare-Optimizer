from datetime import date
from decimal import Decimal

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import DatabasePool
from api.schemas import (
    CheapestWindowItem,
    CheapestWindowsResponse,
    RecommendationResponse,
)

router = APIRouter(tags=["recommendations"])


@router.get(
    "/routes/{route_id}/recommendation",
    response_model=RecommendationResponse,
    summary="Get Book Now / Wait / Neutral baseline recommendation for a route",
)
async def get_recommendation(
    route_id: str,
    departure_date: date,
    db: DatabasePool,
    return_date: date | None = None,
) -> RecommendationResponse:
    normalized_route_id = route_id.upper()
    latest_query = """
        select observed_date, min_total_price, currency_code, contains_demo_data
        from analytics_marts.fct_flight_price_observations
        where route_id = $1
          and departure_date = $2
          and return_date is not distinct from $3::date
        order by observed_date desc
        limit 1
    """
    stats_query = """
        select
            count(distinct observed_date)::integer as observation_days,
            percentile_cont(0.20) within group (order by min_total_price)::numeric(12, 2) as p20,
            percentile_cont(0.50) within group (order by min_total_price)::numeric(12, 2) as p50,
            percentile_cont(0.80) within group (order by min_total_price)::numeric(12, 2) as p80,
            count(*) filter (where min_total_price <= $3)::float / nullif(count(*), 0) as pct_rank
        from analytics_marts.fct_flight_price_observations
        where route_id = $1
          and currency_code = $2
    """
    try:
        async with db.acquire() as conn:
            latest = await conn.fetchrow(latest_query, normalized_route_id, departure_date, return_date)
            if not latest:
                raise HTTPException(status_code=404, detail="No price observations found for this route/date.")
            stats = await conn.fetchrow(
                stats_query,
                normalized_route_id,
                latest["currency_code"],
                latest["min_total_price"],
            )
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Warehouse tables are not built yet. Run dbt first.") from exc

    current_price: Decimal = latest["min_total_price"]
    observed_date: date = latest["observed_date"]
    days_to_departure = (departure_date - observed_date).days
    pct_rank = round(float(stats["pct_rank"] or 0.5), 2)
    obs_days = int(stats["observation_days"] or 1)

    if days_to_departure <= 14:
        signal = "BOOK_NOW"
        reason = (
            f"Departure is in {days_to_departure} days (inside the 14-day advance-purchase cliff). "
            "Fares typically rise sharply from here."
        )
    elif current_price <= stats["p20"]:
        signal = "BOOK_NOW"
        reason = (
            f"Current fare ({latest['currency_code']} {current_price}) is in the bottom 20% "
            f"of observed prices for {normalized_route_id}."
        )
    elif current_price >= stats["p80"] and days_to_departure > 21:
        signal = "WAIT"
        reason = (
            f"Current fare is above the 80th percentile ({latest['currency_code']} {stats['p80']}) "
            f"with {days_to_departure} days left before departure."
        )
    else:
        signal = "NEUTRAL"
        reason = (
            f"Current fare sits near the route median ({latest['currency_code']} {stats['p50']}). "
            "Lock in if your dates are fixed, or monitor for a dip."
        )

    confidence = "high" if obs_days >= 14 else ("medium" if obs_days >= 5 else "low (cold-start baseline)")

    return RecommendationResponse(
        route_id=normalized_route_id,
        departure_date=departure_date,
        return_date=return_date,
        currency_code=latest["currency_code"],
        current_price=current_price,
        route_p20_price=stats["p20"],
        route_median_price=stats["p50"],
        route_p80_price=stats["p80"],
        percentile_rank=pct_rank,
        days_to_departure=days_to_departure,
        observation_days=obs_days,
        signal=signal,
        confidence=confidence,
        reason=reason,
        contains_demo_data=bool(latest["contains_demo_data"]),
    )


@router.get(
    "/routes/{route_id}/cheapest-windows",
    response_model=CheapestWindowsResponse,
    summary="Rank lowest-cost travel windows combining flights and hotels",
)
async def get_cheapest_windows(
    route_id: str,
    db: DatabasePool,
    limit: int = Query(default=20, ge=1, le=100),
) -> CheapestWindowsResponse:
    normalized_route_id = route_id.upper()
    parts = normalized_route_id.split("-")
    destination_iata = parts[1] if len(parts) == 2 else ""

    query = """
        with latest_flights as (
            select distinct on (departure_date, return_date)
                departure_date,
                return_date,
                min_total_price as flight_price,
                currency_code as flight_currency,
                contains_demo_data
            from analytics_marts.fct_flight_price_observations
            where route_id = $1
            order by departure_date, return_date, observed_date desc
        ), hotel_medians as (
            select
                checkin_date,
                checkout_date,
                currency_code as hotel_currency,
                percentile_cont(0.5) within group (order by min_total_price)::numeric(12, 2) as hotel_total_price
            from analytics_marts.fct_hotel_price_observations
            where city_iata = $2
            group by checkin_date, checkout_date, currency_code
        )
        select
            f.departure_date,
            f.return_date,
            f.flight_price,
            f.flight_currency,
            h.hotel_total_price,
            h.hotel_currency,
            case
                when h.hotel_total_price is not null and f.flight_currency = h.hotel_currency
                    then f.flight_price + h.hotel_total_price
                else null
            end as combined_total_price,
            f.contains_demo_data
        from latest_flights as f
        left join hotel_medians as h
          on h.checkin_date = f.departure_date
         and h.checkout_date = f.return_date
        order by coalesce(
            case when h.hotel_total_price is not null and f.flight_currency = h.hotel_currency
                 then f.flight_price + h.hotel_total_price end,
            f.flight_price
        ) asc
        limit $3
    """
    try:
        async with db.acquire() as conn:
            records = await conn.fetch(query, normalized_route_id, destination_iata, limit)
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Warehouse tables are not built yet. Run dbt first.") from exc

    windows = [CheapestWindowItem.model_validate(dict(r)) for r in records]
    return CheapestWindowsResponse(
        route_id=normalized_route_id,
        destination_iata=destination_iata,
        windows=windows,
    )
