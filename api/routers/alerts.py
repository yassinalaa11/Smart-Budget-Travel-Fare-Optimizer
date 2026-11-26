from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.dependencies import DatabasePool
from api.schemas import AlertCheckSummary, PriceAlertCreate, PriceAlertItem
from ingestion.alert_checker import check_price_alerts_with_pool

router = APIRouter(tags=["price alerts"])

ENSURE_ALERTS_SQL = """
    create table if not exists public.price_alerts (
        alert_id                uuid primary key default gen_random_uuid(),
        route_id                text not null,
        departure_date          date not null,
        return_date             date,
        target_price_usd        numeric(12, 2) not null,
        channel                 text not null default 'all',
        contact                 text,
        is_active               boolean not null default true,
        created_at              timestamptz not null default now(),
        last_checked_at         timestamptz,
        last_triggered_at       timestamptz,
        last_observed_price_usd numeric(12, 2),
        trigger_count           integer not null default 0
    )
"""


@router.post(
    "/alerts",
    response_model=PriceAlertItem,
    status_code=201,
    summary="Create a price-drop alert for a route and departure date",
)
async def create_price_alert(payload: PriceAlertCreate, db: DatabasePool) -> PriceAlertItem:
    normalized = payload.route_id.upper()
    parts = normalized.split("-")
    if len(parts) != 2 or len(parts[0]) != 3 or len(parts[1]) != 3:
        raise HTTPException(status_code=400, detail="route_id must use IATA format like CAI-DXB.")

    async with db.acquire() as conn:
        await conn.execute(ENSURE_ALERTS_SQL)
        row = await conn.fetchrow(
            """insert into public.price_alerts
               (route_id, departure_date, return_date, target_price_usd, channel, contact)
               values ($1, $2, $3, $4, $5, $6)
               returning alert_id, route_id, departure_date, return_date, target_price_usd,
                         channel, contact, is_active, created_at, last_checked_at,
                         last_triggered_at, last_observed_price_usd, trigger_count""",
            normalized,
            payload.departure_date,
            payload.return_date,
            payload.target_price_usd,
            payload.channel,
            payload.contact,
        )
    return PriceAlertItem.model_validate(dict(row))


@router.get(
    "/alerts",
    response_model=list[PriceAlertItem],
    summary="List configured price-drop alerts",
)
async def list_price_alerts(
    db: DatabasePool,
    active_only: bool = Query(default=True),
    route_id: str | None = None,
) -> list[PriceAlertItem]:
    normalized = route_id.upper() if route_id else None
    async with db.acquire() as conn:
        await conn.execute(ENSURE_ALERTS_SQL)
        rows = await conn.fetch(
            """select alert_id, route_id, departure_date, return_date, target_price_usd,
                      channel, contact, is_active, created_at, last_checked_at,
                      last_triggered_at, last_observed_price_usd, trigger_count
               from public.price_alerts
               where ($1::boolean = false or is_active = true)
                 and ($2::text is null or route_id = $2)
               order by created_at desc""",
            active_only,
            normalized,
        )
    return [PriceAlertItem.model_validate(dict(r)) for r in rows]


@router.delete(
    "/alerts/{alert_id}",
    summary="Deactivate a price-drop alert",
)
async def deactivate_price_alert(alert_id: UUID, db: DatabasePool) -> dict[str, str]:
    async with db.acquire() as conn:
        await conn.execute(ENSURE_ALERTS_SQL)
        updated = await conn.fetchval(
            """update public.price_alerts
               set is_active = false
               where alert_id = $1
               returning alert_id""",
            alert_id,
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"status": "deactivated", "alert_id": str(alert_id)}


@router.post(
    "/alerts/check",
    response_model=AlertCheckSummary,
    summary="Evaluate all active price-drop alerts against latest warehouse fares",
)
async def run_alert_check_now(db: DatabasePool) -> AlertCheckSummary:
    summary = await check_price_alerts_with_pool(db)
    return AlertCheckSummary.model_validate(summary)
