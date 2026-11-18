"""Import the project's SerpApi CSV snapshots into the raw PostgreSQL layer.

These files are single collection snapshots. Importing them preserves the
collected offers for transformation, but does not create historical price data.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg


def parse_observed_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def optional_decimal(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    try:
        number = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value {value!r}") from exc
    return format(number, "f")


def optional_integer(value: str | None) -> int | None:
    if not value or not value.strip():
        return None
    try:
        number = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid integer value {value!r}") from exc
    if number != number.to_integral_value():
        raise ValueError(f"expected a whole number, got {value!r}")
    return int(number)


def read_rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source_file:
        reader = csv.DictReader(source_file)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
        return list(reader)


def flight_payload(row: dict[str, str]) -> tuple[datetime, dict[str, Any], dict[str, Any]]:
    observed_at = parse_observed_at(row["collected_at"])
    departure_date = datetime.fromisoformat(row["flight_date"].strip()).date().isoformat()
    price = optional_decimal(row.get("price_egp"))
    offer = {
        "id": row.get("flight_number") or None,
        "departure_date": departure_date,
        "return_date": None,
        "price": {"total": price, "currency": "EGP"},
        "airline": row.get("airline") or None,
        "flight_number": row.get("flight_number") or None,
        "departure_time": row.get("departure_time") or None,
        "arrival_time": row.get("arrival_time") or None,
        "duration_min": int(row["duration_min"]) if row.get("duration_min", "").strip() else None,
        "stops": int(row["stops"]) if row.get("stops", "").strip() else None,
        "layovers": row.get("layovers") or None,
        "destination_city": row.get("destination_city") or None,
        "destination_country": row.get("destination_country") or None,
        "price_level": row.get("price_level") or None,
        "airplane": row.get("airplane") or None,
        "travel_class": row.get("travel_class") or None,
        "co2_g": optional_decimal(row.get("co2_g")),
        "serpapi_source_record": row,
    }
    params = {
        "origin": row["origin"].strip().upper(),
        "destination": row["destination"].strip().upper(),
        "departure_date": departure_date,
        "currency": "EGP",
        "travel_class": row.get("travel_class") or "ECONOMY",
        "search_type": "one_way_snapshot",
    }
    return observed_at, {"observed_at": observed_at.isoformat(), "data": [offer]}, params


def hotel_payload(row: dict[str, str]) -> tuple[datetime, dict[str, Any], dict[str, Any]]:
    observed_at = parse_observed_at(row["collected_at"])
    checkin = datetime.fromisoformat(row["check_in"].strip()).date().isoformat()
    checkout = datetime.fromisoformat(row["check_out"].strip()).date().isoformat()
    if checkout <= checkin:
        raise ValueError("check_out must be after check_in")
    destination = row["destination"].strip().upper()
    hotel_name = row["name"].strip()
    hotel_id = hashlib.sha256(f"{destination}\0{hotel_name}".encode()).hexdigest()[:24]
    offer = {
        "source": "serpapi_google_hotels_csv",
        "hotel_id": hotel_id,
        "hotel_name": hotel_name,
        "city_iata": destination,
        "city": row.get("city") or None,
        "checkin_date": checkin,
        "checkout_date": checkout,
        "guests": None,
        "room_type": row.get("type") or None,
        "hotel_class": optional_decimal(row.get("hotel_class")),
        "rating": optional_decimal(row.get("rating")),
        "reviews": optional_integer(row.get("reviews")),
        "nightly_price": optional_decimal(row.get("price_per_night_egp")),
        "total_price": optional_decimal(row.get("total_price_egp")),
        "currency": "EGP",
        "latitude": optional_decimal(row.get("latitude")),
        "longitude": optional_decimal(row.get("longitude")),
        "free_cancellation": row.get("free_cancellation") or None,
        "amenities": row.get("amenities") or None,
        "serpapi_source_record": row,
    }
    params = {
        "destination": destination,
        "checkin_date": checkin,
        "checkout_date": checkout,
        "currency": "EGP",
        "search_type": "hotel_snapshot",
    }
    return observed_at, {"observed_at": observed_at.isoformat(), "data": [offer]}, params


async def import_rows(
    pool: asyncpg.Pool,
    *,
    provider: str,
    endpoint: str,
    path: Path,
    required: set[str],
    build_payload: Any,
) -> tuple[int, int]:
    inserted = skipped = 0
    rows = read_rows(path, required)
    for row_number, row in enumerate(rows, start=2):
        try:
            _, payload, params = build_payload(row)
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT 1 FROM raw.api_responses WHERE provider=$1 AND payload_hash=$2 LIMIT 1",
                    provider,
                    digest,
                )
                if exists:
                    skipped += 1
                    continue
                await conn.execute(
                    """INSERT INTO raw.api_responses
                       (provider, endpoint, request_params, response_status, payload,
                        payload_hash, ingested_at, ingested_date, dag_run_id)
                       VALUES ($1, $2, $3::jsonb, 200, $4::jsonb, $5, now(), CURRENT_DATE, $6)""",
                    provider,
                    endpoint,
                    json.dumps(params),
                    serialized,
                    digest,
                    f"serpapi-csv-{uuid4()}",
                )
            inserted += 1
        except Exception as exc:
            raise ValueError(f"Invalid row {row_number} in {path.name}: {exc}") from exc
    return inserted, skipped


async def run(flights_path: Path, hotels_path: Path) -> None:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    pool = await asyncpg.create_pool(database_url)
    try:
        flight_counts = await import_rows(
            pool,
            provider="serpapi_snapshot",
            endpoint="/google_flights/csv_snapshot",
            path=flights_path,
            required={"collected_at", "origin", "destination", "flight_date", "price_egp"},
            build_payload=flight_payload,
        )
        hotel_counts = await import_rows(
            pool,
            provider="serpapi_snapshot",
            endpoint="/google_hotels/csv_snapshot",
            path=hotels_path,
            required={"collected_at", "destination", "check_in", "check_out", "name", "total_price_egp"},
            build_payload=hotel_payload,
        )
    finally:
        await pool.close()
    print(f"Flights: {flight_counts[0]} inserted, {flight_counts[1]} duplicates skipped")
    print(f"Hotels:  {hotel_counts[0]} inserted, {hotel_counts[1]} duplicates skipped")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flights", type=Path, help="SerpApi flights CSV path")
    parser.add_argument("hotels", type=Path, help="SerpApi hotels CSV path")
    args = parser.parse_args()
    asyncio.run(run(args.flights, args.hotels))


if __name__ == "__main__":
    main()
