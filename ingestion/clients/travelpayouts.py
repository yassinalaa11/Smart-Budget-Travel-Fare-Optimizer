"""Travelpayouts Flight Data API v2 client for cached calendar prices."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx

from ingestion.clients.amadeus import ProviderResponse


class TravelpayoutsClient:
    """Fetch cached flight prices using the documented month-matrix endpoint."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        token: str | None = None,
        base_url: str = "https://api.travelpayouts.com",
    ) -> None:
        self.token = token or os.getenv("TRAVELPAYOUTS_TOKEN", "")
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None

    @staticmethod
    def request_parameters(
        *, origin: str, destination: str, departure_date: date,
        return_date: date, currency: str = "USD", **_: Any,
    ) -> dict[str, Any]:
        return {
            "origin": origin,
            "destination": destination,
            "month": departure_date.replace(day=1).isoformat(),
            "trip_duration": (return_date - departure_date).days,
            "trip_class": 0,
            "one_way": "false",
            "currency": currency.lower(),
            "show_to_affiliates": "true",
        }

    async def search_flights(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date,
        currency: str = "USD",
        **_: Any,
    ) -> ProviderResponse:
        if not self.token:
            raise RuntimeError("Set TRAVELPAYOUTS_TOKEN in .env")
        # The month-matrix API returns cached calendar entries for the given
        # route/month and stay duration. It is not an exact live-offer search.
        response = await self._client.get(
            f"{self.base_url}/v2/prices/month-matrix",
            params=self.request_parameters(
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                return_date=return_date,
                currency=currency,
            ),
            headers={"x-access-token": self.token},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        if payload.get("success") is False and 200 <= response.status_code < 300:
            payload["_provider_error"] = True
        records = payload.get("data", [])
        return ProviderResponse(
            status_code=response.status_code,
            payload=payload,
            record_count=len(records) if isinstance(records, list) else 0,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
