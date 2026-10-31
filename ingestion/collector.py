"""Bounded collection loop that stores provider responses and request metadata."""

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

from ingestion.clients.amadeus import AmadeusClient, ProviderResponse
from ingestion.clients.mock import MockFlightClient
from ingestion.clients.travelpayouts import TravelpayoutsClient
from ingestion.config import ROUTES, lead_time_days
from ingestion.rate_limiter import ProviderLimit, QuotaExceeded, TokenBucketLimiter


@dataclass(frozen=True)
class FlightQuery:
    origin: str
    destination: str
    departure_date: date
    return_date: date

    def parameters(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "departure_date": self.departure_date.isoformat(),
            "return_date": self.return_date.isoformat(),
            "adults": 1,
            "currency": "USD",
            "travel_class": "ECONOMY",
        }


def build_queries(
    observed_on: date,
    *,
    max_requests: int = 25,
    routes: Iterable[dict[str, str]] = ROUTES,
    stay_nights: tuple[int, ...] = (3, 7, 14),
) -> tuple[FlightQuery, ...]:
    """Build a deterministic, explicitly capped batch from the configured grid.

    The conservative default is intentional: 15 routes times the full horizon
    exceeds a free-tier monthly allowance if queried every day.
    """
    if max_requests < 0:
        raise ValueError("max_requests cannot be negative")
    if max_requests == 0:
        return ()

    route_list = list(routes)
    if route_list:
        rotation = observed_on.toordinal() % len(route_list)
        route_list = route_list[rotation:] + route_list[:rotation]
    queries: list[FlightQuery] = []
    # Interleave routes so a small daily request budget still samples the market set.
    for lead_days in lead_time_days():
        for route in route_list:
            departure = observed_on + timedelta(days=lead_days)
            for nights in stay_nights:
                queries.append(
                    FlightQuery(
                        origin=route["origin"],
                        destination=route["destination"],
                        departure_date=departure,
                        return_date=departure + timedelta(days=nights),
                    )
                )
                if len(queries) >= max_requests:
                    return tuple(queries)
    return tuple(queries)


def build_month_matrix_queries(
    observed_on: date,
    *,
    max_requests: int = 25,
    routes: Iterable[dict[str, str]] = ROUTES,
    stay_nights: tuple[int, ...] = (3, 7, 14),
) -> tuple[FlightQuery, ...]:
    """Build unique route/month/stay requests for Travelpayouts' cached matrix."""
    if max_requests < 0:
        raise ValueError("max_requests cannot be negative")
    if max_requests == 0:
        return ()

    queries: list[FlightQuery] = []
    seen: set[tuple[str, str, date, int]] = set()
    route_list = list(routes)
    for lead_days in lead_time_days():
        departure = observed_on + timedelta(days=lead_days)
        month_start = departure.replace(day=1)
        for route in route_list:
            for nights in stay_nights:
                key = (route["origin"], route["destination"], month_start, nights)
                if key in seen:
                    continue
                seen.add(key)
                queries.append(
                    FlightQuery(
                        origin=route["origin"],
                        destination=route["destination"],
                        departure_date=month_start,
                        return_date=month_start + timedelta(days=nights),
                    )
                )
                if len(queries) >= max_requests:
                    return tuple(queries)
    return tuple(queries)


class Collector:
    def __init__(
        self,
        pool: asyncpg.Pool,
        client: Any,
        limiter: TokenBucketLimiter,
        *,
        provider: str = "amadeus",
        dag_run_id: str | None = None,
        monthly_quota: int | None = 2000,
    ) -> None:
        self.pool = pool
        self.client = client
        self.limiter = limiter
        self.provider = provider
        self.dag_run_id = dag_run_id
        self.monthly_quota = monthly_quota

    async def collect(self, queries: Iterable[FlightQuery]) -> tuple[int, int]:
        succeeded = 0
        failed = 0
        for query in queries:
            try:
                if await self._collect_one(query):
                    succeeded += 1
                else:
                    failed += 1
            except QuotaExceeded:
                # Stop this run cleanly instead of repeatedly attempting blocked calls.
                break
            except Exception:
                failed += 1
        return succeeded, failed

    async def _collect_one(self, query: FlightQuery) -> bool:
        request_id = uuid4()
        started_at = datetime.now(timezone.utc)
        params = query.parameters()
        request_parameters = getattr(self.client, "request_parameters", None)
        if request_parameters is not None:
            params = request_parameters(
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                adults=1,
                currency="USD",
                travel_class="ECONOMY",
            )
        async with self.pool.acquire() as connection:
            await connection.execute(
                """INSERT INTO public.ingestion_request_log
                   (request_id, dag_run_id, provider, endpoint, request_params, started_at)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6)""",
                request_id,
                self.dag_run_id,
                self.provider,
                getattr(self.client, "endpoint", "/v2/shopping/flight-offers"),
                json.dumps(params, sort_keys=True),
                started_at,
            )

        try:
            if self.monthly_quota is not None:
                async with self.pool.acquire() as connection:
                    monthly_count = await connection.fetchval(
                        """SELECT count(*) FROM public.ingestion_request_log
                           WHERE provider = $1
                             AND started_at >= date_trunc('month', now() AT TIME ZONE 'UTC')
                                   AT TIME ZONE 'UTC'""",
                        self.provider,
                    )
            else:
                monthly_count = 0
            if self.monthly_quota is not None and monthly_count > self.monthly_quota:
                async with self.pool.acquire() as connection:
                    await connection.execute(
                        "DELETE FROM public.ingestion_request_log WHERE request_id = $1",
                        request_id,
                    )
                raise QuotaExceeded("provider monthly request quota exhausted")
            async with self.limiter.request_slot():
                response = await self.client.search_flights(
                    origin=query.origin,
                    destination=query.destination,
                    departure_date=query.departure_date,
                    return_date=query.return_date,
                    adults=1,
                    currency="USD",
                    travel_class="ECONOMY",
                )
            outcome = (
                "succeeded"
                if 200 <= response.status_code < 300
                and not response.payload.get("_provider_error")
                else "http_error"
            )
            await self._persist_result(request_id, started_at, params, response, outcome)
            return outcome == "succeeded"
        except QuotaExceeded:
            # No provider call was made. Remove the pending audit row for this skipped item.
            async with self.pool.acquire() as connection:
                await connection.execute(
                    "DELETE FROM public.ingestion_request_log WHERE request_id = $1",
                    request_id,
                )
            raise
        except Exception as exc:
            await self._persist_failure(request_id, started_at, str(exc))
            raise

    async def _persist_result(
        self,
        request_id: Any,
        started_at: datetime,
        params: dict[str, Any],
        response: ProviderResponse,
        outcome: str,
    ) -> None:
        serialized = json.dumps(response.payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        completed_at = datetime.now(timezone.utc)
        latency_ms = int((completed_at - started_at).total_seconds() * 1000)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO raw.api_responses
                       (provider, endpoint, request_params, response_status, payload,
                        payload_hash, ingested_at, ingested_date, dag_run_id)
                       VALUES ($1, $2, $3::jsonb, $4, $5::jsonb, $6, $7, $8, $9)""",
                    self.provider,
                    getattr(self.client, "endpoint", "/v2/shopping/flight-offers"),
                    json.dumps(params, sort_keys=True),
                    response.status_code,
                    serialized,
                    payload_hash,
                    completed_at,
                    completed_at.date(),
                    self.dag_run_id,
                )
                await connection.execute(
                    """UPDATE public.ingestion_request_log
                       SET completed_at = $2, latency_ms = $3, http_status = $4,
                           record_count = $5, outcome = $6
                       WHERE request_id = $1""",
                    request_id,
                    completed_at,
                    latency_ms,
                    response.status_code,
                    response.record_count,
                    outcome,
                )

    async def _persist_failure(
        self, request_id: Any, started_at: datetime, error_message: str
    ) -> None:
        completed_at = datetime.now(timezone.utc)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """UPDATE public.ingestion_request_log
                   SET completed_at = $2, latency_ms = $3, outcome = 'network_error',
                       error_message = $4
                   WHERE request_id = $1""",
                request_id,
                completed_at,
                int((completed_at - started_at).total_seconds() * 1000),
                error_message[:2000],
            )


async def run_collection(*, dag_run_id: str | None = None) -> tuple[int, int]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running the collector")
    # The FastAPI service uses SQLAlchemy's async URL scheme; asyncpg uses the plain scheme.
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    max_requests = int(os.getenv("SMARTFARE_MAX_REQUESTS_PER_RUN", "25"))
    provider = os.getenv("SMARTFARE_PROVIDER", "mock").strip().lower()
    if provider == "mock":
        client = MockFlightClient()
        monthly_quota = None
    elif provider == "amadeus":
        if not os.getenv("AMADEUS_CLIENT_SECRET") or not os.getenv("AMADEUS_CLIENT_ID"):
            raise RuntimeError(
                "SMARTFARE_PROVIDER=amadeus requires AMADEUS_CLIENT_ID and "
                "AMADEUS_CLIENT_SECRET in .env"
            )
        client = AmadeusClient()
        monthly_quota = 2000
    elif provider == "travelpayouts":
        if not os.getenv("TRAVELPAYOUTS_TOKEN"):
            raise RuntimeError(
                "SMARTFARE_PROVIDER=travelpayouts requires TRAVELPAYOUTS_TOKEN in .env"
            )
        client = TravelpayoutsClient()
        # Travelpayouts quota depends on the account; cap each run via the
        # existing SMARTFARE_MAX_REQUESTS_PER_RUN setting instead.
        monthly_quota = None
    else:
        raise RuntimeError(
            "SMARTFARE_PROVIDER must be 'mock', 'amadeus', or 'travelpayouts'"
        )

    pool = await asyncpg.create_pool(database_url)
    limiter = TokenBucketLimiter(
        ProviderLimit(
            requests_per_second=10,
            monthly_quota=monthly_quota,
            max_concurrency=5,
        )
    )
    try:
        collector = Collector(
            pool,
            client,
            limiter,
            provider=provider,
            dag_run_id=dag_run_id,
            monthly_quota=monthly_quota,
        )
        today = datetime.now(timezone.utc).date()
        query_builder = build_month_matrix_queries if provider == "travelpayouts" else build_queries
        succeeded, failed = await collector.collect(
            query_builder(today, max_requests=max_requests)
        )
        return succeeded, failed
    finally:
        await client.close()
        await pool.close()


async def main() -> None:
    succeeded, failed = await run_collection()
    print(f"Collection finished: {succeeded} succeeded, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
