"""Deterministic synthetic fares for local development only."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from ingestion.clients.amadeus import ProviderResponse


class MockFlightClient:
    """Produce clearly marked, repeatable sample offers without network access."""

    endpoint = "/mock/flight-offers"

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
        **_: Any,
    ) -> ProviderResponse:
        seed = f"{origin}:{destination}:{departure_date}:{return_date}".encode()
        digest = hashlib.sha256(seed).digest()
        route_component = int.from_bytes(digest[:2], "big") % 550
        variation = int.from_bytes(digest[2:4], "big") % 180
        nights = (return_date - departure_date).days
        total = 180 + route_component + variation + max(nights, 0) * 2

        payload = {
            "demo_data": True,
            "notice": "Synthetic development data. Not a live fare or booking offer.",
            "data": [
                {
                    "id": digest.hex()[:16],
                    "type": "flight-offer",
                    "source": "SMARTFARE_MOCK",
                    "itineraries": [],
                    "traveler_count": adults,
                    "travel_class": travel_class,
                    "departure_date": departure_date.isoformat(),
                    "return_date": return_date.isoformat(),
                    "price": {"currency": currency, "total": f"{total:.2f}"},
                }
            ],
        }
        return ProviderResponse(status_code=200, payload=payload, record_count=1)

    async def close(self) -> None:
        return None
