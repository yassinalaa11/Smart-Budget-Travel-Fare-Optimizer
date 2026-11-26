from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.routers.affiliate import build_partner_deep_link
from tests.conftest import FakeConnection, FakePool


def test_build_partner_deep_link_formats_all_partners() -> None:
    for partner, expected_host in [
        ("aviasales", "www.aviasales.com/search/CAI1401DXB21011"),
        ("google_flights", "www.google.com/travel/flights"),
        ("skyscanner", "www.skyscanner.com/transport/flights/cai/dxb/270114/270121/"),
    ]:
        url = build_partner_deep_link(
            origin_iata="cai",
            destination_iata="dxb",
            departure_date=date(2027, 1, 14),
            return_date=date(2027, 1, 21),
            partner=partner,
            sub_id="sf_test123456",
        )
        assert expected_host in url
        assert "sub_id=sf_test123456" in url


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    pool: FakePool,
    artifacts_dir: Path,
) -> TestClient:
    async def fake_create_pool(*args, **kwargs) -> FakePool:
        return pool

    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("SMARTFARE_ARTIFACTS_DIR", str(artifacts_dir))
    monkeypatch.setattr(api_main.asyncpg, "create_pool", fake_create_pool)
    return TestClient(api_main.app)


def test_health_and_model_status_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    trained_artifacts_dir: Path,
) -> None:
    pool = FakePool(FakeConnection())
    with _make_client(monkeypatch, pool, trained_artifacts_dir) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        status = client.get("/api/v1/models/status")
        assert status.status_code == 200
        body = status.json()
        assert body["artifacts_loaded"] is True
        assert body["model_version"] == "v_pytest_001"

        reloaded = client.post("/api/v1/models/reload")
        assert reloaded.status_code == 200
        assert reloaded.json()["artifacts_loaded"] is True
    assert pool.closed is True


def test_recommendation_flags_book_now_inside_14_day_cliff(
    monkeypatch: pytest.MonkeyPatch,
    trained_artifacts_dir: Path,
) -> None:
    conn = FakeConnection(
        fetchrow_results=[
            {
                "observed_date": date(2026, 11, 25),
                "min_total_price": Decimal("9356.00"),
                "min_total_price_usd": Decimal("171.17"),
                "currency_code": "EGP",
                "contains_demo_data": False,
            },
            {
                "observation_days": 1,
                "p20": Decimal("10085.60"),
                "p50": Decimal("10837.00"),
                "p80": Decimal("25860.00"),
                "pct_rank": 0.25,
            },
        ]
    )
    with _make_client(monkeypatch, FakePool(conn), trained_artifacts_dir) as client:
        resp = client.get("/api/v1/routes/CAI-DXB/recommendation?departure_date=2026-11-29")
        assert resp.status_code == 200
        data = resp.json()
        assert data["signal"] == "BOOK_NOW"
        assert data["days_to_departure"] == 4
        assert data["model_stage"] == "phase_1_statistical_baseline"


def test_redirect_book_logs_click_and_returns_302(
    monkeypatch: pytest.MonkeyPatch,
    trained_artifacts_dir: Path,
) -> None:
    conn = FakeConnection()
    with _make_client(monkeypatch, FakePool(conn), trained_artifacts_dir) as client:
        resp = client.get(
            "/api/v1/redirect/book?route_id=CAI-DXB&departure_date=2027-01-14&partner=aviasales&quoted_price=193.42&quoted_currency=USD",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        sub_id = resp.headers.get("x-smartfare-sub-id")
        assert sub_id is not None and sub_id.startswith("sf_")
        assert sub_id in resp.headers["location"]

        bad = client.get("/api/v1/redirect/book?route_id=INVALID&departure_date=2027-01-14", follow_redirects=False)
        assert bad.status_code == 400


def test_affiliate_stats_computes_conversion_rate_and_rpm(
    monkeypatch: pytest.MonkeyPatch,
    trained_artifacts_dir: Path,
) -> None:
    now = datetime.now(timezone.utc)
    conn = FakeConnection(
        fetchrow_results=[
            {
                "total_clicks": 10,
                "confirmed_conversions": 2,
                "total_booking_value_usd": 400.0,
                "total_commission_usd": 12.0,
            }
        ],
        fetch_results=[
            [
                {
                    "sub_id": "sf_abc123456789",
                    "route_id": "CAI-DXB",
                    "departure_date": date(2027, 1, 14),
                    "return_date": None,
                    "partner": "aviasales",
                    "quoted_price": Decimal("193.42"),
                    "quoted_currency": "USD",
                    "source_tab": "booking_advisor",
                    "clicked_at": now,
                    "conversion_status": "confirmed",
                    "booking_value_usd": Decimal("200.00"),
                    "commission_usd": Decimal("6.00"),
                }
            ]
        ],
    )
    with _make_client(monkeypatch, FakePool(conn), trained_artifacts_dir) as client:
        resp = client.get("/api/v1/affiliate/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_clicks"] == 10
        assert body["confirmed_conversions"] == 2
        assert body["conversion_rate_pct"] == 20.0
        assert body["rpm_usd"] == 1200.0
        assert len(body["recent_clicks"]) == 1


def test_price_alerts_crud_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    trained_artifacts_dir: Path,
) -> None:
    alert_id = uuid4()
    now = datetime.now(timezone.utc)
    alert_row = {
        "alert_id": alert_id,
        "route_id": "CAI-DXB",
        "departure_date": date(2027, 1, 14),
        "return_date": None,
        "target_price_usd": Decimal("210.00"),
        "channel": "all",
        "contact": "tester@example.com",
        "is_active": True,
        "created_at": now,
        "last_checked_at": None,
        "last_triggered_at": None,
        "last_observed_price_usd": None,
        "trigger_count": 0,
    }
    conn = FakeConnection(
        fetchrow_results=[alert_row],
        fetch_results=[[alert_row]],
        fetchval_results=[alert_id],
    )
    with _make_client(monkeypatch, FakePool(conn), trained_artifacts_dir) as client:
        created = client.post(
            "/api/v1/alerts",
            json={
                "route_id": "CAI-DXB",
                "departure_date": "2027-01-14",
                "target_price_usd": 210.0,
                "channel": "all",
                "contact": "tester@example.com",
            },
        )
        assert created.status_code == 201
        assert created.json()["alert_id"] == str(alert_id)

        listed = client.get("/api/v1/alerts")
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        deleted = client.delete(f"/api/v1/alerts/{alert_id}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deactivated"


def test_trip_heatmap_combines_flight_and_hotel_usd(
    monkeypatch: pytest.MonkeyPatch,
    trained_artifacts_dir: Path,
) -> None:
    conn = FakeConnection(
        fetch_results=[
            [{"departure_date": date(2027, 1, 14), "flight_price_usd": 193.42}],
            [],
        ],
        fetchval_results=[68.62],
    )
    with _make_client(monkeypatch, FakePool(conn), trained_artifacts_dir) as client:
        resp = client.get("/api/v1/routes/CAI-DXB/trip-heatmap?stay_nights=3,5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stay_nights_options"] == [3, 5]
        assert len(data["cells"]) == 2
        assert data["cells"][0]["total_trip_usd"] == round(193.42 + 68.62 * 3, 2)
        assert data["cells"][1]["total_trip_usd"] == round(193.42 + 68.62 * 5, 2)
