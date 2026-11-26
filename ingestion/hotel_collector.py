from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

import asyncpg

from ingestion.clients.amadeus import ProviderResponse
from ingestion.clients.hotels import (
    AmadeusHotelClient,
    MockHotelClient,
    SerpApiHotelClient,
)
from ingestion.config import HOTEL_MARKETS, lead_time_days
from ingestion.rate_limiter import ProviderLimit, QuotaExceeded, TokenBucketLimiter


@dataclass(frozen=True)
class HotelQuery:
    city_iata: str
    checkin_date: date
    checkout_date: date
    guests: int = 2
    currency: str = "USD"

    def parameters(self) -> dict[str, Any]:
        return {
            "city_iata": self.city_iata,
            "checkin_date": self.checkin_date.isoformat(),
            "checkout_date": self.checkout_date.isoformat(),
            "guests": self.guests,
            "currency": self.currency,
        }


def build_hotel_queries(
    observed_on: date,
    *,
    max_requests: int = 15,
    markets: Iterable[str] = HOTEL_MARKETS,
    stay_nights: tuple[int, ...] = (3, 7),
    guests: int = 2,
    currency: str = "USD",
) -> tuple[HotelQuery, ...]:
    if max_requests <= 0:
        return ()

    market_list = list(markets)
    if market_list:
        rotation = observed_on.toordinal() % len(market_list)
        market_list = market_list[rotation:] + market_list[:rotation]

    queries: list[HotelQuery] = []
    for lead_days in lead_time_days():
        checkin = observed_on + timedelta(days=lead_days)
        for city in market_list:
            for nights in stay_nights:
                queries.append(
                    HotelQuery(
                        city_iata=city,
                        checkin_date=checkin,
                        checkout_date=checkin + timedelta(days=nights),
                        guests=guests,
                        currency=currency,
                    )
                )
                if len(queries) >= max_requests:
                    return tuple(queries)
    return tuple(queries)


class HotelCollector:
    def __init__(
        self,
        pool: asyncpg.Pool,
        client: Any,
        limiter: TokenBucketLimiter,
        *,
        provider: str,
        dag_run_id: str | None = None,
    ) -> None:
        self.pool = pool
        self.client = client
        self.limiter = limiter
        self.provider = provider
        self.dag_run_id = dag_run_id

    async def collect(self, queries: Iterable[HotelQuery]) -> tuple[int, int]:
        succeeded = 0
        failed = 0
        for query in queries:
            try:
                if await self._collect_one(query):
                    succeeded += 1
                else:
                    failed += 1
            except QuotaExceeded:
                break
            except Exception:
                failed += 1
        return succeeded, failed

    async def _collect_one(self, query: HotelQuery) -> bool:
        request_id = uuid4()
        started_at = datetime.now(timezone.utc)
        params = query.parameters()
        endpoint = getattr(self.client, "endpoint", "/hotels")

        async with self.pool.acquire() as conn:
            await conn.execute(
                """insert into public.ingestion_request_log
                   (request_id, dag_run_id, provider, endpoint, request_params, started_at)
                   values ($1, $2, $3, $4, $5::jsonb, $6)""",
                request_id,
                self.dag_run_id,
                self.provider,
                endpoint,
                json.dumps(params, sort_keys=True),
                started_at,
            )

        try:
            async with self.limiter.request_slot():
                response: ProviderResponse = await self.client.search_hotels(
                    city_iata=query.city_iata,
                    checkin_date=query.checkin_date,
                    checkout_date=query.checkout_date,
                    guests=query.guests,
                    currency=query.currency,
                )
            outcome = (
                "succeeded"
                if 200 <= response.status_code < 300
                and not response.payload.get("_provider_error")
                else "http_error"
            )
            await self._persist_result(request_id, started_at, endpoint, params, response, outcome)
            return outcome == "succeeded"
        except QuotaExceeded:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "delete from public.ingestion_request_log where request_id = $1",
                    request_id,
                )
            raise
        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """update public.ingestion_request_log
                       set completed_at = $2, latency_ms = $3, outcome = 'network_error',
                           error_message = $4
                       where request_id = $1""",
                    request_id,
                    completed_at,
                    int((completed_at - started_at).total_seconds() * 1000),
                    str(exc)[:2000],
                )
            raise

    async def _persist_result(
        self,
        request_id: Any,
        started_at: datetime,
        endpoint: str,
        params: dict[str, Any],
        response: ProviderResponse,
        outcome: str,
    ) -> None:
        serialized = json.dumps(response.payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        completed_at = datetime.now(timezone.utc)
        latency_ms = int((completed_at - started_at).total_seconds() * 1000)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """insert into raw.api_responses
                       (provider, endpoint, request_params, response_status, payload,
                        payload_hash, ingested_at, ingested_date, dag_run_id)
                       values ($1, $2, $3::jsonb, $4, $5::jsonb, $6, $7, $8, $9)""",
                    self.provider,
                    endpoint,
                    json.dumps(params, sort_keys=True),
                    response.status_code,
                    serialized,
                    payload_hash,
                    completed_at,
                    completed_at.date(),
                    self.dag_run_id,
                )
                await conn.execute(
                    """update public.ingestion_request_log
                       set completed_at = $2, latency_ms = $3, http_status = $4,
                           record_count = $5, outcome = $6
                       where request_id = $1""",
                    request_id,
                    completed_at,
                    latency_ms,
                    response.status_code,
                    response.record_count,
                    outcome,
                )


async def run_hotel_collection(*, dag_run_id: str | None = None) -> tuple[int, int]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running the hotel collector")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    max_requests = int(os.getenv("SMARTFARE_MAX_HOTEL_REQUESTS_PER_RUN", "15"))
    provider_mode = os.getenv("SMARTFARE_HOTEL_PROVIDER", "mock").strip().lower()

    if provider_mode == "mock":
        client = MockHotelClient()
    elif provider_mode == "serpapi":
        client = SerpApiHotelClient()
    elif provider_mode == "amadeus":
        client = AmadeusHotelClient()
    else:
        raise RuntimeError("SMARTFARE_HOTEL_PROVIDER must be 'mock', 'serpapi', or 'amadeus'")

    pool = await asyncpg.create_pool(database_url)
    limiter = TokenBucketLimiter(ProviderLimit(requests_per_second=2, max_concurrency=2))
    try:
        collector = HotelCollector(
            pool,
            client,
            limiter,
            provider=client.provider,
            dag_run_id=dag_run_id,
        )
        today = datetime.now(timezone.utc).date()
        return await collector.collect(build_hotel_queries(today, max_requests=max_requests))
    finally:
        await client.close()
        await pool.close()


async def main() -> None:
    succeeded, failed = await run_hotel_collection()
    print(f"Hotel collection finished: {succeeded} succeeded, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
