from datetime import date
from decimal import Decimal

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from api.dependencies import DatabasePool
from api.schemas import (
    CheapestWindowItem,
    CheapestWindowsResponse,
    RecommendationResponse,
)

router = APIRouter(tags=["recommendations"])


@router.get(
    "/routes/{route_id}/recommendation",
    response_model=RecommendationResponse,
    summary="Get Book Now / Wait / Neutral recommendation (auto-promotes to ML when backtest gate passes)",
)
async def get_recommendation(
    route_id: str,
    departure_date: date,
    request: Request,
    db: DatabasePool,
    return_date: date | None = None,
) -> RecommendationResponse:
    normalized_route_id = route_id.upper()
    latest_query = """
        select observed_date, min_total_price, min_total_price_usd, currency_code, contains_demo_data
        from analytics_marts.fct_flight_price_observations
        where route_id = $1
          and departure_date = $2
          and return_date is not distinct from $3::date
        order by observed_date desc
        limit 1
    """
    stats_query = """
        select
            count(distinct observed_date)::integer as observation_days,
            percentile_cont(0.20) within group (order by min_total_price)::numeric(12, 2) as p20,
            percentile_cont(0.50) within group (order by min_total_price)::numeric(12, 2) as p50,
            percentile_cont(0.80) within group (order by min_total_price)::numeric(12, 2) as p80,
            count(*) filter (where min_total_price <= $3)::float / nullif(count(*), 0) as pct_rank
        from analytics_marts.fct_flight_price_observations
        where route_id = $1
          and currency_code = $2
    """
    try:
        async with db.acquire() as conn:
            latest = await conn.fetchrow(latest_query, normalized_route_id, departure_date, return_date)
            if not latest:
                raise HTTPException(status_code=404, detail="No price observations found for this route/date.")
            stats = await conn.fetchrow(
                stats_query,
                normalized_route_id,
                latest["currency_code"],
                latest["min_total_price"],
            )
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Warehouse tables are not built yet. Run dbt first.") from exc

    current_price: Decimal = latest["min_total_price"]
    observed_date: date = latest["observed_date"]
    days_to_departure = (departure_date - observed_date).days
    pct_rank = round(float(stats["pct_rank"] or 0.5), 2)
    obs_days = int(stats["observation_days"] or 1)

    model_stage = "phase_1_statistical_baseline"
    drop_prob: float | None = None
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None

    registry = getattr(request.app.state, "model_registry", None)
    ml_pred = None
    if registry is not None and registry.is_live_route_promoted(normalized_route_id):
        norm_price = float(latest["min_total_price_usd"] or current_price)
        ml_pred = registry.predict_decision(
            route_id=normalized_route_id,
            observed_date=observed_date,
            departure_date=departure_date,
            current_price=norm_price,
            allow_synthetic_prototype=False,
        )

    if ml_pred is not None:
        model_stage = ml_pred["model_stage"]
        drop_prob = ml_pred["drop_probability"]
        p10 = ml_pred["expected_min_price_p10"]
        p50 = ml_pred["expected_min_price_p50"]
        p90 = ml_pred["expected_min_price_p90"]
        if days_to_departure <= 14 or drop_prob < 0.30:
            signal = "BOOK_NOW"
            reason = (
                f"Promoted ML model estimates {drop_prob * 100:.1f}% chance of a >=7% drop "
                f"(expected USD {p10:.0f}-{p90:.0f})."
            )
        elif drop_prob >= 0.55:
            signal = "WAIT"
            reason = (
                f"Promoted ML model estimates {drop_prob * 100:.1f}% chance of a >=7% drop "
                f"(median expected floor USD {p50:.0f})."
            )
        else:
            signal = "NEUTRAL"
            reason = (
                f"Promoted ML model shows balanced outlook ({drop_prob * 100:.1f}% drop probability; "
                f"expected USD {p10:.0f}-{p90:.0f})."
            )
    elif days_to_departure <= 14:
        signal = "BOOK_NOW"
        reason = (
            f"Departure is in {days_to_departure} days (inside the 14-day advance-purchase cliff). "
            "Fares typically rise sharply from here."
        )
    elif current_price <= stats["p20"]:
        signal = "BOOK_NOW"
        reason = (
            f"Current fare ({latest['currency_code']} {current_price}) is in the bottom 20% "
            f"of observed prices for {normalized_route_id}."
        )
    elif current_price >= stats["p80"] and days_to_departure > 21:
        signal = "WAIT"
        reason = (
            f"Current fare is above the 80th percentile ({latest['currency_code']} {stats['p80']}) "
            f"with {days_to_departure} days left before departure."
        )
    else:
        signal = "NEUTRAL"
        reason = (
            f"Current fare sits near the route median ({latest['currency_code']} {stats['p50']}). "
            "Lock in if your dates are fixed, or monitor for a dip."
        )

    confidence = "high" if obs_days >= 14 else ("medium" if obs_days >= 5 else "low (cold-start baseline)")

    return RecommendationResponse(
        route_id=normalized_route_id,
        departure_date=departure_date,
        return_date=return_date,
        currency_code=latest["currency_code"],
        current_price=current_price,
        route_p20_price=stats["p20"],
        route_median_price=stats["p50"],
        route_p80_price=stats["p80"],
        percentile_rank=pct_rank,
        days_to_departure=days_to_departure,
        observation_days=obs_days,
        signal=signal,
        confidence=confidence,
        reason=reason,
        contains_demo_data=bool(latest["contains_demo_data"]),
        model_stage=model_stage,
        drop_probability=drop_prob,
        expected_min_price_p10=p10,
        expected_min_price_p50=p50,
        expected_min_price_p90=p90,
    )


@router.get(
    "/routes/{route_id}/cheapest-windows",
    response_model=CheapestWindowsResponse,
    summary="Rank lowest-cost travel windows combining flights and hotels (FX-normalized to USD)",
)
async def get_cheapest_windows(
    route_id: str,
    db: DatabasePool,
    limit: int = Query(default=20, ge=1, le=100),
) -> CheapestWindowsResponse:
    normalized_route_id = route_id.upper()
    parts = normalized_route_id.split("-")
    destination_iata = parts[1] if len(parts) == 2 else ""

    query = """
        with latest_flights as (
            select distinct on (departure_date, return_date)
                departure_date,
                return_date,
                min_total_price as flight_price,
                currency_code as flight_currency,
                min_total_price_usd as flight_price_usd,
                contains_demo_data
            from analytics_marts.fct_flight_price_observations
            where route_id = $1
            order by departure_date, return_date, observed_date desc
        ), hotel_by_window as (
            select distinct on (checkin_date, checkout_date)
                checkin_date,
                checkout_date,
                currency_code as hotel_currency,
                percentile_cont(0.5) within group (order by min_total_price)::numeric(12, 2) as hotel_total_price,
                percentile_cont(0.5) within group (order by min_total_price_usd)::numeric(12, 2) as hotel_total_price_usd
            from analytics_marts.fct_hotel_price_observations
            where city_iata = $2
            group by checkin_date, checkout_date, currency_code
            order by checkin_date, checkout_date, count(*) desc, currency_code
        ), hotel_by_checkin as (
            select distinct on (checkin_date)
                checkin_date,
                currency_code as hotel_currency,
                percentile_cont(0.5) within group (order by min_total_price)::numeric(12, 2) as hotel_total_price,
                percentile_cont(0.5) within group (order by min_total_price_usd)::numeric(12, 2) as hotel_total_price_usd
            from analytics_marts.fct_hotel_price_observations
            where city_iata = $2
            group by checkin_date, currency_code
            order by checkin_date, count(*) desc, currency_code
        ), hotel_city_fallback as (
            select
                currency_code as hotel_currency,
                percentile_cont(0.5) within group (order by min_total_price)::numeric(12, 2) as hotel_total_price,
                percentile_cont(0.5) within group (order by min_total_price_usd)::numeric(12, 2) as hotel_total_price_usd
            from analytics_marts.fct_hotel_price_observations
            where city_iata = $2
            group by currency_code
            order by count(*) desc, currency_code
            limit 1
        ), joined as (
            select
                f.departure_date,
                f.return_date,
                f.flight_price,
                f.flight_currency,
                f.flight_price_usd,
                coalesce(hw.hotel_total_price, hc.hotel_total_price, hf.hotel_total_price) as hotel_total_price,
                coalesce(hw.hotel_currency, hc.hotel_currency, hf.hotel_currency) as hotel_currency,
                coalesce(hw.hotel_total_price_usd, hc.hotel_total_price_usd, hf.hotel_total_price_usd) as hotel_total_price_usd,
                case
                    when hw.hotel_total_price is not null then 'exact_dates'
                    when hc.hotel_total_price is not null then 'checkin_date'
                    when hf.hotel_total_price is not null then 'city_median_fallback'
                    else 'none'
                end as hotel_match_type,
                f.contains_demo_data
            from latest_flights as f
            left join hotel_by_window as hw
              on hw.checkin_date = f.departure_date
             and hw.checkout_date = f.return_date
            left join hotel_by_checkin as hc
              on hw.hotel_total_price is null
             and hc.checkin_date = f.departure_date
            left join hotel_city_fallback as hf
              on hw.hotel_total_price is null
             and hc.hotel_total_price is null
        )
        select
            departure_date,
            return_date,
            flight_price,
            flight_currency,
            flight_price_usd,
            hotel_total_price,
            hotel_currency,
            hotel_total_price_usd,
            case
                when hotel_total_price is not null and flight_currency = hotel_currency
                    then flight_price + hotel_total_price
                else null
            end as combined_total_price,
            case
                when flight_price_usd is not null and hotel_total_price_usd is not null
                    then flight_price_usd + hotel_total_price_usd
                else null
            end as combined_total_price_usd,
            hotel_match_type,
            contains_demo_data
        from joined
        order by coalesce(
            case
                when flight_price_usd is not null and hotel_total_price_usd is not null
                    then flight_price_usd + hotel_total_price_usd
            end,
            flight_price_usd,
            flight_price
        ) asc
        limit $3
    """
    try:
        async with db.acquire() as conn:
            records = await conn.fetch(query, normalized_route_id, destination_iata, limit)
    except asyncpg.UndefinedTableError as exc:
        raise HTTPException(status_code=503, detail="Warehouse tables are not built yet. Run dbt first.") from exc

    windows = [CheapestWindowItem.model_validate(dict(r)) for r in records]
    return CheapestWindowsResponse(
        route_id=normalized_route_id,
        destination_iata=destination_iata,
        windows=windows,
    )
