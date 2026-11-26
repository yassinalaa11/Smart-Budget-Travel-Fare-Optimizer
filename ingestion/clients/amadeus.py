from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from ingestion.http_retry import request_with_retry


@dataclass(frozen=True)
class ProviderResponse:
    status_code: int
    payload: dict[str, Any]
    record_count: int


class AmadeusClient:
    endpoint = "/v2/shopping/flight-offers"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.client_id = client_id or os.getenv("AMADEUS_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("AMADEUS_CLIENT_SECRET", "")
        self.base_url = (base_url or os.getenv(
            "AMADEUS_BASE_URL", "https://test.api.amadeus.com"
        )).rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _access_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in .env")
        if self._token and time.monotonic() < self._token_expires_at - 30:
            return self._token

        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at - 30:
                return self._token
            response = await request_with_retry(
                self._client,
                "POST",
                f"{self.base_url}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_payload = response.json()
            self._token = token_payload["access_token"]
            self._token_expires_at = time.monotonic() + int(token_payload["expires_in"])
            return self._token

    async def search_flights(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date,
        adults: int = 1,
        currency: str = "USD",
        travel_class: str = "ECONOMY",
        max_results: int = 50,
    ) -> ProviderResponse:
        token = await self._access_token()
        response = await request_with_retry(
            self._client,
            "GET",
            f"{self.base_url}{self.endpoint}",
            params={
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": departure_date.isoformat(),
                "returnDate": return_date.isoformat(),
                "adults": adults,
                "currencyCode": currency,
                "travelClass": travel_class,
                "max": max_results,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        offers = payload.get("data", [])
        return ProviderResponse(
            status_code=response.status_code,
            payload=payload,
            record_count=len(offers) if isinstance(offers, list) else 0,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
