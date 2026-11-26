from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import DatabasePool
from api.schemas import (
    BookingCurvePoint,
    BookingCurveResponse,
    TripHeatmapCell,
    TripHeatmapResponse,
)

router = APIRouter(tags=["analytics"])

DATA_DIR = Path(
    os.getenv(
        "SMARTFARE_DATA_DIR",
        "/app/data" if Path("/app/data").exists() else Path(__file__).resolve().parent.parent.parent / "data",
    )
)

_SYNTHETIC_CURVE_CACHE: dict[str, list[BookingCurvePoint]] | None = None
_SYNTHETIC_SOURCE_NAME: str = ""


def _load_synthetic_booking_curves() -> tuple[dict[str, list[BookingCurvePoint]], str]:
    global _SYNTHETIC_CURVE_CACHE, _SYNTHETIC_SOURCE_NAME
    if _SYNTHETIC_CURVE_CACHE is not None:
        return _SYNTHETIC_CURVE_CACHE, _SYNTHETIC_SOURCE_NAME

    hist_path = DATA_DIR / "historical_flights_3yrs.csv"
    calib_path = DATA_DIR / "synthetic_flights_calibrated.csv"
    if hist_path.exists():
        raw = pd.read_csv(hist_path)
        obs = pd.to_datetime(raw["collected_at"], utc=True, format="mixed").dt.tz_localize(None).dt.normalize()
        dep = pd.to_datetime(raw["flight_date"]).dt.normalize()
        price_col = "price_usd" if "price_usd" in raw.columns and raw["price_usd"].notna().any() else "price_egp"
        df = pd.DataFrame({
            "route_id": raw["origin"].astype(str).str.upper() + "-" + raw["destination"].astype(str).str.upper(),
            "observed_date": obs,
            "departure_date": dep,
            "price": pd.to_numeric(raw[price_col], errors="coerce"),
        }).dropna()
        df = df[df["price"] > 0]
        df = df.groupby(["route_id", "observed_date", "departure_date"], as_index=False)["price"].min()
        df["days_to_departure"] = (df["departure_date"] - df["observed_date"]).dt.days
        source_name = hist_path.name
    elif calib_path.exists():
        raw = pd.read_csv(calib_path)
        df = pd.DataFrame({
            "route_id": raw["origin"].astype(str).str.upper() + "-" + raw["destination"].astype(str).str.upper(),
            "days_to_departure": pd.to_numeric(raw["days_to_departure"], errors="coerce"),
            "price": pd.to_numeric(raw["price"], errors="coerce"),
        }).dropna()
        df = df[df["price"] > 0]
        source_name = calib_path.name
    else:
        _SYNTHETIC_CURVE_CACHE = {}
        _SYNTHETIC_SOURCE_NAME = "none"
        return _SYNTHETIC_CURVE_CACHE, _SYNTHETIC_SOURCE_NAME

    df = df[(df["days_to_departure"] >= 0) & (df["days_to_departure"] <= 120)]
    grouped = (
        df.groupby(["route_id", "days_to_departure"])["price"]
        .agg(
            min_price_usd="min",
            p25_price_usd=lambda s: float(s.quantile(0.25)),
            median_price_usd="median",
            p75_price_usd=lambda s: float(s.quantile(0.75)),
            sample_count="count",
        )
        .reset_index()
    )

    cache: dict[str, list[BookingCurvePoint]] = {}
    for route_id, grp in grouped.groupby("route_id"):
        grp_sorted = grp.sort_values("days_to_departure")
        cache[str(route_id)] = [
            BookingCurvePoint(
                days_to_departure=int(row["days_to_departure"]),
                min_price_usd=round(float(row["min_price_usd"]), 2),
                p25_price_usd=round(float(row["p25_price_usd"]), 2),
                median_price_usd=round(float(row["median_price_usd"]), 2),
                p75_price_usd=round(float(row["p75_price_usd"]), 2),
                sample_count=int(row["sample_count"]),
            )
            for _, row in grp_sorted.iterrows()
        ]

    _SYNTHETIC_CURVE_CACHE = cache
    _SYNTHETIC_SOURCE_NAME = source_name
    return _SYNTHETIC_CURVE_CACHE, _SYNTHETIC_SOURCE_NAME


@router.get(
    "/routes/{route_id}/booking-curve",
    response_model=BookingCurveResponse,
    summary="Return 25th-75th percentile historical booking curve by days before departure",
)
async def get_booking_curve(
    route_id: str,
    db: DatabasePool,
    source: str = Query(default="auto", pattern="^(auto|warehouse|synthetic)$"),
) -> BookingCurveResponse:
    normalized = route_id.upper()
    warehouse_points: list[BookingCurvePoint] = []

    if source in ("auto", "warehouse"):
        query = """
            select
                days_to_departure::integer as days_to_departure,
                min(coalesce(min_total_price_usd, min_total_price))::float as min_price_usd,
                percentile_cont(0.25) within group (order by coalesce(min_total_price_usd, min_total_price))::float as p25_price_usd,
                percentile_cont(0.50) within group (order by coalesce(min_total_price_usd, min_total_price))::float as median_price_usd,
                percentile_cont(0.75) within group (order by coalesce(min_total_price_usd, min_total_price))::float as p75_price_usd,
                count(*)::integer as sample_count
            from analytics_marts.ml_features_flight_pricing
            where route_id = $1
              and days_to_departure between 0 and 120
            group by days_to_departure
            order by days_to_departure asc
        """
        try:
            async with db.acquire() as conn:
                rows = await conn.fetch(query, normalized)
            warehouse_points = [
                BookingCurvePoint(
                    days_to_departure=int(r["days_to_departure"]),
                    min_price_usd=round(float(r["min_price_usd"]), 2),
                    p25_price_usd=round(float(r["p25_price_usd"]), 2),
                    median_price_usd=round(float(r["median_price_usd"]), 2),
                    p75_price_usd=round(float(r["p75_price_usd"]), 2),
                    sample_count=int(r["sample_count"]),
                )
                for r in rows
            ]
        except asyncpg.UndefinedTableError:
            warehouse_points = []

    if source == "warehouse" or (source == "auto" and len(warehouse_points) >= 5):
        return BookingCurveResponse(
            route_id=normalized,
            source="warehouse_ml_features_flight_pricing",
            is_synthetic=False,
            points=warehouse_points,
        )

    synth_map, synth_name = _load_synthetic_booking_curves()
    synth_points = synth_map.get(normalized, [])
    if synth_points:
        return BookingCurveResponse(
            route_id=normalized,
            source=synth_name,
            is_synthetic=True,
            points=synth_points,
        )

    return BookingCurveResponse(
        route_id=normalized,
        source="warehouse_ml_features_flight_pricing",
        is_synthetic=False,
        points=warehouse_points,
    )


@router.get(
    "/routes/{route_id}/trip-heatmap",
    response_model=TripHeatmapResponse,
    summary="Build combined Flight + Hotel USD cost matrix across departure dates and stay lengths",
)
async def get_trip_heatmap(
    route_id: str,
    db: DatabasePool,
    stay_nights: str = Query(default="3,5,7,10"),
) -> TripHeatmapResponse:
    normalized = route_id.upper()
    parts = normalized.split("-")
    destination_iata = parts[1] if len(parts) == 2 else ""

    parsed_nights: list[int] = []
    for token in stay_nights.split(","):
        token = token.strip()
        if token.isdigit():
            val = int(token)
            if 1 <= val <= 30 and val not in parsed_nights:
                parsed_nights.append(val)
    if not parsed_nights:
        parsed_nights = [3, 5, 7, 10]
    parsed_nights.sort()

    flights_query = """
        select distinct on (departure_date)
            departure_date,
            coalesce(min_total_price_usd, min_total_price)::float as flight_price_usd
        from analytics_marts.fct_flight_price_observations
        where route_id = $1
        order by departure_date asc, observed_date desc, coalesce(min_total_price_usd, min_total_price) asc
        limit 24
    """
    hotel_by_date_query = """
        select
            checkin_date,
            percentile_cont(0.5) within group (
                order by coalesce(min_total_price_usd, min_total_price) / greatest((checkout_date - checkin_date), 1)
            )::float as nightly_usd
        from analytics_marts.fct_hotel_price_observations
        where city_iata = $1
        group by checkin_date
    """
    hotel_city_median_query = """
        select
            percentile_cont(0.5) within group (
                order by coalesce(min_total_price_usd, min_total_price) / greatest((checkout_date - checkin_date), 1)
            )::float as nightly_usd
        from analytics_marts.fct_hotel_price_observations
        where city_iata = $1
    """
    try:
        async with db.acquire() as conn:
            flights = await conn.fetch(flights_query, normalized)
            hotel_dates = await conn.fetch(hotel_by_date_query, destination_iata)
            city_fallback = await conn.fetchval(hotel_city_median_query, destination_iata)
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Warehouse tables are not built yet. Run dbt first.") from exc

    nightly_by_date = {r["checkin_date"]: float(r["nightly_usd"]) for r in hotel_dates if r["nightly_usd"] is not None}
    fallback_nightly = float(city_fallback) if city_fallback is not None else None

    cells: list[TripHeatmapCell] = []
    for f in flights:
        dep_date = f["departure_date"]
        flight_usd = round(float(f["flight_price_usd"]), 2)
        if dep_date in nightly_by_date:
            nightly = round(nightly_by_date[dep_date], 2)
            match_type = "checkin_date"
        elif fallback_nightly is not None:
            nightly = round(fallback_nightly, 2)
            match_type = "city_median_fallback"
        else:
            nightly = None
            match_type = "none"

        for nights in parsed_nights:
            hotel_total = round(nightly * nights, 2) if nightly is not None else None
            total_trip = round(flight_usd + (hotel_total or 0.0), 2)
            cells.append(
                TripHeatmapCell(
                    departure_date=dep_date,
                    stay_nights=nights,
                    flight_price_usd=flight_usd,
                    hotel_nightly_usd=nightly,
                    hotel_total_usd=hotel_total,
                    total_trip_usd=total_trip,
                    hotel_match_type=match_type,
                )
            )

    return TripHeatmapResponse(
        route_id=normalized,
        destination_iata=destination_iata,
        stay_nights_options=parsed_nights,
        cells=cells,
    )
