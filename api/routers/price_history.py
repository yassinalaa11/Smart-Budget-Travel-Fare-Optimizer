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
    query = """SELECT provider, observed_date, departure_date, return_date, cabin_class,
                      min_total_price, median_total_price, currency_code,
                      offer_count, response_count, contains_demo_data, all_data_is_demo
               FROM analytics_marts.fct_flight_price_observations
               WHERE route_id = $1 AND departure_date = $2
                 AND return_date IS NOT DISTINCT FROM $3::date
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


@router.get("/serpapi/flights", response_model=list[dict], summary="List SerpApi flight snapshot groups")
async def list_serpapi_flights(
    db: DatabasePool,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[dict]:
    query = """SELECT provider, route_id, origin_iata, destination_iata, departure_date,
                      return_date, observed_date, cabin_class, currency_code,
                      min_total_price, median_total_price, offer_count, response_count,
                      contains_demo_data, all_data_is_demo
               FROM analytics_marts.fct_flight_price_observations
               WHERE provider = 'serpapi_snapshot'
               ORDER BY route_id, departure_date, return_date NULLS FIRST
               LIMIT $1"""
    try:
        async with db.acquire() as connection:
            records = await connection.fetch(query, limit)
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Run dbt models before loading snapshot data.") from exc
    return [dict(record) for record in records]


@router.get("/serpapi/hotels", response_model=list[dict], summary="List SerpApi hotel snapshot prices")
async def list_serpapi_hotels(
    db: DatabasePool,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[dict]:
    query = """SELECT prices.source, prices.city_iata, details.city_name,
                      prices.hotel_id, details.hotel_name, details.room_type,
                      details.hotel_class, details.rating, details.review_count,
                      prices.checkin_date, prices.checkout_date, prices.observed_date,
                      details.nightly_price, prices.min_total_price AS total_price_egp,
                      prices.median_total_price, prices.observation_count,
                      prices.currency_code, details.free_cancellation, details.amenities
               FROM analytics_marts.fct_hotel_price_observations AS prices
               LEFT JOIN LATERAL (
                   SELECT hotel.city_name, hotel.hotel_name, hotel.room_type,
                          hotel.hotel_class, hotel.rating, hotel.review_count,
                          hotel.nightly_price, hotel.free_cancellation, hotel.amenities
                   FROM analytics_staging.stg_hotel_price_observations AS hotel
                   WHERE hotel.source = prices.source
                     AND hotel.city_iata = prices.city_iata
                     AND hotel.hotel_id = prices.hotel_id
                     AND hotel.checkin_date = prices.checkin_date
                     AND hotel.checkout_date = prices.checkout_date
                     AND hotel.observed_at::date = prices.observed_date
                     AND hotel.currency_code = prices.currency_code
                   ORDER BY hotel.raw_response_id
                   LIMIT 1
               ) AS details ON true
               WHERE prices.source = 'serpapi_google_hotels_csv'
               ORDER BY prices.city_iata, prices.min_total_price
               LIMIT $1"""
    try:
        async with db.acquire() as connection:
            records = await connection.fetch(query, limit)
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Run dbt models before loading snapshot data.") from exc
    return [dict(record) for record in records]
