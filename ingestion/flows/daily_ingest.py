from __future__ import annotations

import asyncio

from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run
from prefect.server.schemas.schedules import CronSchedule

from ingestion.alerts import send_alert
from ingestion.collector import run_collection
from ingestion.fx_rates import run_fx_collection
from ingestion.hotel_collector import run_hotel_collection
from ingestion.partitions import ensure_monthly_partitions


async def _run_dbt_command(subcommand: str) -> None:
    logger = get_run_logger()
    process = await asyncio.create_subprocess_exec(
        "dbt",
        subcommand,
        "--project-dir",
        "/app/transform",
        "--profiles-dir",
        "/app/transform",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    log_output = output.decode("utf-8", errors="replace").strip()
    if log_output:
        logger.info("dbt %s output:\n%s", subcommand, log_output)
    if process.returncode != 0:
        raise RuntimeError(f"dbt {subcommand} failed with exit code {process.returncode}")


@task(name="maintain-raw-partitions")
async def maintain_partitions() -> list[str]:
    return await ensure_monthly_partitions(months_ahead=3)


@task(name="ingest-daily-fx-rates", retries=2, retry_delay_seconds=15)
async def ingest_fx_rates(run_id: str) -> int:
    return await run_fx_collection(dag_run_id=run_id)


@task(name="collect-flight-prices")
async def collect_flights(run_id: str) -> tuple[int, int]:
    return await run_collection(dag_run_id=run_id)


@task(name="collect-hotel-prices")
async def collect_hotels(run_id: str) -> tuple[int, int]:
    return await run_hotel_collection(dag_run_id=run_id)


@task(name="build-silver-and-gold-warehouse")
async def build_warehouse() -> None:
    await _run_dbt_command("run")


@task(name="test-warehouse-quality")
async def test_warehouse() -> None:
    await _run_dbt_command("test")


@flow(name="smartfare-daily-ingest")
async def daily_ingest() -> dict[str, int | str]:
    logger = get_run_logger()
    run_id = str(flow_run.id)

    try:
        created_parts = await maintain_partitions()
        if created_parts:
            logger.info("Created partitions: %s", ", ".join(created_parts))

        fx_count = await ingest_fx_rates(run_id)
        logger.info("FX rates stored: %s currencies", fx_count)

        flight_ok, flight_fail = await collect_flights(run_id)
        logger.info("Flights collected: %s succeeded, %s failed", flight_ok, flight_fail)

        hotel_ok, hotel_fail = await collect_hotels(run_id)
        logger.info("Hotels collected: %s succeeded, %s failed", hotel_ok, hotel_fail)

        await build_warehouse()
        await test_warehouse()

        if flight_fail or hotel_fail:
            raise RuntimeError(
                f"Partial provider failures: flights_failed={flight_fail}, hotels_failed={hotel_fail}"
            )

        return {
            "fx_rates": fx_count,
            "flights_succeeded": flight_ok,
            "hotels_succeeded": hotel_ok,
            "warehouse": "built_and_tested",
        }
    except Exception as exc:
        await send_alert(
            "smartfare-daily-ingest failed",
            f"Run ID: {run_id}\nError: {exc}",
        )
        raise


if __name__ == "__main__":
    daily_ingest.serve(
        name="smartfare-daily-ingest",
        schedule=CronSchedule(cron="0 2 * * *", timezone="UTC"),
        pause_on_shutdown=False,
        limit=1,
        tags=["ingestion", "smartfare"],
    )
