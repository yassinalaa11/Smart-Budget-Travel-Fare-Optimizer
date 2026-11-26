from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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