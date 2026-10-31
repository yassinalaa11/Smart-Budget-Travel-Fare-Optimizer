"""Idempotently load route, hotel-market, and calendar CSV seeds into PostgreSQL."""

from __future__ import annotations

import asyncio
import csv
import os
from pathlib import Path
from typing import Any
from datetime import date

import asyncpg


SEED_DIR = Path(__file__).resolve().parent


def read_csv(filename: str) -> list[dict[str, str]]:
    with (SEED_DIR / filename).open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def optional_int(value: str) -> int | None:
    return int(value) if value else None


def optional_bool(value: str) -> bool | None:
    return value.lower() == "true" if value else None


def route_records() -> list[tuple[Any, ...]]:
    return [
        (
            row["route_id"], row["origin_iata"], row["destination_iata"],
            row["origin_city"], row["destination_city"], optional_int(row["distance_km"]),
            row["is_international"].lower() == "true",
            optional_int(row["typical_flight_duration_min"]), True,
        )
        for row in read_csv("dim_routes.csv")
    ]


def hotel_records() -> list[tuple[Any, ...]]:
    return [
        (
            row["hotel_market_id"], row["destination_iata"], row["city_name"],
            int(row["star_tier"]), row["market_category"],
            row["is_active"].lower() == "true",
        )
        for row in read_csv("dim_hotels.csv")
    ]


def calendar_records() -> list[tuple[Any, ...]]:
    return [
        (
            date.fromisoformat(row["date_key"]), int(row["day_of_week"]),
            row["is_weekend"].lower() == "true", int(row["month_num"]),
            row["season_name"], optional_bool(row["is_origin_public_holiday"]),
            optional_bool(row["is_destination_public_holiday"]),
            optional_bool(row["school_break_flag"]),
        )
        for row in read_csv("dim_calendar.csv")
    ]


async def load() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    routes = route_records()
    hotels = hotel_records()
    calendar = calendar_records()

    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            await connection.executemany(
                """INSERT INTO public.dim_routes
                   (route_id, origin_iata, destination_iata, origin_city, destination_city,
                    distance_km, is_international, typical_flight_duration_min, is_active)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (route_id) DO UPDATE SET
                     origin_iata=EXCLUDED.origin_iata,
                     destination_iata=EXCLUDED.destination_iata,
                     origin_city=EXCLUDED.origin_city,
                     destination_city=EXCLUDED.destination_city,
                     distance_km=EXCLUDED.distance_km,
                     is_international=EXCLUDED.is_international,
                     typical_flight_duration_min=EXCLUDED.typical_flight_duration_min,
                     is_active=EXCLUDED.is_active""",
                routes,
            )
            await connection.executemany(
                """INSERT INTO public.dim_hotels
                   (hotel_market_id, destination_iata, city_name, star_tier,
                    market_category, is_active)
                   VALUES ($1,$2,$3,$4,$5,$6)
                   ON CONFLICT (hotel_market_id) DO UPDATE SET
                     destination_iata=EXCLUDED.destination_iata,
                     city_name=EXCLUDED.city_name,
                     star_tier=EXCLUDED.star_tier,
                     market_category=EXCLUDED.market_category,
                     is_active=EXCLUDED.is_active""",
                hotels,
            )
            await connection.executemany(
                """INSERT INTO public.dim_calendar
                   (date_key, day_of_week, is_weekend, month_num, season_name,
                    is_origin_public_holiday, is_destination_public_holiday, school_break_flag)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (date_key) DO UPDATE SET
                     day_of_week=EXCLUDED.day_of_week,
                     is_weekend=EXCLUDED.is_weekend,
                     month_num=EXCLUDED.month_num,
                     season_name=EXCLUDED.season_name,
                     is_origin_public_holiday=EXCLUDED.is_origin_public_holiday,
                     is_destination_public_holiday=EXCLUDED.is_destination_public_holiday,
                     school_break_flag=EXCLUDED.school_break_flag""",
                calendar,
            )
        print(
            "Loaded dimensions: "
            f"{len(routes)} routes, {len(hotels)} hotel markets, "
            f"{len(calendar)} calendar dates"
        )
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(load())
