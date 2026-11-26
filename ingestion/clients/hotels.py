from __future__ import annotations

import asyncio
import hashlib
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from ingestion.clients.amadeus import ProviderResponse
from ingestion.http_retry import request_with_retry

CITY_NAMES: dict[str, str] = {
    "LHR": "London",
    "CDG": "Paris",
    "FRA": "Frankfurt",
    "FCO": "Rome",
    "IST": "Istanbul",
    "ATH": "Athens",
    "DXB": "Dubai",
    "DOH": "Doha",
    "AUH": "Abu Dhabi",
    "JED": "Jeddah",
    "RUH": "Riyadh",
    "AMM": "Amman",
    "JFK": "New York",
    "YYZ": "Toronto",
    "BOM": "Mumbai",
}


class MockHotelClient:
    provider = "mock_hotels"
    endpoint = "/mock/hotels"

    async def search_hotels(
        self,
        *,
        city_iata: str,
        checkin_date: date,
        checkout_date: date,
        guests: int = 2,
        currency: str = "USD",
    ) -> ProviderResponse:
        observed_at = datetime.now(timezone.utc)
        nights = max((checkout_date - checkin_date).days, 1)
        city_name = CITY_NAMES.get(city_iata.upper(), city_iata.upper())
        offers: list[dict[str, Any]] = []

        for tier in (3, 4, 5):
            seed = f"mock-hotel:{city_iata}:{tier}:{checkin_date}:{checkout_date}".encode()
            digest = hashlib.sha256(seed).digest()
            base_rate = 55 + tier * 35 + (int.from_bytes(digest[:2], "big") % 65)
            nightly = Decimal(base_rate)
            total = nightly * nights
            hotel_name = f"SmartFare Mock {city_name} {tier}-Star"
            hotel_id = hashlib.sha256(f"{city_iata}:{hotel_name}".encode()).hexdigest()[:24]
            offers.append({
                "source": "mock_hotels",
                "hotel_id": hotel_id,
                "hotel_name": hotel_name,
                "city_iata": city_iata.upper(),
                "city": city_name,
                "checkin_date": checkin_date.isoformat(),
                "checkout_date": checkout_date.isoformat(),
                "guests": guests,
                "room_type": "hotel",
                "hotel_class": str(tier),
                "rating": f"{3.5 + tier * 0.25:.2f}",
                "reviews": 120 + tier * 80,
                "nightly_price": f"{nightly:.2f}",
                "total_price": f"{total:.2f}",
                "currency": currency.upper(),
                "free_cancellation": "true" if tier >= 4 else "false",
                "amenities": "Free Wi-Fi, Air conditioning",
            })

        payload = {
            "demo_data": True,
            "observed_at": observed_at.isoformat(),
            "data": offers,
        }
        return ProviderResponse(status_code=200, payload=payload, record_count=len(offers))

    async def close(self) -> None:
        return None


class SerpApiHotelClient:
    provider = "serpapi_hotels"
    endpoint = "/google_hotels/live"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        api_key: str | None = None,
        base_url: str = "https://serpapi.com",
    ) -> None:
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None

    async def search_hotels(
        self,
        *,
        city_iata: str,
        checkin_date: date,
        checkout_date: date,
        guests: int = 2,
        currency: str = "USD",
    ) -> ProviderResponse:
        if not self.api_key:
            raise RuntimeError("Set SERPAPI_API_KEY in .env for SMARTFARE_HOTEL_PROVIDER=serpapi")

        city_name = CITY_NAMES.get(city_iata.upper(), city_iata.upper())
        nights = max((checkout_date - checkin_date).days, 1)
        observed_at = datetime.now(timezone.utc)

        response = await request_with_retry(
            self._client,
            "GET",
            f"{self.base_url}/search.json",
            params={
                "engine": "google_hotels",
                "q": f"{city_name} hotels",
                "check_in_date": checkin_date.isoformat(),
                "check_out_date": checkout_date.isoformat(),
                "adults": guests,
                "currency": currency.upper(),
                "gl": "us",
                "hl": "en",
                "api_key": self.api_key,
            },
        )
        try:
            raw = response.json()
        except ValueError:
            raw = {"text": response.text}

        if not isinstance(raw, dict):
            raw = {"properties": []}

        if raw.get("error"):
            return ProviderResponse(
                status_code=response.status_code,
                payload={"observed_at": observed_at.isoformat(), "_provider_error": True, "error": raw.get("error"), "data": []},
                record_count=0,
            )

        offers: list[dict[str, Any]] = []
        for prop in raw.get("properties", []) or []:
            name = (prop.get("name") or "").strip()
            if not name:
                continue
            rate_obj = prop.get("rate_per_night") or {}
            total_obj = prop.get("total_rate") or {}
            nightly_val = rate_obj.get("extracted_lowest")
            total_val = total_obj.get("extracted_lowest")
            if total_val is None and nightly_val is not None:
                total_val = float(nightly_val) * nights
            if total_val is None or float(total_val) <= 0:
                continue
            if nightly_val is None:
                nightly_val = float(total_val) / nights

            hotel_id = hashlib.sha256(f"{city_iata.upper()}\0{name}".encode()).hexdigest()[:24]
            amenities_list = prop.get("amenities") or []
            amenities_str = ", ".join(str(a) for a in amenities_list[:10]) if isinstance(amenities_list, list) else None
            hotel_class = prop.get("extracted_hotel_class") or prop.get("hotel_class")
            if isinstance(hotel_class, str):
                digits = "".join(ch for ch in hotel_class if ch.isdigit() or ch == ".")
                hotel_class = digits or None

            offers.append({
                "source": "serpapi_google_hotels_live",
                "hotel_id": hotel_id,
                "hotel_name": name,
                "city_iata": city_iata.upper(),
                "city": city_name,
                "checkin_date": checkin_date.isoformat(),
                "checkout_date": checkout_date.isoformat(),
                "guests": guests,
                "room_type": prop.get("type") or "hotel",
                "hotel_class": str(hotel_class) if hotel_class is not None else None,
                "rating": str(prop["overall_rating"]) if prop.get("overall_rating") is not None else None,
                "reviews": str(prop["reviews"]) if prop.get("reviews") is not None else None,
                "nightly_price": f"{float(nightly_val):.2f}",
                "total_price": f"{float(total_val):.2f}",
                "currency": currency.upper(),
                "free_cancellation": None,
                "amenities": amenities_str,
            })

        payload = {
            "observed_at": observed_at.isoformat(),
            "data": offers,
        }
        return ProviderResponse(
            status_code=response.status_code,
            payload=payload,
            record_count=len(offers),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class AmadeusHotelClient:
    provider = "amadeus_hotels"
    endpoint = "/v3/shopping/hotel-offers"

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

    async def search_hotels(
        self,
        *,
        city_iata: str,
        checkin_date: date,
        checkout_date: date,
        guests: int = 2,
        currency: str = "USD",
        max_hotels: int = 20,
    ) -> ProviderResponse:
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        observed_at = datetime.now(timezone.utc)
        nights = max((checkout_date - checkin_date).days, 1)

        list_resp = await request_with_retry(
            self._client,
            "GET",
            f"{self.base_url}/v1/reference-data/locations/hotels/by-city",
            params={"cityCode": city_iata.upper()},
            headers=headers,
        )
        if list_resp.status_code >= 300:
            return ProviderResponse(
                status_code=list_resp.status_code,
                payload={"observed_at": observed_at.isoformat(), "_provider_error": True, "data": []},
                record_count=0,
            )

        hotel_ids = [
            item["hotelId"]
            for item in (list_resp.json().get("data") or [])[:max_hotels]
            if item.get("hotelId")
        ]
        if not hotel_ids:
            return ProviderResponse(
                status_code=200,
                payload={"observed_at": observed_at.isoformat(), "data": []},
                record_count=0,
            )

        offer_resp = await request_with_retry(
            self._client,
            "GET",
            f"{self.base_url}{self.endpoint}",
            params={
                "hotelIds": ",".join(hotel_ids),
                "adults": guests,
                "checkInDate": checkin_date.isoformat(),
                "checkOutDate": checkout_date.isoformat(),
                "currency": currency.upper(),
                "bestRateOnly": "true",
            },
            headers=headers,
        )
        try:
            raw = offer_resp.json()
        except ValueError:
            raw = {"text": offer_resp.text}

        offers: list[dict[str, Any]] = []
        city_name = CITY_NAMES.get(city_iata.upper(), city_iata.upper())
        for entry in (raw.get("data") or []) if isinstance(raw, dict) else []:
            hotel_meta = entry.get("hotel") or {}
            offer_list = entry.get("offers") or []
            if not offer_list:
                continue
            price_obj = offer_list[0].get("price") or {}
            total_str = price_obj.get("total")
            if not total_str:
                continue
            total_val = float(total_str)
            if total_val <= 0:
                continue
            offers.append({
                "source": "amadeus_hotels_live",
                "hotel_id": str(hotel_meta.get("hotelId") or ""),
                "hotel_name": hotel_meta.get("name") or hotel_meta.get("hotelId"),
                "city_iata": city_iata.upper(),
                "city": city_name,
                "checkin_date": checkin_date.isoformat(),
                "checkout_date": checkout_date.isoformat(),
                "guests": guests,
                "room_type": (offer_list[0].get("room") or {}).get("type") or "hotel",
                "hotel_class": str(hotel_meta["rating"]) if hotel_meta.get("rating") else None,
                "rating": None,
                "reviews": None,
                "nightly_price": f"{total_val / nights:.2f}",
                "total_price": f"{total_val:.2f}",
                "currency": (price_obj.get("currency") or currency).upper(),
                "free_cancellation": None,
                "amenities": None,
            })

        return ProviderResponse(
            status_code=offer_resp.status_code,
            payload={"observed_at": observed_at.isoformat(), "data": offers},
            record_count=len(offers),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
