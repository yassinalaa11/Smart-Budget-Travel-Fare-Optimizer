"""Load licensed historical hotel-price observations from a normalized CSV."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg

REQUIRED = {
    "observed_at", "source", "hotel_id", "city_iata", "checkin_date",
    "checkout_date", "guests", "total_price", "currency",
}


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a UTC offset, e.g. 2025-01-20T12:00:00Z")
    return parsed.astimezone(timezone.utc)


async def import_file(path: Path) -> tuple[int, int]:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    pool = await asyncpg.create_pool(database_url)
    inserted = skipped = 0
    try:
        with path.open(newline="", encoding="utf-8-sig") as source_file:
            reader = csv.DictReader(source_file)
            if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
                raise ValueError(f"CSV must include columns: {', '.join(sorted(REQUIRED))}")
            for row_number, row in enumerate(reader, start=2):
                try:
                    observed_at = parse_datetime(row["observed_at"].strip())
                    checkin = date.fromisoformat(row["checkin_date"].strip())
                    checkout = date.fromisoformat(row["checkout_date"].strip())
                    price = Decimal(row["total_price"].strip())
                    guests = int(row["guests"])
                    if checkout <= checkin or price <= 0 or guests < 1:
                        raise ValueError("dates, price, and guests must be positive/valid")
                    currency = row["currency"].strip().upper()
                    if len(currency) != 3:
                        raise ValueError("currency must be a 3-letter code")
                    record: dict[str, Any] = {
                        "hotel_id": row["hotel_id"].strip(),
                        "hotel_name": (row.get("hotel_name") or "").strip() or None,
                        "city_iata": row["city_iata"].strip().upper(),
                        "checkin_date": checkin.isoformat(),
                        "checkout_date": checkout.isoformat(),
                        "guests": guests,
                        "room_type": (row.get("room_type") or "").strip() or None,
                        "total_price": format(price, "f"),
                        "currency": currency,
                        "source": row["source"].strip(),
                    }
                    payload = {"observed_at": observed_at.isoformat(), "data": [record]}
                    params = {key: record[key] for key in (
                        "hotel_id", "city_iata", "checkin_date", "checkout_date", "guests", "currency"
                    )}
                    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                    digest = hashlib.sha256(serialized.encode()).hexdigest()
                    async with pool.acquire() as conn:
                        exists = await conn.fetchval(
                            "SELECT 1 FROM raw.api_responses WHERE provider='historical_csv_hotels' AND payload_hash=$1 LIMIT 1",
                            digest,
                        )
                        if exists:
                            skipped += 1
                            continue
                        async with conn.transaction():
                            await conn.execute(
                                """INSERT INTO raw.api_responses
                                   (provider, endpoint, request_params, response_status, payload,
                                    payload_hash, ingested_at, ingested_date, dag_run_id)
                                   VALUES ('historical_csv_hotels', '/historical/hotels', $1::jsonb,
                                           200, $2::jsonb, $3, now(), CURRENT_DATE, $4)""",
                                json.dumps(params), serialized, digest, f"csv-{uuid4()}"
                            )
                    inserted += 1
                except Exception as exc:
                    raise ValueError(f"Invalid CSV row {row_number}: {exc}") from exc
    finally:
        await pool.close()
    return inserted, skipped


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m ingestion.import_hotels /app/data/hotel_prices.csv")
    inserted, skipped = await import_file(Path(sys.argv[1]))
    print(f"Hotel history import complete: {inserted} inserted, {skipped} duplicates skipped")


if __name__ == "__main__":
    asyncio.run(main())
