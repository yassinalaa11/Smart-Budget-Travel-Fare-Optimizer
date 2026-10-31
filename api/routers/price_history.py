from datetime import date

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import DatabasePool
from api.schemas import (
    DepartureOption,
    PriceHistoryPoint,
    PriceHistoryResponse,
    RouteSummary,
)

router = APIRouter(tags=["price history"])


@router.get("/routes", response_model=list[RouteSummary], summary="List routes with price data")
async def list_routes(db: DatabasePool) -> list[RouteSummary]:
    query = """SELECT route_id, origin_iata, destination_iata,
                      count(DISTINCT departure_date)::integer AS departure_count,
                      bool_and(all_data_is_demo) AS all_data_is_demo
               FROM analytics_marts.fct_flight_price_observations
               GROUP BY route_id, origin_iata, destination_iata
               ORDER BY route_id"""
    try:
        async with db.acquire() as connection:
            records = await connection.fetch(query)
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Price history is not built yet. Run the dbt models first.",
        ) from exc
    return [RouteSummary.model_validate(dict(record)) for record in records]


@router.get(
    "/routes/{route_id}/departures",
    response_model=list[DepartureOption],
    summary="List departure and return dates with price data",
)
async def list_departures(route_id: str, db: DatabasePool) -> list[DepartureOption]:
    query = """SELECT DISTINCT departure_date, return_date
               FROM analytics_marts.fct_flight_price_observations
               WHERE route_id = $1
               ORDER BY departure_date, return_date"""
    try:
        async with db.acquire() as connection:
            records = await connection.fetch(query, route_id.upper())
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Price history is not built yet. Run the dbt models first.",
        ) from exc
    return [DepartureOption.model_validate(dict(record)) for record in records]


@router.get(
    "/routes/{route_id}/price-history",
    response_model=PriceHistoryResponse,
    summary="Get observed price history for a route and departure date",
)
async def get_price_history(
    route_id: str,
    departure_date: date,
    db: DatabasePool,
    return_date: date | None = None,
    limit: int = Query(default=180, ge=1, le=365),
) -> PriceHistoryResponse:
    normalized_route_id = route_id.upper()
    query = """SELECT observed_date, departure_date, return_date,
                      min_total_price, median_total_price, currency_code,
                      offer_count, response_count, contains_demo_data, all_data_is_demo
               FROM analytics_marts.fct_flight_price_observations
               WHERE route_id = $1 AND departure_date = $2
                 AND ($3::date IS NULL OR return_date = $3)
               ORDER BY observed_date DESC
               LIMIT $4"""
    try:
        async with db.acquire() as connection:
            records = await connection.fetch(
                query,
                normalized_route_id,
                departure_date,
                return_date,
                limit,
            )
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Price history is not built yet. Run the dbt models first.",
        ) from exc

    if not records:
        raise HTTPException(status_code=404, detail="No price history found for this query")

    points = [PriceHistoryPoint.model_validate(dict(record)) for record in reversed(records)]
    return PriceHistoryResponse(route_id=normalized_route_id, points=points)
