from __future__ import annotations

from datetime import date

import httpx
import pytest

from ingestion.hotel_collector import build_hotel_queries
from ingestion.http_retry import _retry_delay, request_with_retry
from ingestion.partitions import _add_months


def test_add_months_handles_year_rollover() -> None:
    assert _add_months(date(2026, 11, 26), 0) == date(2026, 11, 1)
    assert _add_months(date(2026, 11, 26), 1) == date(2026, 12, 1)
    assert _add_months(date(2026, 11, 26), 2) == date(2027, 1, 1)
    assert _add_months(date(2026, 12, 31), 3) == date(2027, 3, 1)


def test_build_hotel_queries_respects_budget_and_stay_nights() -> None:
    queries = build_hotel_queries(
        date(2026, 11, 26),
        max_requests=6,
        markets=("DXB", "IST", "LHR"),
        stay_nights=(3, 7),
    )
    assert len(queries) == 6
    for q in queries:
        assert (q.checkout_date - q.checkin_date).days in (3, 7)
        assert q.checkin_date > date(2026, 11, 26)
        assert q.currency == "USD"

    assert build_hotel_queries(date(2026, 11, 26), max_requests=0) == ()


def test_retry_delay_honors_retry_after_and_caps_at_max() -> None:
    delay = _retry_delay(0, base_delay=1.0, max_delay=10.0, retry_after="4")
    assert 4.0 <= delay <= 4.5

    capped = _retry_delay(5, base_delay=1.0, max_delay=8.0, retry_after="99")
    assert 8.0 <= capped <= 8.5


async def test_request_with_retry_recovers_from_429_and_503() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        if attempts == 2:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await request_with_retry(
            client,
            "GET",
            "https://example.test/fares",
            max_retries=3,
            base_delay=0.001,
            max_delay=0.01,
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert attempts == 3


async def test_request_with_retry_returns_last_500_after_exhausting_retries() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text="server error", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await request_with_retry(
            client,
            "GET",
            "https://example.test/fail",
            max_retries=2,
            base_delay=0.001,
            max_delay=0.005,
        )

    assert resp.status_code == 500
    assert attempts == 3


async def test_request_with_retry_raises_on_persistent_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ReadTimeout):
            await request_with_retry(
                client,
                "GET",
                "https://example.test/timeout",
                max_retries=1,
                base_delay=0.001,
                max_delay=0.005,
            )
