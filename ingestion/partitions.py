from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone

import asyncpg


def _add_months(anchor: date, offset: int) -> date:
    month_index = (anchor.year * 12 + (anchor.month - 1)) + offset
    year, month_zero = divmod(month_index, 12)
    return date(year, month_zero + 1, 1)


async def ensure_monthly_partitions(*, months_ahead: int = 3) -> list[str]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running partition maintenance")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    today = datetime.now(timezone.utc).date()
    created: list[str] = []

    conn = await asyncpg.connect(database_url)
    try:
        for offset in range(0, months_ahead + 1):
            start = _add_months(today, offset)
            end = _add_months(today, offset + 1)
            table_name = f"api_responses_y{start.year:04d}m{start.month:02d}"

            exists = await conn.fetchval(
                """select 1
                   from pg_class c
                   join pg_namespace n on n.oid = c.relnamespace
                   where n.nspname = 'raw' and c.relname = $1""",
                table_name,
            )
            if exists:
                continue

            start_lit = start.isoformat()
            end_lit = end.isoformat()

            async with conn.transaction():
                default_rows = await conn.fetchval(
                    """select count(*) from raw.api_responses_default
                       where ingested_date >= $1 and ingested_date < $2""",
                    start,
                    end,
                )
                if default_rows:
                    await conn.execute(
                        f"""create temp table _moved_raw_responses on commit drop as
                            select * from raw.api_responses_default
                            where ingested_date >= date '{start_lit}'
                              and ingested_date < date '{end_lit}'"""
                    )
                    await conn.execute(
                        f"""delete from raw.api_responses_default
                            where ingested_date >= date '{start_lit}'
                              and ingested_date < date '{end_lit}'"""
                    )

                await conn.execute(
                    f"""create table raw.{table_name}
                        partition of raw.api_responses
                        for values from ('{start_lit}') to ('{end_lit}')"""
                )

                if default_rows:
                    await conn.execute(
                        """insert into raw.api_responses
                           (id, provider, endpoint, request_params, response_status,
                            payload, payload_hash, ingested_at, ingested_date, dag_run_id)
                           select id, provider, endpoint, request_params, response_status,
                                  payload, payload_hash, ingested_at, ingested_date, dag_run_id
                           from _moved_raw_responses"""
                    )

            created.append(f"raw.{table_name}")
    finally:
        await conn.close()

    return created


async def main() -> None:
    created = await ensure_monthly_partitions(months_ahead=3)
    if created:
        print("Created monthly partitions:", ", ".join(created))
    else:
        print("All monthly partitions already exist.")


if __name__ == "__main__":
    asyncio.run(main())
