from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PriceHistoryPoint(BaseModel):
    observed_date: date
    departure_date: date
    return_date: date
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
    return_date: date
