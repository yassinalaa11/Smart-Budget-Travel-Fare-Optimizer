from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PriceHistoryPoint(BaseModel):
    provider: str
    observed_date: date
    departure_date: date
    return_date: date | None = None
    cabin_class: str
    min_total_price: Decimal
    median_total_price: Decimal
    currency_code: str
    offer_count: int
    response_count: int
    contains_demo_data: bool
    all_data_is_demo: bool

    model_config = ConfigDict(from_attributes=True)


class PriceHistoryResponse(BaseModel):
    route_id: str
    points: list[PriceHistoryPoint]


class RouteSummary(BaseModel):
    route_id: str
    origin_iata: str
    destination_iata: str
    departure_count: int
    all_data_is_demo: bool


class DepartureOption(BaseModel):
    departure_date: date
    return_date: date | None = None


class RecommendationResponse(BaseModel):
    route_id: str
    departure_date: date
    return_date: date | None = None
    currency_code: str
    current_price: Decimal
    route_p20_price: Decimal
    route_median_price: Decimal
    route_p80_price: Decimal
    percentile_rank: float
    days_to_departure: int
    observation_days: int
    signal: str
    confidence: str
    reason: str
    contains_demo_data: bool
    model_stage: str = "phase_1_statistical_baseline"
    drop_probability: float | None = None
    expected_min_price_p10: float | None = None
    expected_min_price_p50: float | None = None
    expected_min_price_p90: float | None = None


class CheapestWindowItem(BaseModel):
    departure_date: date
    return_date: date | None = None
    flight_price: Decimal
    flight_currency: str
    flight_price_usd: Decimal | None = None
    hotel_total_price: Decimal | None = None
    hotel_currency: str | None = None
    hotel_total_price_usd: Decimal | None = None
    combined_total_price: Decimal | None = None
    combined_total_price_usd: Decimal | None = None
    hotel_match_type: str = "none"
    contains_demo_data: bool


class CheapestWindowsResponse(BaseModel):
    route_id: str
    destination_iata: str
    windows: list[CheapestWindowItem]


class ModelStatusResponse(BaseModel):
    artifacts_loaded: bool
    model_version: str | None = None
    trained_at: str | None = None
    live_model_status: str
    live_promoted_routes: list[str]
    synthetic_decision_status: str
    synthetic_promoted_routes: list[str]
    scorecard: dict[str, Any]


class FlightCurvePoint(BaseModel):
    days_to_departure: int
    predicted_price: float


class FlightCurvePredictionResponse(BaseModel):
    route_id: str
    currency: str
    is_synthetic: bool
    best_days_to_departure: int
    lowest_predicted_price: float
    metrics: dict[str, Any]
    curve: list[FlightCurvePoint]


class FlightCurveOptionsResponse(BaseModel):
    routes: list[str]
    currency_by_route: dict[str, str]


class HotelCatalogItem(BaseModel):
    hotel_id: str
    name: str
    city: str
    stars: float


class HotelCurvePoint(BaseModel):
    days_to_checkin: int
    predicted_nightly: float
    predicted_stay_total: float


class HotelCurvePredictionResponse(BaseModel):
    hotel_id: str
    hotel_name: str
    city: str
    currency: str
    nights: int
    adults: int
    is_synthetic: bool
    best_days_to_checkin: int
    lowest_predicted_nightly: float
    lowest_predicted_stay_total: float
    metrics: dict[str, Any]
    curve: list[HotelCurvePoint]


class MLDecisionResponse(BaseModel):
    route_id: str
    departure_date: date
    observed_date: date
    currency_code: str
    current_price: float
    days_to_departure: int
    model_stage: str
    is_synthetic_model: bool
    signal: str
    drop_probability: float | None = None
    expected_min_price_p10: float | None = None
    expected_min_price_p50: float | None = None
    expected_min_price_p90: float | None = None
    reason: str


class BookingCurvePoint(BaseModel):
    days_to_departure: int
    min_price_usd: float
    p25_price_usd: float
    median_price_usd: float
    p75_price_usd: float
    sample_count: int


class BookingCurveResponse(BaseModel):
    route_id: str
    source: str
    is_synthetic: bool
    currency: str = "USD"
    points: list[BookingCurvePoint]


class TripHeatmapCell(BaseModel):
    departure_date: date
    stay_nights: int
    flight_price_usd: float
    hotel_nightly_usd: float | None = None
    hotel_total_usd: float | None = None
    total_trip_usd: float
    hotel_match_type: str


class TripHeatmapResponse(BaseModel):
    route_id: str
    destination_iata: str
    currency: str = "USD"
    stay_nights_options: list[int]
    cells: list[TripHeatmapCell]


class ConversionWebhookRequest(BaseModel):
    sub_id: str = Field(..., min_length=4, max_length=64)
    booking_value_usd: Decimal = Field(..., gt=0)
    commission_usd: Decimal = Field(..., ge=0)
    status: str = Field(default="confirmed", pattern="^(pending|confirmed|cancelled)$")


class ConversionWebhookResponse(BaseModel):
    sub_id: str
    route_id: str
    partner: str
    conversion_status: str
    booking_value_usd: Decimal
    commission_usd: Decimal
    converted_at: datetime


class ClickEventSummary(BaseModel):
    sub_id: str
    route_id: str
    departure_date: date
    return_date: date | None = None
    partner: str
    quoted_price: Decimal | None = None
    quoted_currency: str | None = None
    source_tab: str
    clicked_at: datetime
    conversion_status: str
    booking_value_usd: Decimal | None = None
    commission_usd: Decimal | None = None


class AffiliateStatsResponse(BaseModel):
    total_clicks: int
    confirmed_conversions: int
    conversion_rate_pct: float
    total_booking_value_usd: float
    total_commission_usd: float
    rpm_usd: float
    recent_clicks: list[ClickEventSummary]


class PriceAlertCreate(BaseModel):
    route_id: str = Field(..., min_length=7, max_length=7)
    departure_date: date
    return_date: date | None = None
    target_price_usd: Decimal = Field(..., gt=0)
    channel: str = Field(default="all", pattern="^(all|slack|telegram|email)$")
    contact: str | None = Field(default=None, max_length=160)


class PriceAlertItem(BaseModel):
    alert_id: UUID
    route_id: str
    departure_date: date
    return_date: date | None = None
    target_price_usd: Decimal
    channel: str
    contact: str | None = None
    is_active: bool
    created_at: datetime
    last_checked_at: datetime | None = None
    last_triggered_at: datetime | None = None
    last_observed_price_usd: Decimal | None = None
    trigger_count: int


class AlertCheckSummary(BaseModel):
    checked: int
    triggered: int
    dispatched_alert_ids: list[str]
