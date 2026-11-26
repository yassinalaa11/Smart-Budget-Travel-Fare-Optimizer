from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from api.dependencies import DatabasePool
from api.schemas import (
    AffiliateStatsResponse,
    ClickEventSummary,
    ConversionWebhookRequest,
    ConversionWebhookResponse,
)

router = APIRouter(tags=["affiliate"])

ENSURE_CLICK_EVENTS_SQL = """
    create table if not exists public.click_events (
        click_id            uuid primary key default gen_random_uuid(),
        sub_id              text not null unique,
        route_id            text not null,
        origin_iata         char(3) not null,
        destination_iata    char(3) not null,
        departure_date      date not null,
        return_date         date,
        partner             text not null default 'aviasales',
        target_url          text not null,
        quoted_price        numeric(12, 2),
        quoted_currency     char(3),
        source_tab          text not null default 'booking_advisor',
        clicked_at          timestamptz not null default now(),
        converted_at        timestamptz,
        booking_value_usd   numeric(12, 2),
        commission_usd      numeric(12, 2),
        conversion_status   text not null default 'clicked'
    )
"""


def build_partner_deep_link(
    *,
    origin_iata: str,
    destination_iata: str,
    departure_date: date,
    return_date: date | None,
    partner: str,
    sub_id: str,
) -> str:
    orig = origin_iata.upper()
    dest = destination_iata.upper()
    marker = os.getenv("TRAVELPAYOUTS_MARKER", "smartfare_demo").strip() or "smartfare_demo"

    if partner == "aviasales":
        dep_code = departure_date.strftime("%d%m")
        ret_code = return_date.strftime("%d%m") if return_date else ""
        query = urlencode({"marker": marker, "sub_id": sub_id})
        return f"https://www.aviasales.com/search/{orig}{dep_code}{dest}{ret_code}1?{query}"

    if partner == "skyscanner":
        dep_code = departure_date.strftime("%y%m%d")
        ret_segment = f"/{return_date.strftime('%y%m%d')}" if return_date else ""
        query = urlencode({"ref": "smartfare", "sub_id": sub_id})
        return f"https://www.skyscanner.com/transport/flights/{orig.lower()}/{dest.lower()}/{dep_code}{ret_segment}/?{query}"

    trip_desc = f"Flights to {dest} from {orig} on {departure_date.isoformat()}"
    if return_date:
        trip_desc += f" returning {return_date.isoformat()}"
    query = urlencode({"q": trip_desc, "utm_source": "smartfare", "sub_id": sub_id})
    return f"https://www.google.com/travel/flights?{query}"


@router.get(
    "/redirect/book",
    summary="Log an outbound affiliate click with a unique sub_id and redirect to the booking partner",
)
async def redirect_to_booking_partner(
    route_id: str,
    departure_date: date,
    db: DatabasePool,
    return_date: date | None = None,
    partner: str = Query(default="aviasales", pattern="^(aviasales|google_flights|skyscanner)$"),
    quoted_price: Decimal | None = None,
    quoted_currency: str | None = Query(default=None, min_length=3, max_length=3),
    source_tab: str = Query(default="booking_advisor", max_length=64),
) -> RedirectResponse:
    normalized = route_id.upper()
    parts = normalized.split("-")
    if len(parts) != 2 or len(parts[0]) != 3 or len(parts[1]) != 3:
        raise HTTPException(status_code=400, detail="route_id must use IATA format like CAI-DXB.")

    origin_iata, destination_iata = parts[0], parts[1]
    sub_id = f"sf_{uuid4().hex[:12]}"
    target_url = build_partner_deep_link(
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        departure_date=departure_date,
        return_date=return_date,
        partner=partner,
        sub_id=sub_id,
    )

    async with db.acquire() as conn:
        await conn.execute(ENSURE_CLICK_EVENTS_SQL)
        await conn.execute(
            """insert into public.click_events
               (sub_id, route_id, origin_iata, destination_iata, departure_date, return_date,
                partner, target_url, quoted_price, quoted_currency, source_tab)
               values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            sub_id,
            normalized,
            origin_iata,
            destination_iata,
            departure_date,
            return_date,
            partner,
            target_url,
            quoted_price,
            quoted_currency.upper() if quoted_currency else None,
            source_tab,
        )

    return RedirectResponse(
        url=target_url,
        status_code=302,
        headers={"X-SmartFare-Sub-Id": sub_id},
    )


@router.post(
    "/affiliate/conversions",
    response_model=ConversionWebhookResponse,
    summary="Attribute an affiliate partner conversion back to a tracked click via sub_id",
)
async def record_affiliate_conversion(
    payload: ConversionWebhookRequest,
    db: DatabasePool,
) -> ConversionWebhookResponse:
    now_utc = datetime.now(timezone.utc)
    async with db.acquire() as conn:
        await conn.execute(ENSURE_CLICK_EVENTS_SQL)
        row = await conn.fetchrow(
            """update public.click_events
               set conversion_status = $2,
                   booking_value_usd = $3,
                   commission_usd = $4,
                   converted_at = $5
               where sub_id = $1
               returning sub_id, route_id, partner, conversion_status,
                         booking_value_usd, commission_usd, converted_at""",
            payload.sub_id,
            payload.status,
            payload.booking_value_usd,
            payload.commission_usd,
            now_utc,
        )
    if not row:
        raise HTTPException(status_code=404, detail=f"No click event found for sub_id={payload.sub_id}")
    return ConversionWebhookResponse.model_validate(dict(row))


@router.get(
    "/affiliate/stats",
    response_model=AffiliateStatsResponse,
    summary="Return affiliate click volume, confirmed conversions, commission USD, and RPM",
)
async def get_affiliate_stats(
    db: DatabasePool,
    limit: int = Query(default=25, ge=1, le=200),
) -> AffiliateStatsResponse:
    async with db.acquire() as conn:
        await conn.execute(ENSURE_CLICK_EVENTS_SQL)
        agg = await conn.fetchrow(
            """select
                   count(*)::integer as total_clicks,
                   count(*) filter (where conversion_status = 'confirmed')::integer as confirmed_conversions,
                   coalesce(sum(booking_value_usd) filter (where conversion_status = 'confirmed'), 0)::float as total_booking_value_usd,
                   coalesce(sum(commission_usd) filter (where conversion_status = 'confirmed'), 0)::float as total_commission_usd
               from public.click_events"""
        )
        recent = await conn.fetch(
            """select sub_id, route_id, departure_date, return_date, partner,
                      quoted_price, quoted_currency, source_tab, clicked_at,
                      conversion_status, booking_value_usd, commission_usd
               from public.click_events
               order by clicked_at desc
               limit $1""",
            limit,
        )

    total_clicks = int(agg["total_clicks"] or 0)
    confirmed = int(agg["confirmed_conversions"] or 0)
    total_commission = round(float(agg["total_commission_usd"] or 0.0), 2)
    total_booking = round(float(agg["total_booking_value_usd"] or 0.0), 2)
    conv_rate = round((confirmed / total_clicks) * 100.0, 2) if total_clicks > 0 else 0.0
    rpm = round((total_commission / total_clicks) * 1000.0, 2) if total_clicks > 0 else 0.0

    return AffiliateStatsResponse(
        total_clicks=total_clicks,
        confirmed_conversions=confirmed,
        conversion_rate_pct=conv_rate,
        total_booking_value_usd=total_booking,
        total_commission_usd=total_commission,
        rpm_usd=rpm,
        recent_clicks=[ClickEventSummary.model_validate(dict(r)) for r in recent],
    )
