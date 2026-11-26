from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import httpx

from ingestion.http_retry import request_with_retry

TARGET_CURRENCIES = ("USD", "EGP", "EUR", "GBP", "AED", "SAR", "QAR", "TRY", "KWD", "JOD", "CAD", "INR")
FX_URL = "https://open.er-api.com/v6/latest/USD"


async def run_fx_collection(*, dag_run_id: str | None = None) -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running FX ingestion")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await request_with_retry(client, "GET", FX_URL)
        response.raise_for_status()
        payload = response.json()

    if payload.get("result") != "success" or "rates" not in payload:
        raise RuntimeError(f"Unexpected FX API response: {payload.get('error-type', 'unknown')}")

    rates = payload["rates"]
    now_utc = datetime.now(timezone.utc)
    rate_date = now_utc.date()
    rows = [
        (rate_date, "USD", code, Decimal(str(rates[code])), "open.er-api.com", now_utc)
        for code in TARGET_CURRENCIES
        if code in rates and float(rates[code]) > 0
    ]

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    conn = await asyncpg.connect(database_url)
    try:
        async with conn.transaction():
            await conn.execute(
                """create table if not exists public.fx_rates (
                       rate_date       date not null,
                       base_currency   char(3) not null,
                       quote_currency  char(3) not null,
                       rate            numeric(18, 6) not null,
                       source          text not null default 'open.er-api.com',
                       fetched_at      timestamptz not null default now(),
                       primary key (rate_date, base_currency, quote_currency)
                   )"""
            )
            await conn.executemany(
                """insert into public.fx_rates
                   (rate_date, base_currency, quote_currency, rate, source, fetched_at)
                   values ($1, $2, $3, $4, $5, $6)
                   on conflict (rate_date, base_currency, quote_currency) do update set
                     rate = excluded.rate,
                     source = excluded.source,
                     fetched_at = excluded.fetched_at""",
                rows,
            )
            await conn.execute(
                """insert into raw.api_responses
                   (provider, endpoint, request_params, response_status, payload,
                    payload_hash, ingested_at, ingested_date, dag_run_id)
                   values ('fx_open_er_api', '/v6/latest/USD', '{"base":"USD"}'::jsonb,
                           200, $1::jsonb, $2, $3, $4, $5)""",
                serialized,
                payload_hash,
                now_utc,
                rate_date,
                dag_run_id,
            )
    finally:
        await conn.close()

    return len(rows)


async def main() -> None:
    count = await run_fx_collection()
    print(f"FX rates updated: {count} currencies stored for USD base")


if __name__ == "__main__":
    asyncio.run(main())
