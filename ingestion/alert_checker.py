from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import asyncpg

from ingestion.alerts import send_alert


async def check_price_alerts_with_pool(pool: asyncpg.Pool) -> dict[str, Any]:
    public_api_url = os.getenv("SMARTFARE_PUBLIC_API_URL", "http://localhost:8000").rstrip("/")
    now_utc = datetime.now(timezone.utc)
    cooldown_cutoff = now_utc - timedelta(hours=24)

    checked = 0
    triggered = 0
    dispatched_ids: list[str] = []

    async with pool.acquire() as conn:
        alerts = await conn.fetch(
            """select alert_id, route_id, departure_date, return_date,
                      target_price_usd, channel, contact, last_triggered_at
               from public.price_alerts
               where is_active = true
                 and departure_date >= current_date
               order by created_at asc"""
        )

        for alert in alerts:
            checked += 1
            try:
                latest = await conn.fetchrow(
                    """select observed_date,
                              coalesce(min_total_price_usd, min_total_price)::numeric(12, 2) as observed_price_usd,
                              min_total_price,
                              currency_code
                       from analytics_marts.fct_flight_price_observations
                       where route_id = $1
                         and departure_date = $2
                         and return_date is not distinct from $3::date
                       order by observed_date desc, coalesce(min_total_price_usd, min_total_price) asc
                       limit 1""",
                    alert["route_id"],
                    alert["departure_date"],
                    alert["return_date"],
                )
            except asyncpg.UndefinedTableError:
                break

            if not latest or latest["observed_price_usd"] is None:
                await conn.execute(
                    "update public.price_alerts set last_checked_at = $2 where alert_id = $1",
                    alert["alert_id"],
                    now_utc,
                )
                continue

            observed_usd: Decimal = latest["observed_price_usd"]
            target_usd: Decimal = alert["target_price_usd"]

            await conn.execute(
                """update public.price_alerts
                   set last_checked_at = $2,
                       last_observed_price_usd = $3
                   where alert_id = $1""",
                alert["alert_id"],
                now_utc,
                observed_usd,
            )

            recently_sent = (
                alert["last_triggered_at"] is not None
                and alert["last_triggered_at"] >= cooldown_cutoff
            )
            if observed_usd <= target_usd and not recently_sent:
                params: dict[str, str] = {
                    "route_id": alert["route_id"],
                    "departure_date": alert["departure_date"].isoformat(),
                    "partner": "aviasales",
                    "quoted_price": str(observed_usd),
                    "quoted_currency": "USD",
                    "source_tab": "price_alert",
                }
                if alert["return_date"]:
                    params["return_date"] = alert["return_date"].isoformat()
                book_link = f"{public_api_url}/api/v1/redirect/book?{urlencode(params)}"

                savings = target_usd - observed_usd
                body = "\n".join([
                    f"Route: {alert['route_id']} | Depart: {alert['departure_date']}",
                    f"Observed: USD {observed_usd:.2f} (Target: USD {target_usd:.2f}, under by USD {savings:.2f})",
                    f"Book via SmartFare: {book_link}",
                ])
                await send_alert(
                    f"Price Drop Alert: {alert['route_id']} (${observed_usd:.2f})",
                    body,
                )
                await conn.execute(
                    """update public.price_alerts
                       set last_triggered_at = $2,
                           trigger_count = trigger_count + 1
                       where alert_id = $1""",
                    alert["alert_id"],
                    now_utc,
                )
                triggered += 1
                dispatched_ids.append(str(alert["alert_id"]))

    return {
        "checked": checked,
        "triggered": triggered,
        "dispatched_alert_ids": dispatched_ids,
    }


async def run_price_alert_check() -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running the price alert checker")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
    try:
        return await check_price_alerts_with_pool(pool)
    finally:
        await pool.close()


async def main() -> None:
    result = await run_price_alert_check()
    print(f"Price alerts checked={result['checked']} triggered={result['triggered']}")


if __name__ == "__main__":
    asyncio.run(main())
