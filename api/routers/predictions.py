from __future__ import annotations

from datetime import date

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from api.dependencies import DatabasePool
from api.schemas import (
    FlightCurveOptionsResponse,
    FlightCurvePredictionResponse,
    HotelCatalogItem,
    HotelCurvePredictionResponse,
    MLDecisionResponse,
    ModelStatusResponse,
)
from ml.registry import ModelRegistry

router = APIRouter(tags=["ml predictions"])


def _registry(request: Request) -> ModelRegistry:
    return request.app.state.model_registry


@router.get("/models/status", response_model=ModelStatusResponse, summary="Inspect loaded ML artifacts and walk-forward backtest scorecards")
async def get_model_status(request: Request) -> ModelStatusResponse:
    return ModelStatusResponse.model_validate(_registry(request).status())


@router.post("/models/reload", response_model=ModelStatusResponse, summary="Reload serialized ML artifacts from disk")
async def reload_models(request: Request) -> ModelStatusResponse:
    return ModelStatusResponse.model_validate(_registry(request).reload())


@router.get("/predict/flight/options", response_model=FlightCurveOptionsResponse, summary="List routes available in the synthetic flight curve model")
async def get_flight_prediction_options(request: Request) -> FlightCurveOptionsResponse:
    options = _registry(request).flight_curve_options()
    if not options["routes"]:
        raise HTTPException(
            status_code=503,
            detail="Flight model artifacts not found. Run: docker compose exec ingestion-runner python -m ml.train",
        )
    return FlightCurveOptionsResponse.model_validate(options)


@router.get("/predict/flight", response_model=FlightCurvePredictionResponse, summary="Predict flight lead-time price curve via serialized model")
async def predict_flight(
    request: Request,
    route_id: str,
    departure_month: int = Query(default=1, ge=1, le=12),
    departure_weekday: int = Query(default=4, ge=0, le=6),
) -> FlightCurvePredictionResponse:
    try:
        result = _registry(request).predict_flight_curve(
            route_id=route_id,
            departure_month=departure_month,
            departure_weekday=departure_weekday,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Flight model artifacts not found. Run: docker compose exec ingestion-runner python -m ml.train",
        ) from exc
    return FlightCurvePredictionResponse.model_validate(result)


@router.get("/predict/hotel/options", response_model=list[HotelCatalogItem], summary="List hotels available in the synthetic hotel curve model")
async def get_hotel_prediction_options(request: Request) -> list[HotelCatalogItem]:
    hotels = _registry(request).hotel_curve_options()
    if not hotels:
        raise HTTPException(
            status_code=503,
            detail="Hotel model artifacts not found. Run: docker compose exec ingestion-runner python -m ml.train",
        )
    return [HotelCatalogItem.model_validate(h) for h in hotels]


@router.get("/predict/hotel", response_model=HotelCurvePredictionResponse, summary="Predict hotel nightly and stay-total lead-time curve via serialized model")
async def predict_hotel(
    request: Request,
    hotel_id: str,
    checkin_month: int = Query(default=1, ge=1, le=12),
    checkin_weekday: int = Query(default=4, ge=0, le=6),
    nights: int = Query(default=3, ge=1, le=30),
    adults: int = Query(default=2, ge=1, le=8),
) -> HotelCurvePredictionResponse:
    try:
        result = _registry(request).predict_hotel_curve(
            hotel_id=hotel_id,
            checkin_month=checkin_month,
            checkin_weekday=checkin_weekday,
            nights=nights,
            adults=adults,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Hotel model artifacts not found. Run: docker compose exec ingestion-runner python -m ml.train",
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return HotelCurvePredictionResponse.model_validate(result)


@router.get("/routes/{route_id}/ml-decision", response_model=MLDecisionResponse, summary="Run classifier and quantile regressor for Book Now vs Wait decision")
async def get_ml_decision(
    route_id: str,
    departure_date: date,
    request: Request,
    db: DatabasePool,
    allow_synthetic_prototype: bool = Query(default=False),
) -> MLDecisionResponse:
    normalized = route_id.upper()
    query = """
        select
            observed_date,
            departure_date,
            currency_code,
            coalesce(min_total_price_usd, min_total_price)::float as normalized_price,
            min_total_price::float as raw_price,
            days_to_departure,
            price_velocity_3d::float as price_velocity_3d,
            price_velocity_7d::float as price_velocity_7d,
            z_score_vs_route_horizon::float as z_score_vs_route_horizon,
            markup_over_historical_min::float as markup_over_historical_min,
            drawdown_from_peak::float as drawdown_from_peak
        from analytics_marts.ml_features_flight_pricing
        where route_id = $1
          and departure_date = $2
        order by observed_date desc
        limit 1
    """
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(query, normalized, departure_date)
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Run dbt models first.") from exc

    if not row:
        raise HTTPException(status_code=404, detail="No feature row found for this route and departure date.")

    current_price = float(row["normalized_price"])
    currency = "USD" if row["normalized_price"] != row["raw_price"] else row["currency_code"]
    days_left = int(row["days_to_departure"])

    prediction = _registry(request).predict_decision(
        route_id=normalized,
        observed_date=row["observed_date"],
        departure_date=row["departure_date"],
        current_price=current_price,
        price_velocity_3d=row["price_velocity_3d"],
        price_velocity_7d=row["price_velocity_7d"],
        z_score_vs_route_horizon=row["z_score_vs_route_horizon"],
        markup_over_historical_min=row["markup_over_historical_min"],
        drawdown_from_peak=row["drawdown_from_peak"],
        allow_synthetic_prototype=allow_synthetic_prototype,
    )

    if prediction is None:
        return MLDecisionResponse(
            route_id=normalized,
            departure_date=row["departure_date"],
            observed_date=row["observed_date"],
            currency_code=currency,
            current_price=round(current_price, 2),
            days_to_departure=days_left,
            model_stage="phase_1_statistical_baseline",
            is_synthetic_model=False,
            signal="BOOK_NOW" if days_left <= 14 else "NEUTRAL",
            reason=(
                "Route has not yet passed the walk-forward promotion gate; "
                "using Phase 1 statistical baseline. Pass allow_synthetic_prototype=true to test the synthetic ML prototype."
            ),
        )

    drop_prob = prediction["drop_probability"]
    if days_left <= 14 or drop_prob < 0.30:
        signal = "BOOK_NOW"
        reason = (
            f"Model estimates a {drop_prob * 100:.1f}% chance of a >=7% fare drop in the next 14 days "
            f"(expected range {currency} {prediction['expected_min_price_p10']:.0f}-{prediction['expected_min_price_p90']:.0f})."
        )
    elif drop_prob >= 0.55:
        signal = "WAIT"
        reason = (
            f"Model estimates a {drop_prob * 100:.1f}% probability of a >=7% fare drop "
            f"(median expected floor {currency} {prediction['expected_min_price_p50']:.0f})."
        )
    else:
        signal = "NEUTRAL"
        reason = (
            f"Balanced outlook ({drop_prob * 100:.1f}% drop probability; "
            f"expected range {currency} {prediction['expected_min_price_p10']:.0f}-{prediction['expected_min_price_p90']:.0f})."
        )

    return MLDecisionResponse(
        route_id=normalized,
        departure_date=row["departure_date"],
        observed_date=row["observed_date"],
        currency_code=currency,
        current_price=round(current_price, 2),
        days_to_departure=days_left,
        model_stage=prediction["model_stage"],
        is_synthetic_model=prediction["is_synthetic_model"],
        signal=signal,
        drop_probability=drop_prob,
        expected_min_price_p10=prediction["expected_min_price_p10"],
        expected_min_price_p50=prediction["expected_min_price_p50"],
        expected_min_price_p90=prediction["expected_min_price_p90"],
        reason=reason,
    )
